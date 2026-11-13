#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
主数据来源映射采集：输入主数据表名，沿 主数据(dm) -> 模型 -> 来源 链路
采集元数据、血缘与调度任务的 ETL SQL，解析出字段级映射，落盘 mapping.json。

分层前缀可用环境变量调整（默认 dm/pdata/odata）：
  DM_LAYER_MODEL_PREFIX   模型层库名前缀，默认 pdata
  DM_LAYER_SOURCE_PREFIX  来源层库名前缀，默认 odata

核心口径（重要）：
  以**主数据装载任务**为锚点（任务名 = <主数据表>_grpNN，由 --dm-task 指定或自动发现），
  该任务的**上游调度任务**才是主数据真正依赖的模型任务；只采集这些任务的 ETL SQL。
  模型表其余任务（未被主数据依赖的来源）不进入产物。
  dm 任务自身的 SQL（临时表 + 最终 INSERT）用于提取 dm 层加工逻辑：
  表达式链中出现 src_id 分支 / 优先级合并(a1./a2./a3. / rn / rk)的字段视为“多源判断”。

用法：
    python collect_mapping.py <主数据表名> [--dm-task 调度任务ID] [--out DIR] [--adapter-config FILE]

产出（--out 目录，默认 output/<表名>_<日期>/）：
    mapping.json   全量结构化映射（供 build_tsv.py 与人工核对）
    cache/         适配器原始响应缓存（重跑不重复请求）
"""
import argparse
import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "_shared"))
from datadev_adapters import AdapterError, adapter_from_env

MODEL_LAYER = os.environ.get("DM_LAYER_MODEL_PREFIX", "pdata").strip().lower() or "pdata"
SOURCE_LAYER = os.environ.get("DM_LAYER_SOURCE_PREFIX", "odata").strip().lower() or "odata"

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # skill 根目录（.env 所在）


# ---------------------------------------------------------------- SQL 解析

RE_CREATE_HEAD = re.compile(r"create\s+(?:temporary\s+)?table\s+(?:if\s+not\s+exists\s+)?([\w.]+)\s*(?:\(|as\b)", re.I)
RE_DDL_COL = re.compile(
    r"^\s*,?\s*([A-Za-z_][\w]*)\s+([A-Za-z]+(?:\s*\(\s*[\d,\s]*\s*\))?)"
    r"(?:\s+comment\s+'((?:[^'\\]|\\.)*)')?\s*,?\s*$")
RE_INSERT_HEAD = re.compile(
    r"insert\s+(?:overwrite\s+|into\s+)table\s+([\w.]+)\s*(?:partition\s*\(([^)]*)\))?", re.I)
RE_SRC_ID = re.compile(r"src_id\s*=\s*'([^']+)'", re.I)
RE_ITEM = re.compile(r"^(?:,\s*)?(.*?)\s+as\s+([A-Za-z_][\w]*)\s*(?:--(.*))?$", re.S | re.I)
RE_TABLE_REF = re.compile(
    r"(?:from|join)\s+([A-Za-z_][\w]*)\.([A-Za-z_][\w]*)\s*(?:as\s+)?([A-Za-z_][\w]*)?", re.I)
RE_COL_REF = re.compile(r"\b([A-Za-z_][\w]*)\.([A-Za-z_][\w]*)\b")
RE_BARE_COL = re.compile(r"^[A-Za-z_][\w]*$")
_ALIAS_STOPWORDS = {"where", "on", "and", "or", "join", "left", "right", "inner", "outer",
                    "group", "order", "by", "when", "then", "else", "end", "as", "union",
                    "select", "from", "partition", "case", "set", "values"}
# 多源判断特征：src_id 分支、按来源优先级取数、a1/a2/a3 逐源取值
RE_MULTI_HINT = re.compile(r"\bsrc_id\b|\ba[1-9]\.|\brn\b|\brk\b|\bsrc_id_2\b", re.I)


def parse_ddl_columns(ddl_text):
    """create table (...) 的字段清单 -> [{name,type,comment}]"""
    m = RE_CREATE_HEAD.search(ddl_text)
    if not m or "(" not in ddl_text[m.end() - 1:m.end() + 1]:
        return []
    body = ddl_text[m.end():]
    depth, cur = 1, []
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                break
        if depth == 1:
            cur.append(ch)
    cols = []
    for line in "".join(cur).splitlines():
        mm = RE_DDL_COL.match(line.strip())
        if mm:
            cols.append({"name": mm.group(1).lower(), "type": mm.group(2),
                         "comment": mm.group(3) or ""})
    return cols


def _split_select_items(select_text):
    """select 列表 -> [{expr, field, comment}]。
    按括号深度 0 的逗号做字符级切分（兼容「,expr as x」与「expr as x,」两种风格与跨行表达式）。
    """
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
        raw = raw.strip()
        raw = re.sub(r"^select\s+", "", raw, flags=re.I)  # 首段去掉 select 关键字
        raw = re.sub(r"^\s*--[^\n]*\n?", "", raw)  # 上一个表达式遗留的行尾注释
        if not raw:
            continue
        m = RE_ITEM.match(raw)
        if not m:
            # 无 as 的裸别名（case...end 别名）：别名=末个 token，且表达式以 end 结尾
            m2 = re.match(r"^(.*\S)\s+([A-Za-z_]\w*)\s*(?:--(.*))?$", raw, re.S)
            if m2 and m2.group(1).rstrip().lower().endswith("end"):
                expr = re.sub(r"\s+", " ", m2.group(1)).strip()
                out.append({"expr": expr, "field": m2.group(2).lower(),
                            "comment": (m2.group(3) or "").strip()})
            continue
        expr = re.sub(r"\s+", " ", m.group(1)).strip()
        out.append({"expr": expr, "field": m.group(2).lower(),
                    "comment": (m.group(3) or "").strip()})
    return out


def extract_all_items(sql):
    """提取语句中所有层级 select 列表的 `表达式 as 字段` 项（含子查询/临时表）。"""
    items = []
    text = sql
    for sel in re.finditer(r"\bselect\b", text, re.I):
        # 找该 select 列表的终点：括号深度 0 处的 from / 括号闭合
        depth, k, n = 0, sel.end(), len(text)
        while k < n:
            ch = text[k]
            if ch == "(":
                depth += 1
            elif ch == ")":
                if depth == 0:
                    break
                depth -= 1
            elif depth == 0 and text[k:k + 4].lower() == "from" \
                    and text[k + 4] in " \t\n\r(":
                break
            k += 1
        items.extend(_split_select_items(text[sel.end():k]))
    # 去重（同字段同表达式只留一份）
    seen, uniq = set(), []
    for it in items:
        key = (it["field"], it["expr"])
        if key not in seen:
            seen.add(key)
            uniq.append(it)
    return uniq


def split_statement(sql):
    """select 列表与 from 子句切分：从首个独立 select 起到深度 0 的 from 行。"""
    sel = re.search(r"\bselect\b", sql, re.I)
    if not sel:
        return "", sql
    sel_lines, from_lines, in_sel, depth = [], [], True, 0
    for line in sql[sel.end():].splitlines():
        low = line.strip().lower()
        if in_sel and depth == 0 and (low.startswith("from ") or low == "from"):
            in_sel = False
        (sel_lines if in_sel else from_lines).append(line)
        depth += line.count("(") - line.count(")")
    return "\n".join(sel_lines), "\n".join(from_lines)


def _resolve_aliases(from_clause):
    """from/join 子句 -> {alias: {db, table}}。
    只解析最外层作用域：子查询（任意嵌套）整体消费、不重入，其内部别名不外泄；
    子查询别名取闭合括号后的标识符，内部首张 from 表作为其来源表。
    """
    aliases = {}
    consumed = []  # 已消费的子查询括号区间，防止内层别名/表引用外泄

    def in_consumed(pos):
        return any(s <= pos < e for s, e in consumed)

    def inner_table(text):
        tm = re.search(r"from\s+([A-Za-z_]\w*)\.([A-Za-z_]\w*)", text, re.I) \
            or re.search(r"from\s+([A-Za-z_]\w*)(?=\s|\)|$)", text, re.I)
        if not tm:
            return None
        if tm.lastindex == 2:
            return {"db": tm.group(1).lower(), "table": tm.group(2).lower()}
        return {"db": "", "table": tm.group(1).lower()}

    # 子查询：from/join ( ... ) alias —— 括号深度字符扫描，支持任意嵌套
    for m in re.finditer(r"(?:from|join)\s*\(", from_clause, re.I):
        start = m.end() - 1
        if in_consumed(start):
            continue
        depth, k = 0, start
        while k < len(from_clause):
            if from_clause[k] == "(":
                depth += 1
            elif from_clause[k] == ")":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        consumed.append((start, k))
        am = re.match(r"\s*([A-Za-z_]\w*)", from_clause[k + 1:])
        if not am:
            continue
        alias = am.group(1).lower()
        if alias in _ALIAS_STOPWORDS:
            continue
        tbl = inner_table(from_clause[start + 1:k])
        if tbl:
            aliases[alias] = tbl
    # 普通表引用（db.table 与裸表名），不覆盖子查询别名，且跳过子查询内部
    for m in RE_TABLE_REF.finditer(from_clause):
        if in_consumed(m.start()):
            continue
        alias = (m.group(3) or "").lower()
        if not alias or alias in _ALIAS_STOPWORDS:
            continue
        aliases.setdefault(alias, {"db": m.group(1).lower(), "table": m.group(2).lower()})
    for m in re.finditer(r"(?:from|join)\s+([A-Za-z_]\w*)(?!\.)(?:\s+(?:as\s+)?([A-Za-z_]\w*))?(?=\s|;|$)",
                         from_clause, re.I):
        if in_consumed(m.start()):
            continue
        tbl = m.group(1).lower()
        if tbl in _ALIAS_STOPWORDS:
            continue
        alias = (m.group(2) or "").lower()
        if alias in _ALIAS_STOPWORDS:
            alias = tbl
        aliases.setdefault(alias, {"db": "", "table": tbl})
    return aliases


def _depth0_join_segments(fc):
    """按括号深度 0 的 join 关键字切 from 子句 -> [段文本]（每段 = join ... on ...）"""
    marks = []
    for m in re.finditer(r"\b(?:left\s+|right\s+|inner\s+)?join\b", fc, re.I):
        depth = 0
        for ch in fc[:m.start()]:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
        if depth == 0:
            marks.append(m.start())
    segs = []
    for idx, s in enumerate(marks):
        e = marks[idx + 1] if idx + 1 < len(marks) else len(fc)
        segs.append(fc[s:e])
    return segs


def _seg_alias(seg):
    """join 段 -> 别名（子查询取闭合括号后标识符，普通表取表名/别名）"""
    m = re.search(r"join\s*\(", seg, re.I)
    if m:
        start = m.end() - 1
        depth, k = 0, start
        while k < len(seg):
            if seg[k] == "(":
                depth += 1
            elif seg[k] == ")":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        am = re.match(r"\s*([A-Za-z_]\w*)", seg[k + 1:])
        return am.group(1).lower() if am else None
    m = re.search(r"join\s+(?:[A-Za-z_]\w*\.)?[A-Za-z_]\w*(?:\s+(?:as\s+)?([A-Za-z_]\w*))?", seg, re.I)
    if m:
        alias = (m.group(1) or "").lower()
        if alias and alias not in _ALIAS_STOPWORDS:
            return alias
        tbl = re.match(r"join\s+((?:[A-Za-z_]\w*\.)?[A-Za-z_]\w*)", seg, re.I)
        return tbl.group(1).split(".")[-1].lower() if tbl else None
    return None


def _join_texts(from_clause):
    """{别名: join 子句全文（含 on 条件）}，供码值关联/模型表关联的加工逻辑展示"""
    out = {}
    for seg in _depth0_join_segments(from_clause):
        alias = _seg_alias(seg)
        if alias and alias not in _ALIAS_STOPWORDS:
            out.setdefault(alias, re.sub(r"\s+", " ", seg).strip())
    return out


def _main_table(from_clause):
    """主 from 表 -> {db, table}；从 from 子句开头锚定（深度 0 的第一张表），
    子查询取其内部首个 from 表。"""
    fc = from_clause.strip()
    m = re.match(r"from\s*\(", fc, re.I)
    if m:
        start = m.end() - 1
        depth, k = 0, start
        while k < len(fc):
            if fc[k] == "(":
                depth += 1
            elif fc[k] == ")":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        tm = re.search(r"from\s+([A-Za-z_]\w*)\.([A-Za-z_]\w*)", fc[start + 1:k], re.I) \
            or re.search(r"from\s+([A-Za-z_]\w*)(?=\s|\)|$)", fc[start + 1:k], re.I)
    else:
        tm = re.match(r"from\s+([A-Za-z_]\w*)\.([A-Za-z_]\w*)", fc, re.I) \
            or re.match(r"from\s+([A-Za-z_]\w*)(?=\s|;|$)", fc, re.I)
    if not tm:
        return None
    if tm.lastindex == 2:
        return {"db": tm.group(1).lower(), "table": tm.group(2).lower()}
    return {"db": "", "table": tm.group(1).lower()}


def parse_statement(sql):
    """解析单条语句 -> {kind, target, src_id, columns, items, aliases, mainTable, joinText}
    kind: ddl（create table，含 temporary/CTAS）/ insert
    """
    s = sql.strip().rstrip(";")
    ins = RE_INSERT_HEAD.search(s)
    if not ins:
        target = RE_CREATE_HEAD.search(s)
        if not target:
            return {"kind": "ddl", "target": None, "src_id": None,
                    "columns": parse_ddl_columns(s), "items": [], "aliases": {},
                    "mainTable": None, "joinText": {}}
        tname = target.group(1).lower()
        rest = s[target.end():]
        if rest.lstrip().lower().startswith("select"):
            # create table ... as select：解析 select 项（dm 层临时表加工逻辑）
            _, from_clause = split_statement(rest)
            return {"kind": "ddl", "target": tname, "src_id": None, "columns": [],
                    "items": extract_all_items(rest), "aliases": _resolve_aliases(from_clause),
                    "mainTable": _main_table(from_clause), "joinText": _join_texts(from_clause)}
        return {"kind": "ddl", "target": tname, "src_id": None,
                "columns": parse_ddl_columns(s), "items": [], "aliases": {},
                "mainTable": None, "joinText": {}}
    src_id = None
    if ins.group(2):
        sm = RE_SRC_ID.search(ins.group(2))
        if sm:
            src_id = sm.group(1)
    sel_text, from_clause = split_statement(s[ins.end():])
    items = extract_all_items(s[ins.end():])
    aliases = _resolve_aliases(from_clause)
    for it in items:
        refs = RE_COL_REF.findall(it["expr"])
        srcs = []
        for alias, col in refs:
            tbl = aliases.get(alias.lower())
            if tbl:
                srcs.append({"db": tbl["db"], "table": tbl["table"],
                             "column": col.lower(), "alias": alias.lower()})
        it["srcRefs"] = srcs
        it["constOnly"] = not srcs
    return {"kind": "insert", "target": ins.group(1).lower(), "src_id": src_id,
            "columns": [], "items": items, "aliases": aliases,
            "mainTable": _main_table(from_clause), "joinText": _join_texts(from_clause)}


# ------------------------------------------------------------ dm 层加工逻辑链

def build_dm_field_logic(stmts, master_table, master_columns=None):
    """从 dm 任务语句集中提取每个主数据字段的加工逻辑链。

    返回 {dm_field: {"expr": 最终表达式, "chain": ["tmp表: 表达式", ...],
                      "multiSource": bool, "alias": select别名}}
    策略：最终 INSERT 的字段表达式起，若为裸列名则在临时表 select 项里继续下钻（≤3 层）；
    链上任一表达式命中多源特征（src_id 分支/优先级合并）即标记多源判断。
    注意 Hive INSERT 按位置写入：final INSERT 的第 i 个 select 项对应主数据表第 i 个
    非分区字段，别名可能与真实列名不一致，故优先按位置对齐。
    """
    final = next((st for st in stmts if st["kind"] == "insert"
                  and st["target"].split(".")[-1] == master_table.split(".")[-1]), None)
    if not final:
        return {}
    tmp_cols = {}  # 列名 -> (tmp表名, expr)
    for st in stmts:
        if st["kind"] == "ddl" and st["target"] and st["items"]:
            for it in st["items"]:
                tmp_cols.setdefault(it["field"], (st["target"].split(".")[-1], it["expr"]))
    # 位置对齐：主数据非分区列 <-> INSERT select 项
    part_cols = set()
    pm = re.search(r"partition\s*\(([^)]*)\)", final["sql"], re.I)
    if pm:
        part_cols = {p.split("=")[0].strip().lower() for p in pm.group(1).split(",") if p.strip()}
    keys = None
    if master_columns:
        cols = [c["name"] if isinstance(c, dict) else c for c in master_columns
                if (c["name"] if isinstance(c, dict) else c) not in part_cols]
        if len(cols) == len(final["items"]):
            keys = [c.lower() for c in cols]
    logic = {}
    for idx, it in enumerate(final["items"]):
        field = keys[idx] if keys else it["field"]
        chain, multi = [], False
        seen = set()
        cur = it["expr"]
        for _ in range(3):
            if RE_MULTI_HINT.search(cur):
                multi = True
            if RE_BARE_COL.match(cur) and cur in tmp_cols and cur not in seen:
                seen.add(cur)
                tbl_name, up = tmp_cols[cur]
                chain.append("%s: %s" % (tbl_name, up))
                cur = up
            else:
                break
        logic[field] = {"expr": it["expr"], "chain": chain, "multiSource": multi,
                        "alias": it["field"]}
    return logic


# ---------------------------------------------------------------- 采集主流程

class Collector:
    def __init__(self, outdir, adapter=None, adapter_config=None):
        self.c = adapter or adapter_from_env(path=adapter_config, skill_root=BASE)
        self.outdir = outdir
        self.cache = os.path.join(outdir, "cache")
        os.makedirs(self.cache, exist_ok=True)
        self.errors = []

    def _cache_path(self, key):
        return os.path.join(self.cache, re.sub(r"[^\w.-]", "_", key) + ".json")

    def _cached(self, operation, key, fetch):
        """Disk cache keyed by logical operation + identifier (re-runs are free)."""
        path = self._cache_path("%s.%s" % (operation, key))
        if os.path.exists(path):
            with open(path, encoding="utf-8") as handle:
                return json.load(handle)
        try:
            data = fetch()
        except AdapterError as exc:
            self.errors.append({"operation": operation, "key": key, "error": str(exc)})
            return None
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=1)
        return data

    def table_detail(self, guid):
        return self._cached("get_table", guid, lambda: self.c.get_table(guid)) or {}

    def task_sql(self, task_id):
        return self._cached("get_task_sql", str(task_id),
                            lambda: self.c.get_task_sql(task_id)) or []

    def search_master(self, table_name):
        rows = self._cached("search_table", table_name,
                            lambda: self.c.search_table(table_name)) or []
        exact = [r for r in rows
                 if (r.get("name") or "").lower() == table_name.lower()]
        if not exact:
            raise SystemExit("未在元数据中精确匹配到表 %s（候选：%s）" %
                             (table_name, [r.get("name") for r in rows[:10]]))
        return exact[0]

    # ---- 主数据装载任务发现 ----
    def find_dm_tasks(self, model_tasks, master_name):
        """扫描模型任务的下游，找 taskName 含主数据表名的装载任务。"""
        scanned, hits = set(), []
        for t in model_tasks:
            deps = self._cached(
                "get_task_dependencies", "DOWNSTREAM.1.%s" % t["taskId"],
                lambda tid=t["taskId"]: self.c.get_task_dependencies(tid, "DOWNSTREAM", 1)) or []
            for did in deps:
                if did in scanned:
                    continue
                scanned.add(did)
                detail = self._cached(
                    "get_task_detail", str(did),
                    lambda x=did: self.c.get_task_detail(x)) or {}
                name = detail.get("taskName") or ""
                if master_name.lower() in name.lower():
                    hits.append({"taskId": str(did), "taskName": name,
                                 "desc": detail.get("taskDesc") or ""})
        return hits

    def collect(self, table_name, dm_task_id=None):
        hit = self.search_master(table_name)
        master = self.table_detail(hit["guid"])
        master_info = {
            "name": master.get("name"), "dbName": master.get("db"),
            "guid": hit["guid"], "qualifiedName": master.get("qualifiedName"),
            "comment": master.get("comment"),
            "columns": [{"name": c["name"], "comment": c.get("comment") or "",
                         "codedsetCode": c.get("codedsetCode"),
                         "codedsetChnName": c.get("codedsetChnName")}
                        for c in (master.get("columns") or [])],
        }

        ups = self._cached("get_lineage", hit["guid"],
                           lambda: self.c.get_lineage(hit["guid"], "upstream")) or []
        model_ups = [t for t in ups if MODEL_LAYER in (t.get("qualifiedName") or "").lower()]
        other_ups = [t for t in ups if t not in model_ups]

        models, odata_registry, all_model_tasks = [], {}, []
        for mt in model_ups:
            detail = self.table_detail(mt["guid"])
            model = {"name": detail.get("name"), "dbName": detail.get("db"),
                     "guid": mt["guid"], "comment": detail.get("comment"),
                     "tasks": [], "odataGuids": [], "otherUpstreams": []}
            mups = self._cached("get_lineage", mt["guid"],
                                lambda g=mt["guid"]: self.c.get_lineage(g, "upstream")) or []
            for t in mups:
                qn = (t.get("qualifiedName") or "").lower()
                if SOURCE_LAYER in qn:
                    model["odataGuids"].append(t["guid"])
                    odata_registry[t["guid"]] = {"guid": t["guid"], "qualifiedName": t["qualifiedName"]}
                else:
                    model["otherUpstreams"].append(t.get("qualifiedName"))
            for gid in model["odataGuids"]:
                det = self.table_detail(gid)
                reg = odata_registry[gid]
                reg["comment"] = det.get("comment") or ""
                reg["db"] = det.get("db") or ""
                reg["table"] = det.get("name") or ""
                reg["columns"] = {c["name"]: (c.get("comment") or "")
                                  for c in (det.get("columns") or [])}
            for task in detail.get("tasks") or []:
                model["tasks"].append({"taskId": str(task["taskId"]), "taskName": task["taskName"],
                                       "inDmUpstream": None, "srcId": None, "statements": []})
                all_model_tasks.append(model["tasks"][-1])
            models.append(model)

        # ---- 主数据装载任务（锚点）----
        if dm_task_id:
            dd = self._cached("get_task_detail", str(dm_task_id),
                              lambda: self.c.get_task_detail(dm_task_id)) or {}
            dm_tasks = [{"taskId": str(dm_task_id), "taskName": dd.get("taskName") or "",
                         "desc": dd.get("taskDesc") or ""}]
        else:
            dm_tasks = self.find_dm_tasks(all_model_tasks, table_name)
            print("发现主数据装载任务: %s" % [t["taskId"] + " " + t["taskName"] for t in dm_tasks])
        # 权威模型任务 = 所有装载任务的一级上游
        upstream_ids = []
        for dt in dm_tasks:
            upstream_ids += self._cached(
                "get_task_dependencies", "UPSTREAM.1.%s" % dt["taskId"],
                lambda tid=dt["taskId"]: self.c.get_task_dependencies(tid, "UPSTREAM", 1)) or []
        upstream_ids = [str(u) for u in dict.fromkeys(upstream_ids)]
        up_set = set(upstream_ids)

        # ---- 仅对权威任务拉 SQL 并解析 ----
        for model in models:
            for t in model["tasks"]:
                t["inDmUpstream"] = t["taskId"] in up_set
                if not t["inDmUpstream"]:
                    continue
                stmts_raw = self.task_sql(t["taskId"])
                stmts = []
                for s in stmts_raw:
                    try:
                        st = parse_statement(s)
                        st["sql"] = s
                        stmts.append(st)
                    except Exception as e:
                        self.errors.append({"taskId": t["taskId"], "error": "parse: %s" % e})
                t["srcId"] = next((st["src_id"] for st in stmts if st.get("src_id")), None)
                t["statements"] = stmts

        # ---- dm 任务 SQL -> 字段加工逻辑链 ----
        dm_logic = {}
        for dt in dm_tasks:
            stmts = []
            for s in self.task_sql(dt["taskId"]):
                try:
                    st = parse_statement(s)
                    st["sql"] = s
                    stmts.append(st)
                except Exception as e:
                    self.errors.append({"taskId": dt["taskId"], "error": "parse: %s" % e})
            dt["statements"] = stmts
            dm_logic.update(build_dm_field_logic(stmts, table_name, master_info["columns"]))

        return {"master": master_info,
                "models": models,
                "odata": list(odata_registry.values()),
                "dmTasks": dm_tasks,
                "dmUpstreamTaskIds": upstream_ids,
                "dmFieldLogic": dm_logic,
                "masterOtherUpstreams": [t.get("qualifiedName") for t in other_ups],
                "errors": self.errors}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("table")
    ap.add_argument("--dm-task", default=None, help="主数据装载调度任务ID（缺省自动发现）")
    ap.add_argument("--out", default=None)
    ap.add_argument("--adapter-config", default=None,
                    help="适配器配置 JSON；缺省读 DATADEV_ADAPTER_CONFIG 或 skill 根目录 .env")
    args = ap.parse_args()
    outdir = args.out or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output",
        "%s_%s" % (args.table, datetime.date.today().strftime("%Y%m%d")))
    os.makedirs(outdir, exist_ok=True)
    try:
        col = Collector(outdir, adapter_config=args.adapter_config)
        result = col.collect(args.table, args.dm_task)
    except AdapterError as exc:
        print("[adapter] %s" % exc, file=sys.stderr)
        print("[adapter] 配置示例见 adapter-configs/，可用 "
              "python -m datadev_adapters doctor --config <file> 先自检", file=sys.stderr)
        return 1
    path = os.path.join(outdir, "mapping.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    auth = [t["taskId"] + ":" + t["taskName"]
            for m in result["models"] for t in m["tasks"] if t["inDmUpstream"]]
    multi = [f for f, v in result["dmFieldLogic"].items() if v["multiSource"]]
    print("saved:", path)
    print("master columns: %d | models: %d | 权威任务: %d | odata: %d | 多源判断字段: %d | errors: %d" % (
        len(result["master"]["columns"]), len(result["models"]), len(auth),
        len(result["odata"]), len(multi), len(result["errors"])))
    print("权威任务:", auth)
    print("多源判断字段:", multi)
    for e in result["errors"][:10]:
        print("ERR:", json.dumps(e, ensure_ascii=False)[:200])
    return 0


if __name__ == "__main__":
    sys.exit(main())
