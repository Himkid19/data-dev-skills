#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
通用主数据模型闭环生成器 —— 执行效率守则 #3 的模板实现（替代逐模型手写一次性 build 脚本）。

用法：
  # 第 1 步（MPC 转义预览，每模型第一步必须跑）：输入 DDL 或用户粘贴的字段清单 TSV
  python scripts/model_builder.py --fields <字段清单.tsv> --out <输出目录> --preview
  #   产出：转义预览.tsv（交团队约定）+ 归一决策.tsv（空 confirmed 列的种子）
  # 团队约定/批注后（agent 只改 mdl_fld_ch/mdl_fld_en/note，不要动别的）：
  python scripts/model_builder.py --mark-confirmed <归一决策.tsv> --by "<用户原话/姓名>"
  # 第 2 步（映射落盘，confirmed 列全有值才准跑）：
  python scripts/model_builder.py --fields <字段清单.tsv> --norm <归一决策.tsv> \
      --tbl-ch 债券基础信息 --tbl-en bnd_base_info \
      --src-ch 债券基础信息 --src-en demo_bond_base_info_c \
      --out <输出目录> [--src-id VENDOR_A] [--author 主数据建模] [--opdate YYYYMMDD]

归一决策 TSV（每模型一份，只写"判断"，不写机制；5 列，TAB 分隔，旧 4 列文件仍兼容）：
  src_fld_en	mdl_fld_ch	mdl_fld_en	note	confirmed
  - mdl_fld_ch 空 = 沿用源中文经引擎默认归一
  - mdl_fld_en 空 = 走词根翻译链路（固定搭配→白名单→缓存→词根，进程内 roots.translate_names）
  - 多个源字段给同一 mdl_fld_ch = 声明同义合并（同义字段同名）；不同中文名撞同一英文名 = 重名错误
  - confirmed 空 = 用户未确认（正式构建会拒绝），由 --mark-confirmed 在团队约定后盖章

字段清单 TSV（--fields，用户 99% 给这个，不是 DDL）：
  4 列 表中文名/表英文名/字段中文名/源英文字段名，或 2 列 字段中文名/源英文字段名；
  4 列时首个数据行的表中文/表英文自动填充 --tbl-ch/--src-ch 等缺省参数。

引擎内置默认归一（机制层，norm 行可覆盖）：
  ① 尾部单位括注换算：份→份额、元→金额 并入字段中文（万/亿前缀保留进备注；
     中文已含量度/价格词尾不重复追加；映射外单位剥离进备注并上报待团队约定）
  ② 是否X → X标志
  ③ ×日 → ×日期
  ④ ××标的 → ××标的证券代码（concept_mapping §2.1-4）
  ⑤ §4 标准字段按 src_en 自动映射；src_id/src_rec_no 注入；分区字段取 DDL partitioned by

自校验（对齐 review-checklist A/B）全过才落盘：
  13列映射TSV / roots_used.tsv / cache_input.tsv / 01~04 MD / 码值附录。
需用户审批的项（不在 note 留痕就拒跑）：超 5 个词根段、超 25 位、规范特例缺类词（「类词豁免：<理由>」）；
审批留痕写法：「已审批：<理由>」（超长写「超长审批：<理由>」也可）。
"""
import argparse
import csv
import datetime
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import roots  # noqa: E402  进程内复用，免子进程冷启动

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ── §4 主数据标准字段：src_en → (目标中文, 目标英文, 备注)；命中即自动映射，不参与翻译 ──
STANDARD_MAP = {
    "busi_date": ("业务日期", "busi_date", "标准字段：固定数据日期"),
    "rec_id": ("记录编号", "rec_no", "标准字段 rec_no（dm_mast 旧名 rec_id）"),
    "secu_id": ("统一证券编码", "scr_id", "标准字段；词根化 secu→scr"),
    "rec_upd_time": ("数据更新时间", "rec_upd_time", "标准字段（源：记录修改时间）"),
    "rec_down_time": ("数据进表时间", "rec_down_time", "标准字段（源：记录创建时间）"),
    # 并行标志/并行标识：§4 一律映射 grp_id，禁止按字面直译（prll_flag 是历史别名，一并收口）
    "grp_id": ("并行标志", "grp_id", "标准字段（源：并行标志/并行标识）"),
    "prll_flag": ("并行标志", "grp_id", "标准字段：源'并行标志'一律映射 grp_id（禁止 prll_flag 直译）"),
}
# 每表必注的衍生标准字段（与源字段无一一对应）
INJECTED = [
    ("源系统标识", "src_id", "源系统标识", "", "分区字段（标准字段），固定值'{src_id}'", "固定值'{src_id}'"),
    ("源记录编号", "src_rec_no", "记录编号", "rec_id", "溯源字段（标准），取源 rec_id", "rec_id(溯源取值)"),
]

# 单位 → 度量词换算（换算后并入字段中文；度量词走 fixed_collocations/词根库：金额→amt、份额→shr）
UNIT_MEASURE = {"份": "份额", "元": "金额"}
# 已知但不参与换算的单位（剥离进备注即可，不报确认）；万/亿 前缀自动剥离保留口径
KNOWN_UNITS = {"%", "天", "年", "户", "只", "笔", "日", "月"}
ENUM_LIKE = re.compile(r"^[-0-9]")  # 括注以数字/负号开头 = 码值枚举口径，不是单位

UNIT = re.compile(r"[（(]([^）)]*)[）)]\s*$")
FLD = re.compile(r"(\w+)\s+string comment\s+'([^']*)'")
PART = re.compile(r"partitioned\s+by\s*\((\w+)\s+string comment\s+'([^']*)'", re.I)
ENUM_HINT = re.compile(r"[（(](?=[0-9])|1是0否")
# 中文已含这些度量/价格/限额度量词尾时不再追加度量词（价格（元）≠价格金额、数量上限(亿份)≠…上限份额）
HAS_MEASURE = re.compile(r"(价格|金额|数量|份额|天数|年数|次数|利率|率|数|价|上限|下限)$")

# ── 类词收尾校验：英文名必须以类词收尾（MPC 三段式的 Classifier）──
# 典型缺陷：序号/次序/顺序→seq 缺 no 类词（应为 seq_no）。类词表权威数据 = references/mpc_classifiers.csv；
# COMPAT_TAILS = 历史固定搭配里当类词用的尾（说明→desc、比例→prop、方式→way）。
#  团队约定 A：`ind`（历史旧根 标识→Ind）**不再自动放行**——"××标识"按语义分流为 id（ID 语义）
# 或 flag（指示/判断语义），有歧义交团队约定；确需 `_ind` 的在归一决策 note 写「类词豁免：<理由>」。
# times 为 次数→times 作废前的存量尾，仅放行老字段，新字段必须 cnt
COMPAT_TAILS = {"desc", "prop", "way", "method", "abbr_name", "times"}
EXEMPT_NOTE = "类词豁免"  # 规范特例：在归一决策 TSV 的 note 里写「类词豁免：<理由>」即放行
# 需用户审批的项（超 5 词根段 / 超 25 位）：用户同意后在 note 写「已审批：<理由>」即放行
MAX_WORDS, MAX_LEN = 5, 25
APPROVAL_NOTES = ("已审批", "超长审批", "超段审批")
STD_EN = {v[1] for v in STANDARD_MAP.values()} | {f[1] for f in INJECTED} | {"remark", "grp_id"}
# 源英文字段名尾 = 表示类可判断（concept_mapping §2.1「源英文可判断按源」）：目标沿用同一个类词尾。
# _id：机构编号 corp_id → co_id（证券类 secu_id 由 STANDARD_MAP 直接给 scr_id）
# _flag：次级债混合资本债标识 subordinate_flag → …_flag
SRC_TAIL = {"id": "id", "flag": "flag"}
SRC_TAIL_RE = re.compile(r"(?:^|_)(id|flag)$", re.I)


def classifier_tails():
    """类词尾集合（小写）：references/mpc_classifiers.csv 的英文列 + COMPAT_TAILS。"""
    tails = set(COMPAT_TAILS)
    with open(os.path.join(SKILL_DIR, "references", "mpc_classifiers.csv"), encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            ab = (row.get("英文") or "").strip().lower()
            if ab:
                tails.add(ab)
    return tails


def parse_fields_tsv(path):
    """用户粘贴的字段清单 TSV → ([ (en, ch), ... ], {'src_ch','src_en'})。
    支持 4 列（源表中文/源表英文/字段中文/源字段英文，源表名从首个数据行取）与 2 列（字段中文/源英文）；
    自动跳过表头行与空行。"""
    flds, meta = [], {"src_ch": "", "src_en": ""}
    header = {"字段中文名", "中文字段名", "源字段中文名", "中文名", "src_fld_ch"}
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.reader(f, delimiter="\t"):
            cells = [c.strip() for c in row if c.strip()]
            if len(cells) < 2 or cells[-2] in header:  # 空行 / 表头行
                continue
            if len(cells) >= 4:
                ch, en = cells[-2], cells[-1]
                meta["src_ch"], meta["src_en"] = cells[0], cells[1]
            elif len(cells) == 2:
                ch, en = cells
            else:
                sys.exit(f"[fields] 字段清单每行需 2 列（字段中文/源英文）或 ≥4 列（表中文/表英文/字段中文/源英文）：{cells}")
            flds.append((en, ch))
    if not flds:
        sys.exit(f"[fields] 未解析到字段：{path}")
    return flds, meta


# ── MPC 转义（Modifier 修饰词 + Part 基词 + Classifier 类词/量词）──
# 类词表权威数据 = references/mpc_classifiers.csv；
# 并非所有字段都能三段齐全，按实际提取，允许 M+C / P+C 等组合（空位留白）。
CLASSIFIERS = ["价格", "金额", "数量", "股数", "手数", "份额", "计数", "比率", "率", "收益率",
               "溢价", "价差", "代码", "编号", "标识", "名称", "日期", "时间", "周期", "盈亏",
               "保证金", "状态", "标志", "权重", "等级", "期限", "文本", "数值", "次数", "年份"]
MEASURE_CLS = {"份额", "金额", "数量", "股数", "手数", "计数", "次数"}
MODIFIER_HINTS = ["单一投资者", "机构投资者", "个人投资者", "封闭期", "锁定期", "存续期", "证券", "股票",
                  "债券", "基金", "指数", "期货", "期权", "沪市", "深市", "机构", "日常"]


def extract_mpc(mdl_ch):
    """MPC 逐词提取（草稿）：返回 (Modifier, Part, Classifier)，允许空串；agent 语义复核后交团队约定。"""
    cls = next((c for c in sorted(CLASSIFIERS, key=len, reverse=True) if mdl_ch.endswith(c)), "")
    head = mdl_ch[: len(mdl_ch) - len(cls)] if cls else mdl_ch
    mod = next((m for m in sorted(MODIFIER_HINTS, key=len, reverse=True) if head.startswith(m)), "")
    part = head[len(mod):]
    return mod, part, cls


def parse_ddl(path):
    """解析建表 DDL：返回 (列字段列表, 分区字段 or None)。"""
    text = open(path, encoding="utf-8").read()
    part = PART.search(text)
    part_fld = (part.group(1), part.group(2)) if part else None
    flds = FLD.findall(text)
    if part_fld:
        flds = [(e, c) for e, c in flds if e != part_fld[0]]
    return flds, part_fld


def load_norm(path):
    """归一决策 TSV：src_fld_en → (mdl_ch|None, mdl_en|None, note, confirmed)。"""
    norm = {}
    if path:
        with open(path, encoding="utf-8-sig") as f:
            for row in csv.reader(f, delimiter="\t"):
                if not row or not row[0].strip() or row[0].strip() == "src_fld_en":
                    continue
                r = (row + [""] * 5)[:5]
                src_en, ch, en, note, confirmed = (x.strip() for x in r)
                norm[src_en] = (ch or None, en or None, note or "", confirmed or "")
    return norm


def default_mdl_ch(src_ch, unknown_units):
    """引擎内置默认归一（机制层）：返回 (归一中文, 口径备注, 单位换算出的量词或"")。
    是否X（任意位置）→ X标志（"可/有/按"助词随是否一并剥离）；
    尾部单位括注：映射内单位换算度量词并入中文（份→份额、元→金额，万/亿前缀保留进备注），
    中文已含量度/价格词尾时不重复追加；映射外单位剥离进备注并记入 unknown_units 待团队约定。"""
    note, ch, unit_cls = "", src_ch, ""
    m = UNIT.search(ch)
    if m:
        u = m.group(1).strip()
        ch = ch[:m.start()]
        if ENUM_LIKE.match(u):  # 码值枚举口径，非单位
            note = u + "；"
        else:
            core = u.lstrip("万亿")
            prefix = u[:len(u) - len(core)]
            measure = UNIT_MEASURE.get(core)
            if measure:
                if not HAS_MEASURE.search(ch):
                    ch += measure
                    unit_cls = measure
                note = f"单位{u}；"
            elif core in KNOWN_UNITS or u in KNOWN_UNITS:
                note = f"单位{u}；"
            else:
                note = f"单位{u}；"
                unknown_units.add(u)
    if "是否" in ch:
        ch = ch.replace("是否", "")
        ch = re.sub(r"^[可有按]", "", ch)
        if not ch.endswith("标志"):
            ch += "标志"
    elif ch.endswith("日") and not ch.endswith("日期"):
        ch += "期"
    if ch.endswith("标的"):
        ch += "证券代码"
    ch = ch.replace("时规模", "规模")  # 上市时规模→上市规模：时点助词不参与命名
    return ch, note, unit_cls


def build_plan(args):
    if args.fields:
        flds, meta = parse_fields_tsv(args.fields)
        part_fld = None
        for k in ("src_ch", "src_en"):
            if not getattr(args, k) and meta.get(k):
                setattr(args, k, meta[k])
    else:
        flds, part_fld = parse_ddl(args.ddl)
        meta = {}
    for k in ("tbl_ch", "tbl_en", "src_ch", "src_en"):
        if not getattr(args, k):
            sys.exit(f"[输入] 缺少 --{k.replace('_', '-')}（源表名可由 4 列字段清单首行自动取，目标主数据表名须显式指定）")
    norm = load_norm(args.norm)
    unknown_units = set()
    plan = []
    for en, ch in flds:
        n_ch, n_en, n_note, _n_conf = norm.get(en, (None, None, "", ""))
        unit_cls = ""
        if en in STANDARD_MAP and n_ch is None and n_en is None:
            mdl_ch, mdl_en, note = STANDARD_MAP[en]
            plan.append({"src_en": en, "src_ch": ch, "mdl_ch": mdl_ch, "fixed_en": mdl_en,
                         "note": note, "unit_cls": "", "parsing": extract_mpc(mdl_ch)})
            continue
        base, unit_note, unit_cls = default_mdl_ch(ch, unknown_units)
        mdl_ch = n_ch or base
        note = "；".join(x for x in (n_note, unit_note) if x)
        mdl_en = n_en
        plan.append({"src_en": en, "src_ch": ch, "mdl_ch": mdl_ch, "fixed_en": mdl_en,
                     "note": note, "unit_cls": unit_cls,
                     "src_tail": (_m.group(1).lower()
                                  if (_m := SRC_TAIL_RE.search(en)) and en not in STANDARD_MAP else ""),
                     "parsing": extract_mpc(mdl_ch)})
    for mdl_ch, en, src_ch, src_en, note_tpl, mapping_tpl in INJECTED:
        plan.append({"src_en": src_en, "src_ch": src_ch, "mdl_ch": mdl_ch, "fixed_en": en,
                     "note": note_tpl.format(src_id=args.src_id),
                     "injected": mapping_tpl.format(src_id=args.src_id),
                     "unit_cls": "", "parsing": extract_mpc(mdl_ch)})
    if part_fld:
        p_en, p_ch = part_fld
        plan.append({"src_en": p_en, "src_ch": p_ch, "mdl_ch": p_ch, "fixed_en": p_en,
                     "note": "分区字段（标准字段）", "injected": f"{p_en}(分区)",
                     "unit_cls": "", "parsing": extract_mpc(p_ch)})
    return plan, flds, unknown_units


def validate(plan, flds, no_sec_id=False):
    errs = []
    tails = classifier_tails()
    by_en = {}
    for p in plan:
        by_en.setdefault(p["en"].lower(), []).append(p)
    for en_l, rows in by_en.items():
        if len(rows) > 1 and len({r["mdl_ch"] for r in rows}) > 1:
            errs.append(f"目标英文名重复且概念不同：{rows[0]['en']}（" +
                        " vs ".join(r["mdl_ch"] for r in rows) + "）")
    for p in plan:
        if p["src"] == "miss":
            errs.append(f"词根 miss：{p['mdl_ch']} → {p['roots_used']}")
        note = p.get("note") or ""
        approved = any(m in note for m in APPROVAL_NOTES)  # 用户审批留痕（超段/超长共用）
        if p["src"].startswith("needs-approval") and not approved:
            errs.append(f"超{MAX_WORDS}段需审批：{p['mdl_ch']} → {p['en']}；"
                        f"用户同意后在归一决策 TSV 的 note 写「已审批：<理由>」再跑")
        if len(p["en"]) > MAX_LEN and not approved:
            errs.append(f"超{MAX_LEN}位需审批（{len(p['en'])}位）：{p['mdl_ch']} → {p['en']}；"
                        f"用户同意后在归一决策 TSV 的 note 写「已审批：<理由>」再跑")
        tail = p["en"].split("_")[-1].lower()
        if "是否" in p["src_ch"] and not p["en"].endswith("_flag") and tail not in tails:
            errs.append(f"是否类未落 _flag：{p['src_ch']} → {p['en']}")
        if "代码" in p["src_ch"] and not p["en"].endswith("_cd"):
            errs.append(f"源中文带'代码'目标未带 _cd（§2.1-2）：{p['src_ch']} → {p['en']}")
        is_date = p["src_ch"].endswith("日期") or (p["src_ch"].endswith("日") and "时间" not in p["src_ch"])
        if is_date and not p["en"].endswith("_date") and tail not in tails:
            errs.append(f"日期类未落 _date：{p['src_ch']} → {p['en']}")
        if p["src_ch"] in ("记录修改时间", "记录创建时间") and not p["en"].endswith("_time"):
            errs.append(f"时间类未落 _time：{p['src_ch']} → {p['en']}")
    for p in plan:
        # 类词收尾（MPC Classifier）：标准字段与已声明特例豁免；比值字段 a_to_b 豁免
        if p["en"] in STD_EN or p.get("injected") or EXEMPT_NOTE in (p.get("note") or ""):
            continue
        tail = p["en"].split("_")[-1].lower()
        if "_to_" in p["en"] or tail in tails:
            continue
        errs.append(f"字段名缺类词（MPC Classifier）：{p['mdl_ch']} → {p['en']}（尾段 {tail} 不在类词表；"
                    f"如属规范特例，在归一决策 TSV 的 note 写「{EXEMPT_NOTE}：<理由>」）")
    std_needed = {"busi_date", "rec_no", "scr_id", "src_id", "rec_upd_time", "rec_down_time", "src_rec_no"}
    if no_sec_id:
        std_needed.discard("scr_id")  # 无证券维度的参照表（市场/日历类），见 --no-sec-id
    missing = std_needed - {p["en"] for p in plan}
    if missing:
        errs.append(f"标准字段缺失：{missing}")
    src_rows = [p for p in plan if not p.get("injected")]
    if {p["src_en"] for p in src_rows} != {e for e, _ in flds}:
        errs.append("源字段映射不完整（有遗漏或重复）")
    return errs


def write_outputs(plan, args, errs_count):
    os.makedirs(args.out, exist_ok=True)
    hdr = ["mdl_tbl_ch", "mdl_tbl_en", "mdl_fld_ch", "mdl_fld_en", "src_tbl_ch", "src_tbl_en",
           "src_fld_ch", "src_fld_en", "src_fld_type", "mapping", "src_id", "author", "opdate"]
    tsv_lines = ["\t".join(hdr)]
    for p in plan:
        mapping = p.get("injected") or p["src_en"]
        tsv_lines.append("\t".join([args.tbl_ch, args.tbl_en, p["mdl_ch"], p["en"], args.src_ch,
                                    args.src_en, p["src_ch"], p["src_en"] or "-", "string",
                                    mapping, args.src_id, args.author, args.opdate]))
    with open(os.path.join(args.out, f"{args.tbl_en}_mapping.tsv"), "w", encoding="utf-8", newline="") as f:
        f.write("\n".join(tsv_lines) + "\n")
    with open(os.path.join(args.out, "roots_used.tsv"), "w", encoding="utf-8", newline="") as f:
        f.write("mdl_fld_ch\tmdl_fld_en\t来源\t词根构成\n")
        for p in plan:
            f.write(f"{p['mdl_ch']}\t{p['en']}\t{p['src']}\t{p['roots_used']}\n")
    with open(os.path.join(args.out, "cache_input.tsv"), "w", encoding="utf-8", newline="") as f:
        for p in plan:
            f.write(f"{p['mdl_ch']}\t{p['en']}\n")
    with open(os.path.join(args.out, "01_目录.md"), "w", encoding="utf-8") as f:
        f.write(f"# 主数据模型目录 · {args.tbl_ch}\n\n")
        f.write(f"| 证券类型 | 主数据中文表名 | 主数据英文表名 | 源表 | 字段数 |\n|---|---|---|---|---|\n")
        f.write(f"| {args.domain} | {args.tbl_ch} | {args.tbl_en} | {args.src_en}（{args.src_id}） | {len(plan)} |\n\n")
        f.write("标准字段：busi_date / rec_no / scr_id / grp_id(分区) / src_id / src_rec_no(溯源) / "
                "rec_upd_time / rec_down_time（§4）。\n生成：model_builder.py 闭环（归一决策见 03 决策备注）。\n")
    usage, root_map = {}, {}
    for e in roots.load_roots():
        root_map[e["zh"]] = e
    for p in plan:
        for part in p["roots_used"].split("|"):
            m = re.match(r"(.+?)→(.+?)\((collocation|root|whitelist)\)", part)
            if m:
                usage[(m.group(1), m.group(2), m.group(3))] = usage.get((m.group(1), m.group(2), m.group(3)), 0) + 1
    with open(os.path.join(args.out, "02_基础词根圈定.md"), "w", encoding="utf-8") as f:
        f.write("# 基础词根圈定 · " + args.tbl_en + "\n\n")
        f.write("词根数据源 = MySQL md_word_roots（词根平台同步）+ fixed_collocations.csv（高优先级批次）。\n\n")
        f.write("| 词根中文 | 缩写 | 来源 | 使用次数 |\n|---|---|---|---|\n")
        for (seg, abbr, tag), n in sorted(usage.items(), key=lambda x: -x[1]):
            full = root_map.get(seg, {}).get("en", "")
            f.write(f"| {seg} | {abbr} | {tag}{'/' + full if full else ''} | {n} |\n")
    with open(os.path.join(args.out, "03_字段词根表.md"), "w", encoding="utf-8") as f:
        f.write("# 字段词根表 · " + args.tbl_en + "\n\n| 字段中文名 | 英文名 | 来源 | 词根构成 | 决策备注 |\n|---|---|---|---|---|\n")
        for p in plan:
            f.write(f"| {p['mdl_ch']} | {p['en']} | {p['src']} | {p['roots_used']} | {p['note'] or '-'} |\n")
    with open(os.path.join(args.out, "04_字段映射表.md"), "w", encoding="utf-8") as f:
        f.write(f"# 字段映射表 · dm_mast({args.src_id}) → 主数据 {args.tbl_en}\n\n")
        f.write("|" + "---|".join(hdr) + "|\n|" + "---|" * 13 + "\n")
        for line in tsv_lines[1:]:
            f.write("| " + " | ".join(line.split("\t")) + " |\n")
    enums = [(p["mdl_ch"], p["en"], p["src_ch"]) for p in plan if ENUM_HINT.search(p["src_ch"])]
    with open(os.path.join(args.out, "code_value_appendix.md"), "w", encoding="utf-8") as f:
        f.write("# 码值附录 · " + args.tbl_en + "（枚举字段）\n\n")
        f.write("以下码域经 `code_value.py lookup`（多域批量）查证未登记标准码值的，仅列源值不做硬映射，"
                "待治理组定标准值后 align。\n\n| 字段中文 | 英文名 | 源码值（源注释原文） | 状态 |\n|---|---|---|---|\n")
        for ch, en, src in enums:
            val = re.search(r"[（(](.+?)[)）]", src)
            f.write(f"| {ch} | {en} | {val.group(1) if val else src} | 码域未登记，待提案 |\n")
    print(f"交付物已落盘：{args.out}")
    print(f"  {args.tbl_en}_mapping.tsv（{len(tsv_lines)-1} 行）、roots_used.tsv、cache_input.tsv、01~04 MD、code_value_appendix.md（{len(enums)} 个枚举）")


def stamp_confirmed(path, by):
    """团队约定后给归一决策 TSV 的 confirmed 列盖章（只盖空行），原地重写并报告行数。"""
    rows, stamped = [], 0
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.reader(f, delimiter="\t"):
            if not row or not row[0].strip() or row[0].strip() == "src_fld_en":
                rows.append(row)
                continue
            row = (row + [""] * 5)[:5]
            if not row[4].strip():
                row[4] = f"{by} {datetime.date.today().isoformat()}"
                stamped += 1
            rows.append(row)
    head = ["src_fld_en", "mdl_fld_ch", "mdl_fld_en", "note", "confirmed"]
    if rows and rows[0] and rows[0][0].strip() == "src_fld_en":
        rows[0] = head  # 表头统一成 5 列（兼容旧 4 列文件）
    else:
        rows.insert(0, head)
    with open(path, "w", encoding="utf-8", newline="") as f:
        csv.writer(f, delimiter="\t", lineterminator="\n").writerows(rows)
    print(f"已盖章 confirmed：{stamped} 行 → {path}")
    return 0


def check_confirmed(flds, norm):
    """闸门用：返回未确认（不在 norm 或 confirmed 列空）的非标准源字段。"""
    return [en for en, _ch in flds
            if en not in STANDARD_MAP and not (norm.get(en) and norm[en][3])]


def apply_src_tail(plan):
    """源英文字段名尾 → 目标同类词尾（concept_mapping §2.1「源英文可判断按源」）：
    corp_id → co_id（标识类）、subordinate_flag → …_flag（指示类）；证券类 secu_id 由 STANDARD_MAP 给 scr_id。"""
    for p in plan:
        tail = p.pop("src_tail", "")
        if tail and "_" in p["en"] and not p["en"].endswith("_" + tail):
            p["en"] = p["en"].rsplit("_", 1)[0] + "_" + tail
            p["roots_used"] += f"|源英文 _{tail}→类词 {tail}(rule)"


def main():
    ap = argparse.ArgumentParser(description="通用主数据模型闭环生成器（判断与机制分离：norm TSV 只写判断）")
    ap.add_argument("--ddl", help="源表 DDL 文件路径（与 --fields 二选一）")
    ap.add_argument("--fields", help="字段清单 TSV（4 列 表中文/表英文/字段中文/源英文，或 2 列 字段中文/源英文）")
    ap.add_argument("--norm", help="归一决策 TSV（src_fld_en/mdl_fld_ch/mdl_fld_en/note/confirmed）；正式构建必填")
    ap.add_argument("--tbl-ch"); ap.add_argument("--tbl-en")
    ap.add_argument("--src-ch"); ap.add_argument("--src-en")
    ap.add_argument("--out")
    ap.add_argument("--domain", default=""); ap.add_argument("--src-id", default="VENDOR_A")
    ap.add_argument("--author", default="主数据建模")
    ap.add_argument("--opdate", default=datetime.date.today().strftime("%Y%m%d"))
    ap.add_argument("--preview", action="store_true",
                    help="第 1 步：产出 MPC 转义预览表交团队约定，不落盘交付物")
    ap.add_argument("--mark-confirmed", metavar="NORM_TSV", help="团队约定后盖章 confirmed 列（配合 --by）")
    ap.add_argument("--by", default="用户", help="确认人（写入 confirmed 列留痕）")
    ap.add_argument("--no-sec-id", action="store_true",
                    help="本表无证券维度（市场/日历/参数类参照表）：豁免 §4 的 scr_id 必备要求")
    args = ap.parse_args()
    if args.mark_confirmed:
        return stamp_confirmed(args.mark_confirmed, args.by)
    if not args.out:
        ap.error("--out 必填（交付物/预览落盘目录）")
    if bool(args.ddl) == bool(args.fields):
        ap.error("--ddl 与 --fields 必须且只能给一个（用户粘贴的字段清单用 --fields）")

    plan, flds, unknown_units = build_plan(args)
    if not args.preview:
        if not args.norm:
            ap.error("正式构建必须给 --norm（第 1 步 --preview 产出的归一决策.tsv，团队约定后传回）")
        unconfirmed = check_confirmed(flds, load_norm(args.norm))
        if unconfirmed:
            sys.exit("⛔ 用户尚未确认字段命名（confirmed 列空或字段未登记）：" + "、".join(unconfirmed) +
                     "\n   第 1 步先跑 --preview 出转义表交团队约定，确认后 --mark-confirmed 盖章再跑本步。")
    auto_names = [p["mdl_ch"] for p in plan if not p["fixed_en"]]
    translated = {zh: (en, src, ru) for zh, en, src, ru in roots.translate_names(auto_names)}
    for p in plan:
        if p["fixed_en"]:
            p["en"], p["src"], p["roots_used"] = p["fixed_en"], "fixed(标准/override)", "固定译名：" + p["fixed_en"]
        else:
            p["en"], p["src"], p["roots_used"] = translated[p["mdl_ch"]]
    apply_src_tail(plan)

    errs = validate(plan, flds, args.no_sec_id)
    if unknown_units:
        print("⚠️ 以下单位不在度量换算映射（UNIT_MEASURE）内，已剥离进备注——按口径需与团队约定换算词后"
              "加入 model_builder.py UNIT_MEASURE：")
        for u in sorted(unknown_units):
            print(f"  · {u}")

    if args.preview:
        # A1.5 交互闸门：MPC 逐词转义提取表（先全量执行、覆盖所有字段），团队约定后才进入映射/翻译落盘；
        # 映射阶段再结合缓存已有译名为用户推荐。
        os.makedirs(args.out, exist_ok=True)
        rows = ["源字段中文名\tModifier 修饰词提取\tPart 基词提取\tClassifier 类词提取\t转义后字段中文名"]
        for p in plan:
            mod, part, cls = p["parsing"]
            rows.append(f"{p['src_ch']}\t{mod}\t{part}\t{cls}\t{p['mdl_ch']}")
        preview = "\n".join(rows)
        print(preview)
        prev_path = os.path.join(args.out, "转义预览.tsv")
        norm_path = os.path.join(args.out, "归一决策.tsv")
        if os.path.exists(prev_path) or os.path.exists(norm_path):
            # 用户批注过/盖过章的文件不覆盖（那是本次建模的确认留痕）
            print(f"\n⚠️ {prev_path} 或 {norm_path} 已存在，本次预览不覆盖（防止冲掉用户批注）。"
                  f"\n   要重新出草稿：先移走/删除这两个文件再跑 --preview。")
            return 0
        with open(prev_path, "w", encoding="utf-8") as f:
            f.write(preview + "\n")
        # 归一决策.tsv 种子：转义后中文名 + 引擎建议英文名，confirmed 留空等团队约定后盖章
        with open(norm_path, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, delimiter="\t", lineterminator="\n")
            w.writerow(["src_fld_en", "mdl_fld_ch", "mdl_fld_en", "note", "confirmed"])
            for p in plan:
                if p["src_en"] in STANDARD_MAP or p.get("injected"):
                    continue
                w.writerow([p["src_en"], p["mdl_ch"], p["en"], p["note"], ""])
        print(f"\n[预览模式] 转义提取表已写出（{len(rows)-1} 字段，空位=该成分无法提取，允许 M+C/P+C 组合）："
              f"{os.path.join(args.out, '转义预览.tsv')}")
        print(f"[预览模式] 归一决策种子（团队约定/批注后再动它）：{norm_path}")
        print("[预览模式] ⛔ 到此停下，把转义表交团队约定；确认后 --mark-confirmed 盖章，再跑正式构建（去掉 --preview）。")
        return 0

    print(f"校验：源字段 {len(flds)}，映射行 {len(plan)}；错误 {len(errs)}")
    for e in errs:
        print("  ❌ " + e)
    if errs:
        sys.exit("校验未通过，不落盘。")
    write_outputs(plan, args, len(errs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
