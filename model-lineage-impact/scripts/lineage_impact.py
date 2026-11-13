#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
模型依赖血缘影响排查（表级 + 字段级）。

输入：调度号（任务 ID）或模型表名。流程：定位锚点任务 → 下游依赖任务（默认 1 层）
→ 逐个取下游任务的 ETL SQL（磁盘缓存）→ 解析表引用 / 字段引用 / src_id 分区过滤
→ 评估字段改动影响 → 落盘 TSV。

用法：
    python lineage_impact.py --task 1001 --depth 1 --field mkt_cn_name
    python lineage_impact.py --table dm_demo_security --depth 1 --field mkt_cn_name --src-id VENDOR_A

产出（--out 目录，默认 output/<目标>_<日期>/）：
    impact.json            结构化结果（供人工核对 / 重算）
    1_downstream_tasks.tsv 下游任务清单（表级：是否引用目标表 / 字段 / 分区 / 结论）
    2_field_impact.tsv     字段级影响矩阵（含"下游引用了、目标表却没有"的字段）
    3_reference_detail.tsv 引用明细（一条证据一行：任务 / 语句 / 字段 / 下游落地字段 / 片段）
    cache/                 MCP 原始响应缓存（重跑不重复请求）

效率约定：所有 MCP 调用串行 + 磁盘缓存，不并发；调用次数在 stdout 明确打印。
"""
import argparse
import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "_shared"))
from datadev_adapters import AdapterError, adapter_from_env

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # skill 根目录（.env 所在）

# ------------------------------------------------------------------ 文本工具

RE_LINE_COMMENT = re.compile(r"--[^\n]*")
RE_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
RE_COL_REF = re.compile(r"\b([A-Za-z_]\w*)\s*\.\s*([A-Za-z_]\w*)\b")
RE_INSERT_HEAD = re.compile(
    r"insert\s+(?:overwrite\s+|into\s+)?table\s+([\w.]+)\s*(?:partition\s*\(([^)]*)\))?", re.I)
RE_CREATE_HEAD = re.compile(
    r"create\s+(?:temporary\s+)?table\s+(?:if\s+not\s+exists\s+)?([\w.]+)", re.I)
RE_SRC_ID = re.compile(r"src_id\s*=\s*'([^']*)'", re.I)
RE_ITEM = re.compile(r"^(?:,\s*)?(.*?)\s+as\s+([A-Za-z_][\w]*)\s*(?:--(.*))?$", re.S | re.I)
RE_SELECT_HEAD = re.compile(r"\bselect\b", re.I)
RE_WORD = re.compile(r"(?<![\w.])([A-Za-z_]\w*)(?![\w])")
RE_STOP_ALIAS = {
    "where", "on", "and", "or", "join", "left", "right", "inner", "outer", "full",
    "group", "order", "by", "when", "then", "else", "end", "as", "union", "select",
    "from", "partition", "case", "set", "values", "having", "limit", "cluster",
    "distribute", "sort", "lateral", "view", "tablesample", "cross", "semi",
}
TMP_HINT = re.compile(r"(^|[_.])tmp([_.]|$)|_tmp$|^tmp_", re.I)


def strip_comments(sql):
    """去掉 SQL 注释（结构解析用；证据片段仍取原文）。"""
    return RE_LINE_COMMENT.sub(" ", RE_BLOCK_COMMENT.sub(" ", sql))


def short_table(name):
    """库.表 → 表名（小写、去引号）。"""
    return (name or "").split(".")[-1].strip().strip("`;").lower()


def one_line(text, width=300):
    s = re.sub(r"\s+", " ", (text or "")).strip()
    return s[:width] + ("…" if len(s) > width else "")


def has_token(text, token):
    """text 中是否出现独立标识符 token（排除 a.token 形式）。"""
    return bool(re.search(r"(?<![\w.])" + re.escape(token) + r"(?![\w])", text, re.I))


def resolve_aliases(sql):
    """from/join 子句 → ({别名: {db, table}}, 冲突别名集合)。

    子查询整体消费、内部别名不外泄；同名别名指向不同表时记入冲突集（判定降级为"疑似"）。
    """
    aliases, conflicts, consumed = {}, set(), []

    def in_consumed(pos):
        return any(s <= pos < e for s, e in consumed)

    for m in re.finditer(r"(?:from|join)\s*\(", sql, re.I):
        start = m.end() - 1
        if in_consumed(start):
            continue
        depth, k = 0, start
        while k < len(sql):
            if sql[k] == "(":
                depth += 1
            elif sql[k] == ")":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        consumed.append((start, k))
        am = re.match(r"\s*(?:as\s+)?([A-Za-z_]\w*)", sql[k + 1:])
        if not am:
            continue
        alias = am.group(1).lower()
        if alias in RE_STOP_ALIAS:
            continue
        inner = sql[start + 1:k]
        tm = re.search(r"from\s+([A-Za-z_]\w*)\.([A-Za-z_]\w*)", inner, re.I) \
            or re.search(r"from\s+([A-Za-z_]\w*)", inner, re.I)
        if not tm:
            continue
        ref = ({"db": tm.group(1).lower(), "table": tm.group(2).lower()}
               if tm.lastindex == 2 else {"db": "", "table": tm.group(1).lower()})
        # 子查询别名：记下它产出的列（空 = select * 透传），用于区分"真取目标表字段"与"子查询加工出来的列"
        ref["via"] = "subquery"
        ref["items"] = {it["field"]: it["expr"] for it in select_list_of(inner)}
        _bind(aliases, conflicts, alias, ref)

    for m in re.finditer(
            r"(?:from|join)\s+([A-Za-z_]\w*)(?:\.([A-Za-z_]\w*))?"
            r"(?:\s+(?:as\s+)?([A-Za-z_]\w*))?(?=[\s,;)]|$)", sql, re.I):
        if in_consumed(m.start()):
            continue
        tbl = (m.group(2) or m.group(1)).lower()
        db = (m.group(1) or "").lower() if m.group(2) else ""
        alias = (m.group(3) or "").lower()
        if alias in RE_STOP_ALIAS:
            alias = ""
        _bind(aliases, conflicts, alias or tbl, {"db": db, "table": tbl, "via": "table"})
    return aliases, conflicts


def _bind(aliases, conflicts, alias, ref):
    cur = aliases.get(alias)
    if cur is None:
        aliases[alias] = ref
    elif cur.get("table") != ref.get("table"):
        conflicts.add(alias)


def _split_select_items(select_text):
    """select 列表 → [{expr, field, comment}]（按括号深度 0 的逗号切分）。"""
    pieces, buf, depth = [], [], 0
    for ch in select_text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            pieces.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        pieces.append("".join(buf))
    out = []
    for raw in pieces:
        raw = re.sub(r"^\s*select\s+", "", raw.strip(), flags=re.I)
        raw = re.sub(r"^\s*distinct\s+", "", raw, flags=re.I)
        raw = re.sub(r"^\s*(--[^\n]*\n?)+", "", raw)
        if not raw.strip():
            continue
        m = RE_ITEM.match(raw)
        if not m:
            # Hive 允许省略 as（`表达式 别名`）；末 token 是关键字说明它其实没有别名
            m2 = re.match(r"^(.*\S)\s+([A-Za-z_]\w*)\s*(?:--(.*))?$", raw, re.S)
            if m2 and m2.group(2).lower() not in RE_STOP_ALIAS:
                out.append({"expr": re.sub(r"\s+", " ", m2.group(1)).strip(),
                            "field": m2.group(2).lower(),
                            "comment": (m2.group(3) or "").strip()})
            continue
        out.append({"expr": re.sub(r"\s+", " ", m.group(1)).strip(),
                    "field": m.group(2).lower(), "comment": (m.group(3) or "").strip()})
    return out


def select_list_of(text):
    """文本里第一个 select 列表的项（用于看子查询产出了哪些列）。"""
    m = RE_SELECT_HEAD.search(text)
    if not m:
        return []
    depth, k, n = 0, m.end(), len(text)
    while k < n:
        ch = text[k]
        if ch == "(":
            depth += 1
        elif ch == ")":
            if depth == 0:
                break
            depth -= 1
        elif depth == 0 and text[k:k + 4].lower() == "from" and text[k + 4] in " \t\n\r(":
            break
        k += 1
    return _split_select_items(text[m.end():k])


def select_blocks(sql):
    """按 select 块切分 → [{sel, fromText, block, tables}]。

    tables = 该 select 块的 from/join 子句引用的表名集合（含子查询别名解析后的来源表），
    用于判断"裸列名到底属不属于目标表所在的这个块"。
    """
    out, n = [], len(sql)
    for m in RE_SELECT_HEAD.finditer(sql):
        depth, k = 0, m.end()
        while k < n:
            ch = sql[k]
            if ch == "(":
                depth += 1
            elif ch == ")":
                if depth == 0:
                    break
                depth -= 1
            elif depth == 0 and sql[k:k + 4].lower() == "from" and sql[k + 4] in " \t\n\r(":
                break
            k += 1
        d0 = sql.count("(", 0, m.start()) - sql.count(")", 0, m.start())
        end, d, j = n, d0, k
        while j < n:
            if sql[j] == "(":
                d += 1
            elif sql[j] == ")":
                if d == d0:
                    end = j
                    break
                d -= 1
            elif sql[j] == ";" and d == d0:
                end = j
                break
            j += 1
        aliases, _ = resolve_aliases(sql[k:end])
        out.append({"sel": sql[m.end():k], "fromText": sql[k:end], "block": sql[m.start():end],
                    "tables": {ref["table"] for ref in aliases.values()}})
    return out


def where_fragments(sql):
    """where 子句片段（用于捕捉过滤条件里的字段 / 分区）。"""
    pat = re.compile(
        r"\bwhere\b(.*?)(?=\bgroup\s+by\b|\border\s+by\b|\bhaving\b|\bunion\b|\bcluster\s+by\b"
        r"|\bselect\b|\binsert\b|\bfrom\b|\blimit\b|$)",
        re.I | re.S)
    return [re.sub(r"\s+", " ", m.group(1)).strip() for m in pat.finditer(sql)]


def anchor_table(sql_statements):
    """锚点任务写出的目标表：优先最后一个非 TMP 的 insert 目标，其次 create table。"""
    inserts, creates = [], []
    for s in sql_statements:
        clean = strip_comments(s)
        inserts += [m.group(1).lower() for m in RE_INSERT_HEAD.finditer(clean)]
        creates += [m.group(1).lower() for m in RE_CREATE_HEAD.finditer(clean)]
    for name in reversed(inserts):
        if not TMP_HINT.search(short_table(name)):
            return name
    return inserts[-1] if inserts else (creates[-1] if creates else None)


def anchor_src_ids(sql_statements):
    """锚点任务写入的分区 src_id（如 DC）。"""
    found = []
    for s in sql_statements:
        for m in RE_INSERT_HEAD.finditer(strip_comments(s)):
            if m.group(2):
                found += RE_SRC_ID.findall(m.group(2))
    return sorted(set(found))


def ddl_columns(sql_statements, table_name=None):
    """从语句里的 create table (...) 提取字段清单（元数据不可用/不一致时为准）。"""
    for s in sql_statements:
        head = RE_CREATE_HEAD.search(strip_comments(s))
        if not head:
            continue
        if table_name and short_table(head.group(1)) != table_name:
            continue
        rest = s[head.end():]
        open_idx = rest.find("(")
        if open_idx < 0:
            continue
        body = rest[open_idx + 1:]
        depth, buf = 1, []
        for ch in body:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    break
            if depth == 1:
                buf.append(ch)
        cols = []
        for line in "".join(buf).splitlines():
            m = re.match(r"^\s*,?\s*([A-Za-z_]\w*)\s+(?!by\b)([A-Za-z]+)", line)
            if m:
                cm = re.search(r"comment\s+'([^']*)'", line)
                cols.append({"name": m.group(1).lower(), "comment": cm.group(1) if cm else ""})
        if cols:
            return cols
    return []


# -------------------------------------------------------------------- 引用解析

def statement_refs(sql, target_table, field_scope):
    """解析单条语句对目标表的引用。

    返回 {tables, targetAliases, suspectAliases, srcIds, hits}，
    hits = {列名: [{"kind": 限定/疑似, "downField", "expr", "where"}]}。
    kind=限定：`别名.列` 且该别名确实绑定到目标表（高置信）。
    kind=疑似：裸列名 / 别名作用域冲突（需人工确认）。
    field_scope：裸列名只在该集合内记录（避免把其它表的同名字段算进来）。
    """
    clean = strip_comments(sql)
    aliases, conflicts = resolve_aliases(clean)
    target_aliases, suspect_aliases = set(), set()
    for alias, ref in aliases.items():
        if ref["table"] != target_table:
            continue
        (suspect_aliases if alias in conflicts else target_aliases).add(alias)
    hits = {}

    def kind_of(alias, col):
        """别名.列 的置信度：直接表引用=限定；子查询别名看它是否透传该列。"""
        if alias in conflicts:
            return "疑似"
        ref = aliases[alias]
        if ref.get("via") == "subquery":
            items = ref.get("items") or {}
            if not items:                       # select * 透传
                return "限定"
            expr = (items.get(col) or "").strip()
            if not expr:
                return "疑似"                    # 该列不是这个子查询产出的
            if re.fullmatch(r"[A-Za-z_]\w*", expr) and expr.lower() == col:
                return "限定"
            m = re.fullmatch(r"([A-Za-z_]\w*)\s*\.\s*([A-Za-z_]\w*)", expr)
            if m and m.group(2).lower() == col and \
                    (aliases.get(m.group(1).lower()) or {}).get("table") == target_table:
                return "限定"
            return "疑似"                        # 加工/改名出来的列
        return "限定"

    def add(col, kind, down_field, expr, where=""):
        col = col.lower()
        hit = {"kind": kind, "downField": down_field, "expr": one_line(expr), "where": where}
        if hit not in hits.setdefault(col, []):
            hits[col].append(hit)

    # 按 select 块判定：裸列名只在该块的 from 子句确实读目标表时才算疑似命中，
    # 避免把其它表的同名列（busi_date/remark 之类）算成目标表字段。
    for blk in select_blocks(clean):
        in_target_block = target_table in blk["tables"]
        # 该 select 块只有目标表一个数据源 → 裸列名无歧义，直接判"限定"
        single_target = blk["tables"] == {target_table}
        for it in _split_select_items(blk["sel"]):
            found = {}
            for m in RE_COL_REF.finditer(it["expr"]):
                alias, cn = m.group(1).lower(), m.group(2).lower()
                if alias in target_aliases:
                    found[cn] = kind_of(alias, cn)
                elif alias in suspect_aliases:
                    found.setdefault(cn, "疑似")
            for m in (RE_WORD.finditer(it["expr"]) if in_target_block else ()):
                tok = m.group(1).lower()
                if tok in found or tok not in field_scope:
                    continue
                found.setdefault(tok, "限定" if single_target else "疑似")
            for cn, kind in found.items():
                add(cn, kind, it["field"], it["expr"])
            # 下游把某字段落成同名别名（如 mkt_ch_name as mkt_cn_name）——与"引用"区分开
            if it["field"] in field_scope:
                add(it["field"], "同名", it["field"], "%s as %s" % (it["expr"], it["field"]))
        if not in_target_block:
            continue
        for frag in where_fragments(blk["block"]):
            for m in RE_COL_REF.finditer(frag):
                alias, cn = m.group(1).lower(), m.group(2).lower()
                if alias in target_aliases:
                    add(cn, kind_of(alias, cn), "", "where " + frag, where=frag)
            for m in RE_WORD.finditer(frag):
                tok = m.group(1).lower()
                if tok in field_scope:
                    add(tok, "限定" if single_target else "疑似", "", "where " + frag, where=frag)

    return {"tables": {ref["table"] for ref in aliases.values()},
            "targetAliases": sorted(target_aliases), "suspectAliases": sorted(suspect_aliases),
            "srcIds": sorted(set(RE_SRC_ID.findall(clean))), "hits": hits}


# -------------------------------------------------------------------- 采集器

class Collector:
    """串行 + 磁盘缓存的元数据采集器（不并发，重跑不重复请求）。"""

    def __init__(self, outdir, adapter=None, adapter_config=None):
        self.c = adapter or adapter_from_env(path=adapter_config, skill_root=BASE)
        self.cache = os.path.join(outdir, "cache")
        os.makedirs(self.cache, exist_ok=True)
        self.errors = []
        self.calls = {"hit": 0, "miss": 0}

    def _cache_path(self, key):
        return os.path.join(self.cache, re.sub(r"[^\w.-]", "_", key) + ".json")

    def _cached(self, operation, key, fetch):
        path = self._cache_path("%s.%s" % (operation, key))
        if os.path.exists(path):
            self.calls["hit"] += 1
            with open(path, encoding="utf-8") as handle:
                return json.load(handle)
        try:
            data = fetch()
        except AdapterError as exc:
            self.errors.append({"operation": operation, "key": key, "error": str(exc)})
            return None
        self.calls["miss"] += 1
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=1)
        return data

    def task_detail(self, task_id, enabled=True):
        """调度任务详情（任务名等）；enabled=False 时不发请求。"""
        if not enabled:
            return {}
        return self._cached("get_task_detail", str(task_id),
                            lambda: self.c.get_task_detail(task_id)) or {}

    def task_sql(self, task_id):
        data = self._cached("get_task_sql", str(task_id),
                            lambda: self.c.get_task_sql(task_id)) or []
        return [s for s in data if isinstance(s, str)]

    def downstream(self, task_id, depth):
        data = self._cached(
            "get_task_dependencies", "DOWNSTREAM.%s.%s" % (depth, task_id),
            lambda: self.c.get_task_dependencies(task_id, "DOWNSTREAM", depth)) or []
        return [str(i) for i in data]

    def table_meta(self, table_name):
        """表名 → {guid, name, db, comment, columns, tasks}（元数据精确匹配）。"""
        rows = self._cached("search_table", table_name,
                            lambda: self.c.search_table(table_name)) or []
        exact = [r for r in rows
                 if (r.get("name") or "").lower() == table_name.lower()]
        if not exact:
            return None
        guid = exact[0].get("guid")
        data = self._cached("get_table", guid, lambda: self.c.get_table(guid)) or {}
        return {"guid": guid, "name": data.get("name") or table_name,
                "db": data.get("db") or "", "comment": data.get("comment") or "",
                "qualifiedName": data.get("qualifiedName") or "",
                "columns": data.get("columns") or [],
                "tasks": [{"taskId": t["taskId"], "taskName": t.get("taskName") or ""}
                          for t in (data.get("tasks") or [])]}


# -------------------------------------------------------------------- 主分析

CONCLUSION = {"hit": "命中(字段引用)", "suspect": "疑似(名称命中)",
              "table": "仅引用目标表", "none": "不涉及"}


def analyze(task_id=None, table=None, depth=1, fields=None, src_id="auto",
            max_tasks=120, with_names=False, use_meta=True, outdir=None,
            adapter_config=None):
    label = ("task-%s" % task_id) if task_id else table
    outdir = outdir or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output",
        "%s_%s" % (re.sub(r"[^\w.-]", "_", label), datetime.date.today().strftime("%Y%m%d")))
    os.makedirs(outdir, exist_ok=True)
    col = Collector(outdir, adapter_config=adapter_config)

    # ---- Step 1 锚点任务 ----
    notes, anchors = [], []
    target_table = short_table(table) if table else None
    if task_id:
        detail = col.task_detail(task_id)
        anchors.append({"taskId": str(task_id), "taskName": detail.get("taskName") or ""})
        notes.append("锚点任务 %s（%s）" % (task_id, detail.get("taskName") or "名称未知"))
    table_meta = None
    if table:
        if use_meta:
            table_meta = col.table_meta(target_table)
            if table_meta is None:
                notes.append("元数据未精确匹配到表 %s" % table)
        if table_meta and table_meta.get("tasks"):
            anchors = list(table_meta["tasks"])
            notes.append("锚点任务 = 该表 horaeTasks 共 %d 个" % len(anchors))
    if not anchors:
        raise SystemExit("未能定位锚点任务：请提供 --task <调度任务ID> 或可查到 horaeTasks 的 --table")

    # ---- Step 2 目标表 / src_id / 字段清单 ----
    anchor_sql = {}
    if task_id:
        anchor_sql[str(task_id)] = col.task_sql(task_id)
        if not target_table:
            target_table = short_table(anchor_table(anchor_sql[str(task_id)]))
            notes.append("由锚点任务 SQL 推断写出表：%s" % (target_table or "未知"))
        if target_table and use_meta and table_meta is None:
            table_meta = col.table_meta(target_table)
            if table_meta is None:
                notes.append("元数据未精确匹配到表 %s，字段清单回退到 SQL DDL" % target_table)
    elif anchors:
        probe = anchors[0]["taskId"]
        anchor_sql[probe] = col.task_sql(probe)
        notes.append("取锚点任务 %s 的 SQL 推断写入表 / 分区 / 字段（样本）" % probe)
    if not target_table:
        raise SystemExit("无法确定目标表：请显式传 --table <表名>")

    tgt = {"table": target_table, "db": "", "comment": "", "columns": [], "guid": None,
           "columnsFrom": ""}
    if table_meta:
        tgt.update({k: table_meta[k] for k in ("guid", "db", "comment", "columns")})
        tgt["name"] = table_meta.get("name")
        tgt["columnsFrom"] = "元数据"
        notes.append("元数据匹配表：%s（%d 列%s）" % (
            table_meta.get("qualifiedName") or table_meta.get("name"),
            len(tgt["columns"]),
            ("，" + table_meta["comment"]) if table_meta.get("comment") else ""))
    for stmts in anchor_sql.values():
        cols = ddl_columns(stmts, target_table)
        if not cols:
            continue
        if tgt["columns"] and len(cols) != len(tgt["columns"]):
            notes.append("任务 DDL %d 列 ≠ 元数据 %d 列，按任务 DDL 为准（元数据可能匹配到同名异表）"
                         % (len(cols), len(tgt["columns"])))
        tgt["columns"], tgt["columnsFrom"] = cols, "锚点任务 DDL"
        break
    if not tgt["columns"]:
        notes.append("未取得字段清单（无 DDL / 元数据未命中）：字段级判定按显式 --field 兜底")

    auto_src = sorted({s for stmts in anchor_sql.values() for s in anchor_src_ids(stmts)})
    if src_id == "auto":
        src_id = auto_src[0] if len(auto_src) == 1 else ""
    if auto_src:
        notes.append("锚点任务写入分区 src_id=%s" % "/".join(auto_src))
    want_src = src_id if src_id else ""

    # ---- Step 3 下游任务 ----
    anchor_ids = {a["taskId"] for a in anchors}
    downstream_ids = []
    for a in anchors:
        for did in col.downstream(a["taskId"], depth):
            if did not in anchor_ids and did not in downstream_ids:
                downstream_ids.append(did)
    if len(downstream_ids) > max_tasks:
        print("[停止] 下游任务 %d 个，超过 --max-tasks %d；确认范围后调大该值再跑，避免大量调用。"
              % (len(downstream_ids), max_tasks))
        raise SystemExit(2)

    # ---- Step 4 逐任务取 SQL 并解析（串行 + 缓存）----
    scan_fields = [f.lower() for f in (fields or [])] or \
        [c["name"] for c in tgt["columns"] if c["name"]]
    table_cols = {c["name"] for c in tgt["columns"] if c["name"]}
    field_scope = set(scan_fields) | table_cols
    tasks_out, detail_rows = [], []
    for tid in downstream_ids:
        statements = col.task_sql(tid)
        row = {"taskId": tid, "taskName": col.task_detail(tid, enabled=with_names).get("taskName") or "",
               "writes": anchor_table(statements) or "", "statements": len(statements),
               "refsTarget": False, "targetAliases": set(), "srcIds": [], "hits": {},
               "conclusion": CONCLUSION["none"]}
        for idx, stmt in enumerate(statements, 1):
            refs = statement_refs(stmt, target_table, field_scope)
            if target_table in refs["tables"]:
                row["refsTarget"] = True
                row["targetAliases"] |= set(refs["targetAliases"])
            row["srcIds"] = sorted(set(row["srcIds"]) | set(refs["srcIds"]))
            for cn, hits in refs["hits"].items():
                for h in hits:
                    if h in row["hits"].setdefault(cn, []):
                        continue
                    row["hits"][cn].append(h)
                    detail_rows.append({
                        "taskId": tid, "taskName": row["taskName"], "stmt": idx,
                        "where": ("where" if h["where"] else
                                  ("输出别名" if h["kind"] == "同名" else "select")),
                        "kind": h["kind"], "table": target_table,
                        "alias": ",".join(refs["targetAliases"] or refs["suspectAliases"]),
                        "field": cn, "downField": h["downField"], "expr": h["expr"],
                        "srcIds": ",".join(refs["srcIds"]),
                        "inScope": "是" if cn in scan_fields else "否"})
        if row["hits"]:
            row["conclusion"] = CONCLUSION["hit"] if any(
                h["kind"] == "限定" for hs in row["hits"].values() for h in hs) \
                else CONCLUSION["suspect"]
        elif row["refsTarget"]:
            row["conclusion"] = CONCLUSION["table"]
        tasks_out.append(row)

    # ---- Step 5 字段级汇总 ----
    col_by_name = {c["name"]: c for c in tgt["columns"]}
    hit_cols = {c for r in tasks_out for c in r["hits"]}
    unknown = sorted({c for r in tasks_out for c in r["hits"]
                      if c not in col_by_name
                      and any(h["kind"] == "限定" for h in r["hits"][c])})
    field_rows = []
    for f in list(dict.fromkeys(scan_fields + sorted(hit_cols - set(scan_fields)))):
        users = [r for r in tasks_out if f in r["hits"]]
        by = {k: [r["taskId"] for r in users if any(h["kind"] == k for h in r["hits"][f])]
              for k in ("限定", "疑似", "同名")}
        ref_ids = by["限定"] + by["疑似"]
        field_rows.append({
            "field": f, "comment": (col_by_name.get(f) or {}).get("comment") or "",
            "inTable": f in col_by_name,
            "kind": next((k for k in ("限定", "疑似", "同名") if by[k]), "无"),
            "qual": by["限定"], "susp": by["疑似"], "same": by["同名"], "tasks": ref_ids,
            "taskNames": [r["taskName"] or r["writes"] or "" for r in users
                          if r["taskId"] in ref_ids],
            "downFields": sorted({h["downField"] for r in users for h in r["hits"][f] if h["downField"]}),
            "srcIds": sorted({s for r in users for s in r["srcIds"]}),
            "example": next((h["expr"] for r in users for h in r["hits"][f]), "")})

    # ---- Step 6 影响范围小结（给用户"心里有底"用的数字）----
    scan_set = set(scan_fields)
    def scan_kinds(row):
        return {h["kind"] for f, hs in row["hits"].items() if f in scan_set for h in hs}
    hit_tasks = [r for r in tasks_out if scan_kinds(r) & {"限定", "疑似"}]
    suspect_tasks = [r for r in hit_tasks if "限定" not in scan_kinds(r)]
    same_tasks = [r for r in tasks_out if "同名" in scan_kinds(r) and r not in hit_tasks]
    referenced = sorted({c for r in tasks_out for c in r["hits"]
                         if any(h["kind"] == "限定" for h in r["hits"][c])})
    tables = sorted({r["writes"] for r in tasks_out if r["refsTarget"] and r["writes"]})
    scope = {
        "downstreamTasks": len(downstream_ids),
        "refTargetTasks": sum(1 for r in tasks_out if r["refsTarget"]),
        "hitTasks": [r["taskId"] for r in hit_tasks],
        "suspectTasks": [r["taskId"] for r in suspect_tasks],
        "sameNameTasks": [r["taskId"] for r in same_tasks],
        "scanFields": scan_fields,
        "hitFields": [f["field"] for f in field_rows
                      if f["field"] in scan_set and f["kind"] in ("限定", "疑似")],
        "targetColumns": len(tgt["columns"]),
        "referencedFields": referenced,
        "unknownColumns": unknown,
        "downstreamTables": tables,
        "downstreamTableCount": len(tables),
    }

    result = {
        "input": {"taskId": task_id, "table": table, "depth": depth,
                  "fields": fields or [], "srcId": src_id},
        "anchors": anchors, "anchorNotes": notes,
        "target": {"table": target_table, "db": tgt.get("db"), "comment": tgt.get("comment"),
                   "guid": tgt.get("guid"), "srcIds": auto_src, "columns": tgt["columns"],
                   "columnsFrom": tgt["columnsFrom"], "scanFields": scan_fields},
        "downstream": [{**r, "targetAliases": sorted(r["targetAliases"])} for r in tasks_out],
        "fields": field_rows, "details": detail_rows,
        "scope": scope,
        "stats": {"downstreamTasks": len(downstream_ids),
                  "refTarget": scope["refTargetTasks"],
                  "hitField": sum(1 for r in tasks_out if r["hits"]),
                  "apiMiss": col.calls["miss"], "cacheHit": col.calls["hit"],
                  "referencedColumns": referenced,
                  "unknownColumns": unknown},
        "errors": col.errors,
    }
    with open(os.path.join(outdir, "impact.json"), "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1)
    return result, outdir, want_src


# -------------------------------------------------------------------- TSV 输出

def write_tsv(path, header, rows):
    def cell(v):
        return re.sub(r"[\t\r\n]+", " ", str(v if v is not None else "")).strip()
    lines = ["\t".join(header)] + ["\t".join(cell(v) for v in r) for r in rows]
    with open(path, "w", encoding="utf-8-sig", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    return path


def build_tsv(result, outdir):
    tasks = result["downstream"]
    scan = set(result["target"]["scanFields"])
    sc = result["scope"]

    rows0 = [
        ["下游任务数", sc["downstreamTasks"], "本次扫描范围（%d 层直接下游）" % result["input"]["depth"]],
        ["引用目标表的下游任务数", sc["refTargetTasks"], "SQL 里真的 from/join 了目标表"],
        ["命中本次排查字段的任务数", len(sc["hitTasks"]),
         "限定或疑似（不含仅同名）；%s" % ",".join(sc["hitTasks"])],
        ["其中限定命中(必然波及)的任务数",
         len([t for t in sc["hitTasks"] if t not in set(sc["suspectTasks"])]), "别名直连目标表 / select * 透传"],
        ["疑似(需人工确认)任务数", len(sc["suspectTasks"]), ",".join(sc["suspectTasks"])],
        ["仅同名(下游自己产出同名字段)任务数", len(sc["sameNameTasks"]), ",".join(sc["sameNameTasks"])],
        ["本次排查字段数", len(sc["scanFields"]), ",".join(sc["scanFields"])],
        ["命中字段数", len(sc["hitFields"]), ",".join(sc["hitFields"])],
        ["目标表列数", sc["targetColumns"], "%s（清单来源：%s）" % (
            result["target"]["table"], result["target"]["columnsFrom"] or "无")],
        ["下游引用的目标表列数", len(sc["referencedFields"]), ",".join(sc["referencedFields"])],
        ["涉及下游表(模型/主题表)数", sc["downstreamTableCount"], "%s" % ";".join(sc["downstreamTables"])],
        ["下游引用了、目标表没有的字段", len(sc["unknownColumns"]), ",".join(sc["unknownColumns"])],
    ]
    p0 = write_tsv(os.path.join(outdir, "0_scope.tsv"), ["指标", "数值", "说明"], rows0)

    rows1 = []
    for r in tasks:
        km = {"限定": [], "疑似": [], "同名": []}
        for f, hs in r["hits"].items():
            for h in hs:
                if f not in km[h["kind"]]:
                    km[h["kind"]].append(f)
        evid = " | ".join(one_line(r["hits"][f][0]["expr"], 160) for f in r["hits"])
        in_scan = set(r["hits"]) & scan
        kinds = {h["kind"] for f in in_scan for h in r["hits"][f]}
        if "限定" in kinds:
            concl = CONCLUSION["hit"]                  # 真读了本次排查字段
        elif "疑似" in kinds:
            concl = CONCLUSION["suspect"]
        elif "同名" in kinds:
            concl = "同名(下游同名字段)"
        elif r["hits"]:
            concl = "命中(其它字段)"                    # 引用了目标表，但不涉及本次排查字段
        else:
            concl = r["conclusion"]
        rows1.append([1, r["taskId"], r["taskName"] or "", r["writes"],
                      "是" if r["refsTarget"] else "否", ",".join(r["targetAliases"]),
                      " / ".join("%s: %s" % (k, ",".join(v)) for k, v in km.items() if v),
                      ",".join(r["srcIds"]),
                      concl, evid])
    p1 = write_tsv(os.path.join(outdir, "1_downstream_tasks.tsv"),
                   ["层级", "任务ID", "任务名", "写入表(推断)", "引用目标表", "目标表别名",
                    "引用目标字段", "src_id分区", "结论", "证据SQL片段"], rows1)

    rows2 = [[f["field"], f["comment"], "是" if f["inTable"] else "否", f["kind"],
              len(f["qual"]), len(f["susp"]), len(f["same"]),
              ",".join(f["tasks"]), ",".join(f["same"]),
              ",".join(f["downFields"]), ",".join(f["srcIds"]), one_line(f["example"], 200)]
             for f in result["fields"]]
    p2 = write_tsv(os.path.join(outdir, "2_field_impact.tsv"),
                   ["字段英文名", "字段中文名", "在目标表中", "引用方式", "限定任务数", "疑似任务数",
                    "同名任务数", "引用任务ID(限定/疑似)", "同名任务ID", "下游落地字段",
                    "src_id分区", "示例SQL片段"], rows2)

    rows3 = [[d["taskId"], d["taskName"], d["stmt"], d["where"], d["kind"], d["table"], d["alias"],
              d["field"], d["downField"], one_line(d["expr"], 400), d["srcIds"], d["inScope"]]
             for d in result["details"]]
    p3 = write_tsv(os.path.join(outdir, "3_reference_detail.tsv"),
                   ["任务ID", "任务名", "语句序号", "引用位置", "置信度", "目标表", "表别名",
                    "目标字段", "下游落地字段", "下游表达式", "src_id分区", "本次排查字段"], rows3)
    return p0, p1, p2, p3


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--task", default=None, help="调度号（调度任务 ID），如 1001")
    ap.add_argument("--table", default=None, help="模型表名，如 dm_demo_security")
    ap.add_argument("--depth", type=int, default=1, help="下游层数，默认 1（只查直接下游）")
    ap.add_argument("--field", action="append", default=None,
                    help="要排查的字段，可重复；缺省扫描目标表全部字段")
    ap.add_argument("--src-id", default="auto",
                    help="关注的 src_id 分区：auto=按锚点任务写入分区推断；传 - 表示不限")
    ap.add_argument("--max-tasks", type=int, default=120, help="下游任务数上限（防大量调用）")
    ap.add_argument("--names", action="store_true",
                    help="为每个下游任务补任务名（每个任务 +1 次 MCP 调用，默认关闭）")
    ap.add_argument("--no-meta", action="store_true", help="不查元数据（少 1~2 次调用）")
    ap.add_argument("--out", default=None, help="输出目录（缺省 output/<目标>_<日期>/）")
    ap.add_argument("--adapter-config", default=None,
                    help="适配器配置 JSON；缺省读 DATADEV_ADAPTER_CONFIG 或 skill 根目录 .env")
    args = ap.parse_args(argv)
    if not args.task and not args.table:
        ap.error("至少提供 --task 或 --table 之一")
    if args.src_id in ("-", "None"):
        args.src_id = ""

    try:
        result, outdir, want_src = analyze(
            task_id=args.task, table=args.table, depth=args.depth, fields=args.field,
            src_id=args.src_id, max_tasks=args.max_tasks, with_names=args.names,
            use_meta=not args.no_meta, outdir=args.out, adapter_config=args.adapter_config)
    except AdapterError as exc:
        print("[adapter] %s" % exc, file=sys.stderr)
        print("[adapter] 配置示例见 adapter-configs/，可用 "
              "python -m datadev_adapters doctor --config <file> 先自检", file=sys.stderr)
        return 1
    p0, p1, p2, p3 = build_tsv(result, outdir)

    st, tgt, sc = result["stats"], result["target"], result["scope"]
    print("目标表：%s%s（%d 列，字段清单来源：%s）" % (tgt["table"],
          ("，分区 src_id=" + "/".join(tgt["srcIds"])) if tgt["srcIds"] else "",
          len(tgt["columns"]), tgt["columnsFrom"] or "无"))
    print("本次排查字段：%s" % ", ".join(tgt["scanFields"]))
    for note in result["anchorNotes"]:
        print("  ·", note)
    # 影响范围小结：让用户一眼知道这次波及面多大，细节看 TSV
    print("影响范围：下游任务 %d 个（%d 层，引用目标表 %d 个）｜命中本次字段 %d 个任务｜疑似 %d｜仅同名 %d"
          % (sc["downstreamTasks"], args.depth, sc["refTargetTasks"],
             len(sc["hitTasks"]), len(sc["suspectTasks"]), len(sc["sameNameTasks"])))
    print("          涉及字段：排查 %d 个（命中 %d 个）；目标表 %d 列，被下游引用 %d 列"
          % (len(sc["scanFields"]), len(sc["hitFields"]),
             sc["targetColumns"], len(sc["referencedFields"])))
    if sc["downstreamTableCount"]:
        names = "、".join(sc["downstreamTables"][:10])
        print("          涉及下游表(模型/主题表)：%d 个（%s%s）"
              % (sc["downstreamTableCount"], names,
                 " 等" if sc["downstreamTableCount"] > 10 else ""))
    print("MCP 调用：实发 %d 次，缓存命中 %d 次" % (st["apiMiss"], st["cacheHit"]))
    if want_src:
        print("关注分区 src_id='%s'" % want_src)
    if st["unknownColumns"]:
        print("下游引用了、但目标表字段清单里没有的字段（疑似改名/删除后的脏引用）：%s"
              % ",".join(st["unknownColumns"]))
    order = {"限定": 0, "疑似": 1, "同名": 2, "无": 3}
    scan_set = set(sc["scanFields"])
    shown = [x for x in result["fields"] if x["field"] in scan_set or not x["inTable"]]
    for f in sorted(shown, key=lambda x: (order[x["kind"]], -len(x["tasks"]), x["field"])):
        if f["tasks"] or not f["inTable"]:
            print("  %-18s %-4s 引用 %d 个任务（限定 %d / 疑似 %d / 同名 %d）%s"
                  % (f["field"], f["kind"], len(f["tasks"]),
                     len(f["qual"]), len(f["susp"]), len(f["same"]),
                     "（不在目标表中）" if not f["inTable"] else ""))
    others = [x for x in result["fields"] if x["field"] not in scan_set and x["inTable"]]
    if others:
        print("  （另有 %d 个非本次排查字段被下游引用，见 2_field_impact.tsv）" % len(others))
    for p in (p0, p1, p2, p3, os.path.join(outdir, "impact.json")):
        print("saved:", p)
    for e in result["errors"][:10]:
        print("ERR:", one_line(json.dumps(e, ensure_ascii=False), 200))
    return 0


if __name__ == "__main__":
    sys.exit(main())
