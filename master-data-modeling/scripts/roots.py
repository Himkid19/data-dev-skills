#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
词根查询与字段翻译工具（MPC 主数据建模的确定性命名底座）。

词根是"精确查找"型数据，禁止靠模型记忆或现场臆造缩写——一律走本脚本。
词根字典唯一数据源 = MySQL 表 md_word_roots（上游词根平台，roots.py sync 同步），
不维护本地 md 快照；空表时用 `sync --apply` 首次引导。
中文名 → 英文字段名的翻译优先级：
  固定搭配（fixed_collocations.csv，最高）→ 金融缩写白名单（fin_abbr_whitelist.csv）
  → 字段翻译缓存（md_field_cache，按 cache_type 优先级）→ 词根拼接（md_word_roots）。

种子/词表文件缺失时自动回退同名 `.example` 文件（开源仓库只带脱敏示例，
真实词表由团队在本地维护、不入库）。

子命令：
  sync [--apply]            从词根平台拉最新词根，输出新增/变更/删除差异报告；
                            --apply 写入 md_word_roots（空表首次引导也用它）
  lookup <中文词...>        批量查词根（空格分隔多个词，单进程单连接一次查完；
                            每词独立：精确 → 包含 → 模糊 → 英文术语回查 en-match）
  translate [--model M]     批量翻译：stdin 或 --file 传中文字段名列表（每行一个）；
                            输出含 词根构成 列；>5 个词根段的字段标记需用户审批；
                            --model 仅作输出标注，使用明细在确认入缓存时写入
  en2zh --file f.csv [--kind field|table] [--out f.tsv]
                            反向翻译：源表名/字段名（英文）→ 中文名（词根反查 + 别名裁决）；
                            表名开头内部标识（ods_/dwd_/dim_… 等非词根段）不译，结尾 _ext→拓展表，
                            未命中词根的行中文列留「无法翻译」；--self-check 跑最小自检
  usage-sync-cache [--model M1,M2]
                            把 md_field_cache 的字段名反解为词根明细，回填 md_root_usage
                            （默认全表；--model 按 source_model 过滤，逗号分隔）
  cache-hit <中文名...>     批量只查缓存（空格分隔多个词，按 cache_type 优先级给出最优条目）
  cache-list [--cache-type T] [--model 模型名] [--match 关键词]
                            列出缓存条目 = **读权威案例**（如 `--model demo_base_info`）；
                            权威案例以 md_field_cache 为准，历史物料不再参考
  cache-add <中文名> <英文名> [--cache-type T]  确认一条翻译进缓存（同时写词根使用明细）
  cache-add-batch --file f.tsv [--domain D] [--source-model M] [--cache-type T]
批量入缓存（TSV 两列：中文名<TAB>英文名）。**第 3 步才用**：用户已确认映射表 + review 全绿后回写
  cache-check --file f.tsv  拟用字段清单与缓存反查：输出英文名撞车 / 中文名相近条目
  cache-import-seed         field_cache.csv（或 .example）→ md_field_cache（首次引导种子导入）

词根使用明细（md_root_usage）：一行一个词根段，root_id 关联 md_word_roots.id，
非词根成分（固定搭配/白名单/规则衍生 flag、cn、to）root_id 为 NULL、seg_source 标注来源；
在 cache-add / cache-add-batch 确认入缓存时自动写入，幂等可重跑。

用法示例：
  python roots.py lookup 证券
  python roots.py sync                # 上游更新后，先跑 dry-run 看差异
  python roots.py sync --apply
  python roots.py translate --file fields.txt --domain 证券 --model demo_base_info_v2
  python roots.py cache-add 统一证券编码 scr_id 证券 --cache-type scr_base
"""
import argparse
import csv
import datetime
import os
import re
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(BASE)
CACHE_CSV = os.path.join(BASE, "field_cache.csv")
COLLOC_CSV = os.path.join(SKILL, "references", "fixed_collocations.csv")
WHITELIST_CSV = os.path.join(SKILL, "references", "fin_abbr_whitelist.csv")
CLASSIFIER_CSV = os.path.join(SKILL, "references", "mpc_classifiers.csv")


def seed_path(path):
    """词表/种子文件：优先真实文件，缺失时回退同名 `.example.<ext>`（开源仓库只带脱敏示例）。"""
    if os.path.exists(path):
        return path
    root, ext = os.path.splitext(path)
    fallback = root + ".example" + ext
    return fallback if os.path.exists(fallback) else path

# 常见无意义词，切词时剔除（不参与拼接）
STOPWORDS = {"的", "了", "和", "与", "或", "等", "是", "在", "及", "对应", "相关"}

# "是否/判断"类字段统一翻译为 xx_flag（=xxx标志）：剥离前缀，翻译剩余部分，追加 flag
FLAG_PREFIXES = ("是否", "判断")

# 规则衍生段的中文（en2zh 反查用）
RULE_ZH = {"flag": "标志", "to": "比值", "cn": "中文", "en": "英文"}

# 反查（英→中）忽略的短缩写：H=十六进制、S/Sec=秒、MD=麦氏久期…… 语料里全是误配，一律不猜；
# 固定搭配/类词/白名单是人工确认过的，不受此限（id=标识、cd=代码 照用）
MIN_ABBR_LEN = 2
ROOT_ABBR_MIN_LEN = 2

# 英→中 反查的别名/裁决表：源系统拼写 → 已有词根的中文（词根库同缩写多译时按源表语境取一个）。
# 只做"选哪个词根"，不新增缩写；`md`（麦氏久期）、`st`（街道）在源表名里另有含义，反查不采信。
# ponytail: 这张表是登记缺口的临时补丁，长期应由 词根治理组按源系统补登记词根别名。
EN_ALIAS = {
    # 同义不同拼写
    "px": "价格", "prc": "价格", "rtg": "评级", "moody": "穆迪评级", "fitch": "惠誉评级",
    "cpn": "票面利率", "ccy": "币种", "crncy": "币种", "rcv": "应收", "des": "说明",
    "typ": "类型", "redemp": "赎回", "settle": "结算", "notional": "名义", "jingzhi": "净值",
    "nxt": "下一个", "secu": "证券", "ticker": "代码", "war": "权证",
    "tot": "合计",
    # 词根库同缩写多译，按源表语境裁决（股票/基金/交易所/文件/机构/彭博/收入/持有）
    "stk": "股票", "fnd": "基金", "exch": "交易所", "file": "文件",
    "comp": "机构", "bb": "彭博", "income": "收入", "hold": "持有",
}
EN_IGNORE_ABBR = {"md", "st", "righ"}

# 字段默认词根段数上限，超过需用户审批（主数据内部规范守则）
MAX_WORDS = 5

# 缓存优先级：数值越小越优先。证券基础信息 > 证券子类基础信息 > 普通 > 其他
CACHE_TYPE_PRIORITY = {
    "scr_base": 1,    # 证券基础信息（scr_base_info 口径）
    "stk_base": 2,    # 股票基础信息
    "bnd_base": 2,    # 债券基础信息
    "idx_base": 2,    # 指数基础信息
    "fund_base": 2,   # 基金基础信息
    "common": 8,      # 普通模型字段
    "other": 9,       # 其他/未分类
}
KNOWN_CACHE_TYPES = set(CACHE_TYPE_PRIORITY)


def _operator():
    return os.environ.get("MD_OPERATOR") or __import__("db").get("MD_OPERATOR") or "unknown"


# ─────────────────────────── 词根数据源（MySQL 唯一数据源） ───────────────────────────

def load_roots():
    """词根唯一数据源 = md_word_roots；空表时报错并给出 sync --apply 引导指令。"""
    import db
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT root_zh, root_en, abbr, category FROM md_word_roots")
            rows = cur.fetchall()
    finally:
        try:
            conn.close()
        except Exception:
            pass
    if not rows:
        sys.exit("[roots] md_word_roots 为空。请先从词根平台首次引导："
                 "python scripts/roots.py sync --apply（需 .env 的 DDM_API_URL/USERNAME/COOKIE）。"
                 "词根不落本地文件，MySQL 是唯一数据源。")
    return [{"zh": r[0], "en": r[1], "abbr": r[2], "cat": r[3]} for r in rows]


def _fetch_ddm_items():
    """词根拉取逻辑封装在 ddm_client.py（自包含，便于本 skill 独立成仓迁移）。"""
    import ddm_client
    return ddm_client.fetch_all_roots()


def cmd_sync(args):
    import db
    ddm = _fetch_ddm_items()
    ddm_map = {(r["zh"], r["abbr"]): r for r in ddm}
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT root_zh, root_en, abbr, category FROM md_word_roots")
            db_rows = [{"zh": r[0], "en": r[1], "abbr": r[2], "cat": r[3]} for r in cur.fetchall()]
    finally:
        conn.close()
    db_map = {(r["zh"], r["abbr"]): r for r in db_rows}

    added, changed, removed = [], [], []
    for k, r in ddm_map.items():
        old = db_map.get(k)
        if not old:
            added.append(r)
        elif old["en"] != r["en"] or old["cat"] != r["cat"]:
            changed.append((old, r))
    for k, r in db_map.items():
        if k not in ddm_map:
            removed.append(r)

    today = datetime.date.today().isoformat()
    print(f"词根同步差异报告（{today}）：上游 {len(ddm)} 条 / 库内 {len(db_rows)} 条")
    print(f"  新增 {len(added)} 条：" + ("；".join(f"{r['zh']}→{r['abbr']}" for r in added[:20]) if added else "无"))
    if len(added) > 20:
        print(f"  …（其余 {len(added) - 20} 条新增已随 --apply 写入库内）")
    print(f"  变更 {len(changed)} 条：")
    for old, new in changed[:20]:
        print(f"    {old['zh']}→{old['abbr']}：全称「{old['en']}→{new['en']}」分类「{old['cat']}→{new['cat']}」")
    print(f"  上游已删除 {len(removed)} 条：" + ("；".join(f"{r['zh']}→{r['abbr']}" for r in removed[:20]) if removed else "无")
          + "（仅报告，不自动删除；需清理时人工处理）")

    if not args.apply:
        print("\n本次为 dry-run，未写库。确认无误后加 --apply 执行写入。")
        return 0

    conn = db.connect()
    try:
        with conn.cursor() as cur:
            for r in added + [new for _, new in changed]:
                cur.execute(
                    "INSERT INTO md_word_roots (root_zh, root_en, abbr, category, synced_at) "
                    "VALUES (%s,%s,%s,%s,%s) "
                    "ON DUPLICATE KEY UPDATE root_en=VALUES(root_en), category=VALUES(category), synced_at=VALUES(synced_at)",
                    (r["zh"], r["en"], r["abbr"], r["cat"], today))
        print(f"已写入 md_word_roots：新增+变更共 {len(added) + len(changed)} 条")
    finally:
        conn.close()
    return 0


# ─────────────────────────── 固定搭配 / 金融缩写白名单 ───────────────────────────

def _load_csv_rows(path, required_cols):
    path = seed_path(path)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = []
        for row in reader:
            row = {k.strip(): (v or "").strip() for k, v in row.items() if k}
            if all(row.get(c) for c in required_cols):
                rows.append(row)
        return rows


def load_collocations():
    """固定搭配表：中文搭配 → 固定缩写，最高优先级，覆盖词根字典里的旧译（如 计数→cnt 覆盖 Num）。"""
    return {r["zh"]: r["abbr"] for r in _load_csv_rows(COLLOC_CSV, ["zh", "abbr"])}


def load_whitelist():
    """金融缩写白名单：zh/abbr 都可命中，命中后跳过词根处理直接使用。"""
    by_zh, by_abbr = {}, {}
    for r in _load_csv_rows(WHITELIST_CSV, ["abbr"]):
        by_abbr[r["abbr"].lower()] = r
        if r.get("zh"):
            by_zh[r["zh"]] = r
    return by_zh, by_abbr


# ─────────────────────────── 字段翻译缓存（含优先级） ───────────────────────────

def _load_cache():
    """字段翻译缓存（MySQL，多用户共享）；CSV 仅作首次引导种子，不再读取。"""
    import db
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT fld_ch, fld_en, domain, cache_type, source_model, confirmed_by, "
                        "confirmed_at FROM md_field_cache")
            return [
                {"fld_ch": r[0], "fld_en": r[1], "domain": r[2], "cache_type": r[3] or "common",
                 "source_model": r[4], "confirmed_by": r[5], "confirmed_at": str(r[6])}
                for r in cur.fetchall()
            ]
    finally:
        conn.close()


def cache_type_priority(cache_type):
    return CACHE_TYPE_PRIORITY.get(cache_type, CACHE_TYPE_PRIORITY["other"])


def cache_lookup(zh, all_matches=False):
    """按 cache_type 优先级取最优缓存条目：证券基础信息 > 证券子类基础信息 > 普通 > 其他；
    同优先级取最近确认的一条。"""
    rows = [r for r in _load_cache() if r["fld_ch"] == zh]
    rows.sort(key=lambda r: r["confirmed_at"], reverse=True)   # 同优先级：最近确认的在前
    rows.sort(key=lambda r: cache_type_priority(r["cache_type"]))  # 优先级：数值小者在前（稳定排序）
    if all_matches:
        return rows
    return rows[0] if rows else None


def cache_upsert(zh, en, domain="", source_model="", cache_type="common", force=False):
    import db
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, fld_en, cache_type FROM md_field_cache WHERE fld_ch=%s "
                        "ORDER BY cache_type, confirmed_at", (zh,))
            rows = cur.fetchall()
            same_en = [r for r in rows if r[1] == en]
            conflicts = [r for r in rows if r[1] != en]
            higher_source = (conflicts and
                             cache_type_priority(cache_type) <
                             min(cache_type_priority(r[2]) for r in conflicts))
            if conflicts and higher_source:
                # 更高优先级来源（如证券基础信息）译名不同：允许并存，由 cache_type 优先级裁决
                cur.execute(
                    "INSERT INTO md_field_cache (fld_ch,fld_en,domain,cache_type,source_model,"
                    "confirmed_by,confirmed_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (zh, en, domain, cache_type, source_model, _operator(),
                     datetime.date.today().isoformat()))
                print(f"已入缓存（更高优先级来源并存，优先级裁决取用）：{zh} → {en}"
                      f"（cache_type={cache_type}，操作人={_operator()}）")
                _write_usage(source_model, zh, en)
                return 0
            if conflicts and not force:
                print(f"⚠️ 缓存冲突：{zh} 已有翻译 {conflicts[0][1]}（{conflicts[0][2]}），"
                      f"现拟改为 {en}（{cache_type}）。同名不同译会破坏\"同义字段同名\"，"
                      f"请先人工裁决后用 --force 覆盖。")
                return 2
            if conflicts and force:
                cur.execute("DELETE FROM md_field_cache WHERE fld_ch=%s AND fld_en != %s", (zh, en))
            if same_en:
                # 同翻译已在缓存：仅按需提升 cache_type 优先级，不重复插行
                better = [r for r in same_en
                          if cache_type_priority(cache_type) < cache_type_priority(r[2])]
                if better:
                    cur.execute("UPDATE md_field_cache SET cache_type=%s WHERE id=%s",
                                (cache_type, better[0][0]))
                    print(f"已提升缓存优先级：{zh} → {en}（{better[0][2]} → {cache_type}）")
                else:
                    print(f"缓存已存在相同翻译，跳过：{zh} → {en}")
                _write_usage(source_model, zh, en)
                return 0
            cur.execute(
                "INSERT INTO md_field_cache (fld_ch,fld_en,domain,cache_type,source_model,"
                "confirmed_by,confirmed_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (zh, en, domain, cache_type if cache_type in KNOWN_CACHE_TYPES else "common",
                 source_model, _operator(), datetime.date.today().isoformat()))
        print(f"已入缓存（MySQL）：{zh} → {en}（cache_type={cache_type}，操作人={_operator()}）")
    finally:
        conn.close()
    _write_usage(source_model, zh, en)
    return 0


def cmd_cache_hit(args):
    miss = 0
    for zh in args.zh:
        zh = zh.strip()
        if not zh:
            continue
        rows = cache_lookup(zh, all_matches=True)
        r = rows[0] if rows else None
        if r:
            print(f"CACHE HIT  {r['fld_ch']} → {r['fld_en']}  (cache_type={r['cache_type']}, "
                  f"来源模型: {r.get('source_model','')}, 确认: {r.get('confirmed_at','')})")
            for o in rows[1:]:
                print(f"           （低优先级并存条目：{o['fld_en']}，{o['cache_type']}）")
        else:
            miss += 1
            print(f"CACHE MISS  {zh}")
    return 0 if not miss else 2


def cmd_cache_list(args):
    """按 cache_type / source_model 列出缓存条目 —— 读**权威案例**用（如 --model demo_base_info）。
    比翻历史物料可靠：缓存是多人确认过的最新口径。"""
    import db
    where, params = [], []
    if args.cache_type:
        where.append("cache_type=%s"); params.append(args.cache_type)
    if args.model:
        where.append("source_model=%s"); params.append(args.model)
    if args.match:
        where.append("(fld_ch LIKE %s OR fld_en LIKE %s)"); params += [f"%{args.match}%"] * 2
    sql = "SELECT fld_ch, fld_en, cache_type, source_model FROM md_field_cache"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY fld_ch"
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    finally:
        conn.close()
    if not rows:
        print("无匹配条目（检查 --cache-type / --model / --match 取值）")
        return 2
    print(f"共 {len(rows)} 条（中文名\t英文名\tcache_type\t来源模型）：")
    for ch, en, ct, sm in rows:
        print(f"{ch}\t{en}\t{ct}\t{sm or '-'}")
    return 0


def cmd_cache_add(args):
    if args.cache_type not in KNOWN_CACHE_TYPES:
        print(f"⚠️ 未知 cache_type「{args.cache_type}」，可选值：{'/'.join(sorted(KNOWN_CACHE_TYPES))}")
        return 2
    return cache_upsert(args.zh, args.en, args.domain or "", args.source_model or "",
                        args.cache_type, args.force)


def cmd_cache_add_batch(args):
    ok, fail = 0, 0
    with open(args.file, encoding="utf-8-sig") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip() or line.startswith("中文名") or line.startswith("fld_ch"):
                continue
            parts = line.split("\t")
            if len(parts) < 2 or not parts[1].strip():
                fail += 1
                print(f"  ⚠️ 跳过无效行：{line[:60]}")
                continue
            zh, en = parts[0].strip(), parts[1].strip()
            rc = cache_upsert(zh, en, args.domain or "", args.source_model or "",
                              args.cache_type, args.force)
            ok += (rc == 0)
            fail += (rc != 0)
    print(f"\n批量入缓存完成：成功 {ok} 条，失败/冲突 {fail} 条（冲突行请人工裁决后 --force 或改译名）")
    return 0 if fail == 0 else 2


def cmd_cache_check(args):
    """拟用字段清单 vs 缓存反查：发现英文名撞车与中文名相近条目（防重复造名）。"""
    cache = _load_cache()
    proposed = []
    with open(args.file, encoding="utf-8-sig") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 2 and parts[0].strip() and parts[1].strip():
                proposed.append((parts[0].strip(), parts[1].strip()))

    en_index = {}
    for r in cache:
        en_index.setdefault(r["fld_en"].lower(), []).append(r)

    issues = 0
    print("中文名\t英文名\t反查结果")
    for zh, en in proposed:
        hits = en_index.get(en.lower(), [])
        if hits:
            issues += 1
            owners = "；".join(f"{h['fld_ch']}({h['cache_type']},{h['source_model']})" for h in hits[:3])
            print(f"{zh}\t{en}\t⚠️ 英文名已被占用：{owners} → 复用该名或换名")
            continue
        similar = [r for r in cache if r["fld_ch"] == zh]
        if similar:
            issues += 1
            best = sorted(similar, key=lambda r: cache_type_priority(r["cache_type"]))[0]
            print(f"{zh}\t{en}\t⚠️ 缓存已有该中文名：{best['fld_en']}({best['cache_type']}) → 应复用缓存译名")
        else:
            fuzzy = [r for r in cache if zh and (zh in r["fld_ch"] or r["fld_ch"] in zh)]
            if fuzzy:
                issues += 1
                cand = "；".join(f"{r['fld_ch']}→{r['fld_en']}" for r in fuzzy[:3])
                print(f"{zh}\t{en}\tℹ️ 缓存有相近中文名：{cand} → 判断是否同一概念（参考 concept_mapping.md 归一）")
            else:
                print(f"{zh}\t{en}\t✅ 无撞车")
    print(f"\n反查完成：{issues} 条需关注。")
    return 0


def cmd_cache_import_seed(args):
    """field_cache.csv → md_field_cache（首次引导种子导入；缺真实文件时用 .example 示例）。"""
    ok, skip = 0, 0
    with open(seed_path(CACHE_CSV), encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            zh, en = (row.get("fld_ch") or "").strip(), (row.get("fld_en") or "").strip()
            if not zh or not en:
                continue
            rc = cache_upsert(zh, en, (row.get("domain") or "").strip(),
                              (row.get("source_model") or "").strip(), "common", force=True)
            ok += (rc == 0)
            skip += (rc != 0)
    print(f"\n种子导入完成：写入 {ok} 条，跳过 {skip} 条")
    return 0


# ─────────────────────────── 词根查询与翻译 ───────────────────────────

def lookup_exact(roots_by_zh, term):
    return roots_by_zh.get(term, [])


def lookup_contains(entries, term):
    return [e for e in entries if term in e["zh"] or e["zh"] in term]


def lookup_fuzzy(entries, term, limit=10):
    """宽松匹配：单侧包含任一字符（用于兜底提示，需人工确认）。"""
    hits = [e for e in entries if term and any(ch in e["zh"] for ch in term)]
    return hits[:limit]


def lookup_by_en(entries, term, limit=10):
    """英文术语回查：中文 miss 时把中文翻成英文术语，按词根库全称列模糊匹配。
    例：永续→Perpetual→perp、递进→Progressive→进度 prog、浮息→Floating→浮动 flot。"""
    t = term.strip().lower()
    if not t:
        return []
    starts = [e for e in entries if (e["en"] or "").lower().startswith(t)]
    contains = [e for e in entries if t in (e["en"] or "").lower() and e not in starts]
    return (starts + contains)[:limit]


def _lookup_one(entries, roots_by_zh, term):
    """单个词的四级匹配（精确 → 包含 → 模糊 → 英文回查），返回 (hits, level, en_hits)。"""
    hits = lookup_exact(roots_by_zh, term)
    level = "exact"
    if not hits:
        hits = lookup_contains(entries, term)
        level = "contains"
    if not hits:
        hits = lookup_fuzzy(entries, term)
        level = "fuzzy"
    if not hits:
        hits = lookup_by_en(entries, term)
        level = "en-match"
    en_hits = [e for e in lookup_by_en(entries, term) if e not in hits] if level != "exact" else []
    return hits, level, en_hits


def cmd_lookup(args):
    """批量查词根：多个词共用一次词根库加载与连接，减少冷启动开销。"""
    entries = load_roots()
    roots_by_zh = {}
    for e in entries:
        roots_by_zh.setdefault(e["zh"], []).append(e)
    miss = 0
    for term in args.term:
        term = term.strip()
        if not term:
            continue
        hits, level, en_hits = _lookup_one(entries, roots_by_zh, term)
        if not hits:
            miss += 1
            print(f"【{term}】未找到词根。该词需要在词根治理组登记新词根后才能使用，"
                  f"禁止臆造缩写。建议：换近义词查 lookup，或作为规则缺口提交知识库提案。")
            continue
        print(f"【{term}】匹配级别：{level}，共 {len(hits)} 条：")
        for e in hits[:20]:
            print(f"  {e['zh']}  →  {e['abbr']}  (全称: {e['en'] or '-'}, 分类: {e['cat'] or '-'})")
        if en_hits:
            print(f"  【{term}】英文回查候选（en-match，中文候选不达意时优先看这里）：")
            for e in en_hits[:10]:
                print(f"  {e['zh']}  →  {e['abbr']}  (全称: {e['en'] or '-'}, 分类: {e['cat'] or '-'})")
    return 0 if not miss else 2


def _segment(zh, collocations, roots_by_zh, whitelist_by_zh, whitelist_by_abbr):
    """最长匹配切词。返回 [(片段, 缩写或None, 来源)]，来源 ∈ collocation/whitelist/root/stop/unknown。"""
    parts, rest = [], zh
    while rest:
        matched = None
        for l in range(len(rest), 0, -1):
            seg = rest[:l]
            if seg in STOPWORDS:
                matched = (seg, None, "stop")
                break
            if seg in collocations:
                matched = (seg, collocations[seg], "collocation")
                break
            if seg in whitelist_by_zh:
                matched = (seg, whitelist_by_zh[seg]["abbr"], "whitelist")
                break
            wl = whitelist_by_abbr.get(seg.lower())  # 备注里直接写 PE/TTM 等缩写时整体命中
            if wl:
                matched = (seg, wl["abbr"], "whitelist")
                break
            ex = lookup_exact(roots_by_zh, seg)
            if ex:
                matched = (seg, ex[0]["abbr"], "root")
                break
        if not matched:
            matched = (rest[:1], None, "unknown")
        parts.append(matched)
        rest = rest[len(matched[0]):]
    return parts


def translate_names(names):
    """批量翻译核心（供闭环脚本 import 后进程内复用，免子进程冷启动与重复连库）。
    返回 [(zh, en, src, roots_used), ...]，不打印、不落盘。"""
    roots_by_zh = {}
    for e in load_roots():
        roots_by_zh.setdefault(e["zh"], []).append(e)
    collocations = load_collocations()
    whitelist_by_zh, whitelist_by_abbr = load_whitelist()

    results = []
    for zh in names:
        flag_field = False
        core = zh
        for p in FLAG_PREFIXES:
            if core.startswith(p) and len(core) > len(p):
                flag_field = True
                core = core[len(p):]
                break

        hit = cache_lookup(core) or cache_lookup(zh)
        if hit:
            en = hit["fld_en"]
            if flag_field and not en.endswith("_flag"):
                en = f"{en}_flag"
            src = f"cache({hit['cache_type']})"
            roots_used = "复用缓存译名" + ("；是否→flag(rule)" if flag_field and en.endswith("_flag") and "flag" not in hit["fld_en"] else "")
        else:
            parts = _segment(core, collocations, roots_by_zh, whitelist_by_zh, whitelist_by_abbr)
            abbrs = [(seg, a, tag) for seg, a, tag in parts if a]
            unknown = "".join(seg for seg, a, tag in parts if a is None and tag != "stop")
            if abbrs and not unknown:
                words = [a.lower() for _, a, _ in abbrs]
                dedup = [w for i, w in enumerate(words) if i == 0 or w != words[i - 1]]
                dedup_applied = dedup != words
                if flag_field:
                    dedup.append("flag")
                en = "_".join(dedup)
                roots_used = "|".join(f"{seg}→{a.lower()}({tag})" for seg, a, tag in abbrs)
                if dedup_applied:
                    roots_used += "|连续重复词去重"
                if flag_field:
                    roots_used += "|是否→flag(rule)"
                src = "root-compose"
                if len(dedup) > MAX_WORDS:
                    src = f"needs-approval({len(dedup)}段>{MAX_WORDS})"
            else:
                en = "?? 未命中词根"
                roots_used = f"未命中片段「{unknown}」" if unknown else "切词失败"
                src = "miss"
        results.append((zh, en, src, roots_used))
    return results


def cmd_translate(args):
    if args.file:
        with open(args.file, encoding="utf-8") as f:
            names = [l.strip() for l in f if l.strip()]
    else:
        names = [l.strip() for l in sys.stdin if l.strip()]

    results = translate_names(names)
    misses = [(zh, (ru.split("「", 1)[1].split("」", 1)[0] if "「" in ru else zh))
              for zh, en, src, ru in results if src == "miss"]
    approval = [r for r in results if r[2].startswith("needs-approval")]

    print("中文名\t英文名\t来源\t词根构成")
    for zh, en, src, ru in results:
        print(f"{zh}\t{en}\t{src}\t{ru}")
    if approval:
        print(f"\n⛔ {len(approval)} 个字段超过 {MAX_WORDS} 个单词（{MAX_WORDS}段上限），"
              f"需用户审批后才能使用：", file=sys.stderr)
        for zh, en, _, ru in approval:
            print(f"  {zh} → {en}", file=sys.stderr)
    if misses:
        print(f"\n⚠️ {len(misses)} 个字段含未命中词根，禁止臆造：", file=sys.stderr)
        for zh, unknown in misses:
            print(f"  {zh} → 未命中片段「{unknown}」", file=sys.stderr)

    return 0 if not misses and not approval else 2


# ─────────────────────────── 词根使用明细（md_root_usage，一行一个词根段） ───────────────────────────

RULE_TOKENS = {"flag", "to", "cn", "en"}  # 规则衍生段：xx_flag、比值 to 连接、cn/en 语言限定


def _load_abbr_index():
    """反解用索引：abbr（含 par_val 等含下划线复合缩写，统一小写）→ (来源, root_id|None, 中文, 规范缩写)。
    优先级 固定搭配 > MPC 类词 > 白名单 > 词根（abbr + 英文全称归一）；后命中者不覆盖先命中。
    单字符缩写不参与（H=十六进制/Sec=秒 这类在源表名里 100% 误配）。"""
    import db
    idx = {}

    def put(abbr, zh, src, rid=None):
        abbr = (abbr or "").strip().lower()
        if len(abbr) < MIN_ABBR_LEN or not zh:
            return
        canon = abbr
        idx.setdefault(abbr, (src, rid, zh, canon))
        idx.setdefault(abbr.replace("_", ""), (src, rid, zh, canon))

    for zh, ab in load_collocations().items():
        put(ab, zh, "collocation")
    for r in _load_csv_rows(CLASSIFIER_CSV, ["中文类词", "英文"]):
        put(r["英文"], r["中文类词"], "classifier")
    _, wl_by_abbr = load_whitelist()
    for ab, row in wl_by_abbr.items():
        put(ab, row.get("zh") or "", "whitelist")
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, abbr, root_zh, root_en FROM md_word_roots ORDER BY id")
            for rid, ab, zh, en in cur.fetchall():
                if len((ab or "").strip()) >= ROOT_ABBR_MIN_LEN:   # 1~2 字符词根缩写歧义太大，反查不采信
                    put(ab, zh, "root", rid)
                en_key = re.sub(r"[^a-z0-9]", "", (en or "").lower())
                if len(en_key) >= 3:
                    idx.setdefault(en_key, ("root-en", rid, zh, (ab or "").strip()))
    finally:
        conn.close()
    for ab, zh in EN_ALIAS.items():                 # 别名/裁决最后落地，覆盖同缩写的词根旧译
        idx[ab] = ("alias", None, zh, ab)
    for ab in EN_IGNORE_ABBR:
        idx.pop(ab, None)
    return idx


def decompose_fld_en(fld_en, idx):
    """最终英文字段名 → 词根明细 [{abbr, root_id, seg_source, seg_seq}]。
    贪心最长匹配：先把整段名按 _ 切 token，再从长到短尝试把连续 token 拼回
    复合缩写（bnd_par_val→bnd+par_val 两段）；拼不回的落 rule/unknown。"""
    toks = [t for t in fld_en.split("_") if t]
    out, i = [], 0
    while i < len(toks):
        hit = None
        for j in range(len(toks) - i, 0, -1):
            base = "_".join(toks[i:i + j]).lower()
            # j>1 只认带下划线的复合缩写（bnd_par_val→bnd+par_val），否则 s+md 会拼出 smd 误配
            for cand in ((base,) if j > 1 else (base, base.replace("_", ""))):
                ent = idx.get(cand)
                if ent:
                    hit = (ent[3] or cand, ent[1], ent[0], j)
                    break
            if hit:
                break
        if hit:
            abbr, rid, src, j = hit
            out.append({"abbr": abbr, "root_id": rid, "seg_source": src,
                        "seg_seq": len(out) + 1, "ntok": j})
            i += j
        else:
            tok = toks[i].lower()
            src = "rule" if (tok in RULE_TOKENS or tok.isdigit()) else "unknown"
            out.append({"abbr": tok, "root_id": None, "seg_source": src,
                        "seg_seq": len(out) + 1, "ntok": 1})
            i += 1
    return out


# ─────────────────────────── 英→中（源表名/字段名翻译） ───────────────────────────

def _seg_zh(seg, idx):
    """单段 → 中文：词根命中 → 中文；规则段（flag/to/数字）→ 规则中文或空；否则 None（未命中）。"""
    ent = idx.get(seg["abbr"].lower())
    if ent:
        return ent[2].split("、")[0]      # 词根多义（衍生、衍生品）取第一个义项，拼名更干净
    if seg["seg_source"] == "rule":
        return RULE_ZH.get(seg["abbr"], "")
    return None


def translate_en_to_zh(en, idx, kind="field"):
    """英文名 → 中文名。返回 (中文名, 词根构成, 未命中片段)。
    kind=field：逐段译中文拼接，任一段未命中即「无法翻译」。
    kind=table：开头连续的非词根段视为模型内部标识（ods_/dwd_/dim_… 等非词根段）不译；
               结尾 _ext → 「…拓展表」；仍有未命中段即「无法翻译」。"""
    segs = decompose_fld_en(en, idx)
    ext, head = False, 0
    if kind == "table":
        while head < len(segs) and segs[head]["seg_source"] == "unknown":
            head += 1
        if segs and segs[-1]["abbr"].lower() == "ext" and _seg_zh(segs[-1], idx):
            ext, segs = True, segs[:-1]     # 结尾 _ext = 拓展表（用户口径），单独追加
    parts, composed, miss = [], [], []
    for i, s in enumerate(segs):
        zh = _seg_zh(s, idx)
        if i < head:
            composed.append(f"-{s['abbr']}(模型内部标识)")
            continue
        if s["abbr"].isdigit():
            composed.append(f"-{s['abbr']}(内部编号)")
            continue
        if zh is None:
            miss.append(s["abbr"])
            composed.append(f"?{s['abbr']}")
        elif zh:
            parts.append(zh)
            composed.append(f"{s['abbr']}→{zh}({s['seg_source']})")
        else:
            composed.append(f"-{s['abbr']}")
    zh_name = "".join(parts) + ("拓展表" if ext else "")
    if ext:
        composed.append("ext→拓展表(rule)")
    if miss or not zh_name:
        zh_name = "无法翻译"
    return zh_name, "|".join(composed), miss


def cmd_en2zh(args):
    idx = _load_abbr_index()
    with open(args.file, encoding="utf-8-sig") as f:
        lines = [l.strip() for l in f]
    lines = [l for l in lines if l]                      # 去空行
    lines = [l for l in lines[1:] if l] if lines and "_" not in lines[0] else lines  # 去表头（无下划线）

    kind = args.kind
    rows, miss_cnt, miss_frag = [], 0, {}
    for name in lines:
        zh, composed, miss = translate_en_to_zh(name, idx, kind)
        if zh == "无法翻译":
            miss_cnt += 1
            for m in miss:
                miss_frag[m] = miss_frag.get(m, 0) + 1
        rows.append((name, zh, composed))

    if args.out:
        with open(args.out, "w", encoding="utf-8-sig", newline="") as f:   # 带 BOM，Excel 直开不乱码
            w = csv.writer(f, delimiter="\t", lineterminator="\n")
            w.writerow([{"field": "字段", "table": "表"}[kind], "中文名", "词根构成"])
            w.writerows(rows)
        print(f"已写出 {args.out}：共 {len(rows)} 行")
    else:
        print(f"{kind}\t中文名\t词根构成")
        for r in rows:
            print("\t".join(r))

    print(f"\n翻译完成：{len(rows)} 行，无法翻译 {miss_cnt} 行（{miss_cnt * 100 // max(len(rows), 1)}%）")
    if miss_frag:
        top = sorted(miss_frag.items(), key=lambda kv: -kv[1])[:30]
        print("未命中词根片段（按出现次数）：" + "；".join(f"{k}×{v}" for k, v in top))
    return 0


def self_check():
    """最小回归自检：python roots.py en2zh --self-check（需 MySQL 词根库）。"""
    idx = _load_abbr_index()
    cases = [
        ("src_id", "field", "来源标识"),
        ("rec_upd_time", "field", "记录更新时间"),
        ("ods_stk_iss_ext", "table", "股票发行拓展表"),
        ("ods_bond_base_info_tbl", "table", "债券基础信息表"),
        ("scr_abbr_pinyin_name", "field", "证券拼音简称"),   # 拼音简称 MPC 口径（M=scr/P=abbr_pinyin/C=name）
        ("raw_05", "table", "无法翻译"),          # 表名只有内部标识+编号
        ("bjxtdm_lj", "field", "无法翻译"),        # 拼音字段无词根
    ]
    bad = [(n, k, translate_en_to_zh(n, idx, k)[0], want)
           for n, k, want in cases if translate_en_to_zh(n, idx, k)[0] != want]
    for n, k, got, want in bad:
        print(f"FAIL {n}（{k}）：期望 {want}，实际 {got}")
    print("自检通过" if not bad else f"自检失败 {len(bad)} 条")
    return 1 if bad else 0


def _write_usage(model_name, fld_ch, fld_en, idx=None):
    """确认入缓存时调用：字段名反解为词根明细写入 md_root_usage（幂等，可重跑）。"""
    if idx is None:
        idx = _load_abbr_index()
    segs = decompose_fld_en(fld_en, idx)
    import db
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            for s in segs:
                cur.execute(
                    "INSERT INTO md_root_usage (model_name, fld_ch, fld_en, root_id, abbr, "
                    "seg_seq, seg_source, operator) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON DUPLICATE KEY UPDATE root_id=VALUES(root_id), abbr=VALUES(abbr), "
                    "seg_source=VALUES(seg_source), operator=VALUES(operator)",
                    (model_name or "unknown", fld_ch, fld_en, s["root_id"], s["abbr"],
                     s["seg_seq"], s["seg_source"], _operator()))
    finally:
        conn.close()
    return len(segs)


def cmd_usage_sync(args):
    """md_field_cache → md_root_usage 存量回填：把已确认字段反解为词根明细。
    model_name 取 source_model（空则 unknown）；幂等，重复执行只更新不重复插行。"""
    import db
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            if args.model:
                models = [m.strip() for m in args.model.split(",") if m.strip()]
                ph = ",".join(["%s"] * len(models))
                cur.execute(f"SELECT DISTINCT fld_ch, fld_en, COALESCE(source_model,'') "
                            f"FROM md_field_cache WHERE source_model IN ({ph})", models)
            else:
                cur.execute("SELECT DISTINCT fld_ch, fld_en, COALESCE(source_model,'') "
                            "FROM md_field_cache")
            rows = cur.fetchall()
    finally:
        conn.close()
    idx = _load_abbr_index()
    n = 0
    for zh, en, model in rows:
        _write_usage(model or "unknown", zh, en, idx)
        n += 1
    print(f"词根使用明细回填完成：{n} 个字段 → md_root_usage（model_name 取 source_model）")
    return 0


def main():
    p = argparse.ArgumentParser(description="词根查询与字段翻译（MySQL 版）")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sync")
    s.add_argument("--apply", action="store_true", help="写入 md_word_roots（默认 dry-run）")
    s.set_defaults(fn=cmd_sync)
    s = sub.add_parser("lookup"); s.add_argument("term", nargs="+", help="一个或多个中文词（空格分隔），批量一次查"); s.set_defaults(fn=cmd_lookup)
    s = sub.add_parser("translate")
    s.add_argument("--file"); s.add_argument("--model", default="", help="仅作输出标注；使用明细在确认入缓存时写入")
    s.set_defaults(fn=cmd_translate)
    s = sub.add_parser("usage-sync-cache")
    s.add_argument("--model", help="按 source_model 过滤，逗号分隔；缺省全表回填")
    s.set_defaults(fn=cmd_usage_sync)
    s = sub.add_parser("cache-hit"); s.add_argument("zh", nargs="+", help="一个或多个中文名（空格分隔），批量一次查"); s.set_defaults(fn=cmd_cache_hit)
    s = sub.add_parser("cache-list", help="按 cache_type/source_model 列出缓存条目（读权威案例）")
    s.add_argument("--cache-type"); s.add_argument("--model", help="source_model，如 demo_base_info")
    s.add_argument("--match", help="中文名/英文名模糊过滤")
    s.set_defaults(fn=cmd_cache_list)
    s = sub.add_parser("cache-add")
    s.add_argument("zh"); s.add_argument("en")
    s.add_argument("--domain"); s.add_argument("--source-model")
    s.add_argument("--cache-type", default="common",
                   help="缓存优先级类型：scr_base/stk_base/bnd_base/idx_base/fund_base/common/other")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_cache_add)
    s = sub.add_parser("cache-add-batch")
    s.add_argument("--file", required=True, help="TSV 两列：中文名<TAB>英文名")
    s.add_argument("--domain"); s.add_argument("--source-model")
    s.add_argument("--cache-type", default="common"); s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_cache_add_batch)
    s = sub.add_parser("cache-check")
    s.add_argument("--file", required=True, help="TSV 两列：中文名<TAB>英文名（拟用字段清单）")
    s.set_defaults(fn=cmd_cache_check)
    s = sub.add_parser("cache-import-seed").set_defaults(fn=cmd_cache_import_seed)
    s = sub.add_parser("en2zh", help="源表名/字段名（英文）→ 中文名：词根反查，TSV 落盘")
    s.add_argument("--file", required=True, help="每行一个英文名（首行为表头时自动跳过）")
    s.add_argument("--kind", choices=["field", "table"], default="field",
                   help="field：字段名；table：表名（跳过开头内部标识、结尾 _ext→拓展表）")
    s.add_argument("--out", help="输出 TSV 路径（缺省打印到 stdout）")
    s.add_argument("--self-check", action="store_true", help="跑最小回归自检后退出")
    s.set_defaults(fn=lambda a: self_check() if a.self_check else cmd_en2zh(a))
    args = p.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
