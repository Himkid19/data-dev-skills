#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
由 collect_mapping.py 产出的 mapping.json 生成两份 TSV（UTF-8 BOM，可直接粘贴远程表格）：

  1_source_tables.tsv  接入源表清单：来源缩写 / 来源表英文名 / 来源表中文名 / 匹配情况
  2_field_mapping.tsv  详细字段映射大宽表：主数据字段 + 来源1..N 各 5 列
                       (来源x / 来源x表名称 / 来源x表字段中文名称 / 来源x表字段英文名称 / 来源x表字段加工逻辑)

口径：
  - 来源列全局对齐：来源1..N 在所有行固定同一来源缩写，顺序由环境变量
    SOURCE_PRIORITY 决定（逗号分隔，如 `VENDOR_A,VENDOR_B`），未列出的按字母序在后；
    每个字段 × 每个来源恰好一组，某来源没有该字段映射时该组表名称填该来源的主源表、
    加工逻辑填「空值」。
  - 来源x表名称只放一张 odata 源表：表达式引用的源表，缺省为该任务主表。
  - 来源x表字段只放真正来自源表的字段：直传/表达式取表达式引用的源表字段；
    码值转换（值来自码值维表）取与码值维表 left join 的源表关联键字段，
    加工逻辑填该 join 子句；值来自其他模型表同样只填 join 子句。
  - 加工逻辑只写非平凡情形：常量 / 空值 / 表达式 / join 子句；纯直传留空。
  - dm 层多源判断（src_id 分支/优先级合并）字段照常给出各来源分组，
    匹配情况标「多源判断(仅加工逻辑)」。
"""
import argparse
import json
import os
import re
import sys

RE_LITERAL = re.compile(r"^'[^']*'$|^-?\d+(\.\d+)?$")
RE_PLAIN_COL = re.compile(r"^[A-Za-z_]\w*\.[A-Za-z_]\w*$")

# 来源缩写展示顺序（可选）：SOURCE_PRIORITY=VENDOR_A,VENDOR_B
# 列出的来源按给定顺序在前，未列出的按字母序排在后面；完全不配置则全部字母序。
SOURCE_PRIORITY = [s.strip() for s in os.environ.get("SOURCE_PRIORITY", "").split(",") if s.strip()]

# 来源层库名前缀（与 collect_mapping.py 的 DM_LAYER_SOURCE_PREFIX 保持一致）
SOURCE_LAYER = os.environ.get("DM_LAYER_SOURCE_PREFIX", "odata").strip().lower() or "odata"


def is_source_db(db):
    return (db or "").lower().startswith(SOURCE_LAYER)


def src_sort_key(src):
    """来源排序键：优先按 SOURCE_PRIORITY，其次字母序。"""
    if src in SOURCE_PRIORITY:
        return (0, SOURCE_PRIORITY.index(src), src)
    return (1, 0, src)


def is_simple_expr(expr):
    return bool(re.fullmatch(r"[A-Za-z_]\w*\.[A-Za-z_]\w*", expr))


def src_sys_of(task):
    if task.get("srcId"):
        return task["srcId"]
    m = re.match(r"[\w]+_n\.[\w]+?_([A-Z]{2,5})(?:_|$)", task.get("taskName", ""))
    return m.group(1) if m else task.get("taskName", "?")


def model_candidates(dlogic):
    """dm 字段 -> 可对应的模型字段名候选（链上引用的裸列名，剔除技术列）。"""
    tech = {"src_id", "src_id_2", "rn", "rn_i", "rk", "busi_date"}
    names = []
    for e in [dlogic["expr"]] + dlogic.get("chain", []):
        for m in re.finditer(r"\b([A-Za-z_][\w]*)\b", e):
            n = m.group(1).lower()
            if n not in tech and n not in names:
                names.append(n)
    return names


def _join_key_fields(join_text, aliases):
    """join 子句 on 条件里落在 odata 源表上的纯 column 关联键 -> [{col, db, table}]"""
    if not join_text:
        return []
    low = join_text.lower()
    pos = low.rfind(" on ")
    if pos < 0:
        return []
    on = join_text[pos + 4:]
    out = []
    for token in re.split(r"\band\b|=", on, flags=re.I):
        side = token.strip().strip("()")
        if RE_PLAIN_COL.match(side):
            a, c = side.lower().split(".")
            tbl = aliases.get(a)
            if tbl and is_source_db(tbl["db"]) and not any(f["col"] == c for f in out):
                out.append({"col": c, "db": tbl["db"], "table": tbl["table"]})
    return out


def entry_view(model, task, stmt, it, odata_by_name):
    """单条模型 select 项 -> {table(db.table), fields, logic, real, simple, const, empty}"""
    expr = it["expr"]
    refs = it.get("srcRefs") or []
    odata_refs = [r for r in refs if is_source_db(r["db"])]
    inner_refs = [r for r in refs if not is_source_db(r["db"])]
    aliases = stmt.get("aliases") or {}

    fields = []           # [{col, cn, db, table}]
    tbl_count = {}
    for r in odata_refs:
        key = r["db"] + "." + r["table"]
        tbl_count[key] = tbl_count.get(key, 0) + 1
        if not any(f["col"] == r["column"] and f["table"] == r["table"] for f in fields):
            o = odata_by_name.get(key, {})
            fields.append({"col": r["column"], "cn": o.get("columns", {}).get(r["column"], ""),
                           "db": r["db"], "table": r["table"]})

    def jt_of(alias):
        return (stmt.get("joinText") or {}).get(alias)

    parts = []
    if expr in ("''", '""', ""):
        logic = "空值"
    elif RE_LITERAL.match(expr):
        logic = "常量"
    elif is_simple_expr(expr) and odata_refs and not inner_refs:
        logic = ""
    elif inner_refs and not odata_refs:
        # 值来自码值表/模型表：加工逻辑 = join 子句；字段 = on 条件里的源表关联键
        for a in dict.fromkeys(r["alias"] for r in inner_refs):
            jt = jt_of(a)
            if jt:
                parts.append(jt)
                for kf in _join_key_fields(jt, aliases):
                    tbl_count[kf["db"] + "." + kf["table"]] = tbl_count.get(kf["db"] + "." + kf["table"], 0) + 1
                    if not any(f["col"] == kf["col"] and f["table"] == kf["table"] for f in fields):
                        o = odata_by_name.get(kf["db"] + "." + kf["table"], {})
                        fields.append({"col": kf["col"], "cn": o.get("columns", {}).get(kf["col"], ""),
                                       "db": kf["db"], "table": kf["table"]})
        logic = " ; ".join(parts)
    else:
        logic = expr
    # 表名：表达式引用的源表（多数），否则任务主表
    if tbl_count:
        table = max(tbl_count.items(), key=lambda kv: kv[1])[0]
    else:
        mt = stmt.get("mainTable") or {}
        table = (mt.get("db", "") + "." + mt.get("table", "")) if mt.get("table") else ""
    empty_val = logic == "空值"
    real = not empty_val  # 常量/直传/表达式/join 均视为有内容；仅空值视为无来源
    return {"table": table, "fields": fields, "logic": logic, "real": real,
            "simple": is_simple_expr(expr), "const": logic == "常量",
            "empty": empty_val}


def collect_field_sources(mapping):
    """{dm_field: [group per 来源缩写]}；group = {srcSys, table, fields, logics, ...}"""
    odata_by_name = {}
    for o in mapping.get("odata", []):
        qdb = (o.get("db") or o["qualifiedName"].split("@")[0].split(".")[0]).lower()
        odata_by_name[qdb + "." + o["table"].lower()] = o
    odata_by_name.update({o["table"].lower(): o for o in mapping.get("odata", [])})

    def groups_for(dm_field, cands):
        # 每个来源分两类候选：exact = 模型 select 别名就是该主数据字段（值源就是它）；
        # loose = dm 表达式链里出现过的候选别名（模型换了字段名承载该值）。
        # 同一来源有 exact 时只用 exact——否则 dm 层 CASE 的判断输入列
        # （如 coup_var2 里的 coalesce(last_coup_rate, iss_coup_rate, ref_yield)）
        # 会被误当成该字段的来源字段混进产物。
        hits = {}
        for model in mapping["models"]:
            for task in model["tasks"]:
                if task.get("inDmUpstream") is False or not task.get("statements"):
                    continue
                sysname = src_sys_of(task)
                bucket = hits.setdefault(sysname, {"exact": [], "loose": []})
                for stmt in task["statements"]:
                    if stmt.get("kind") != "insert":
                        continue
                    for it in stmt.get("items", []):
                        if it["field"] not in cands:
                            continue
                        v = entry_view(model, task, stmt, it, odata_by_name)
                        v.update({"model": model["name"], "taskId": task["taskId"],
                                  "srcSys": sysname})
                        bucket["exact" if it["field"] == dm_field else "loose"].append(v)
        groups = []
        for sysname, bucket in hits.items():
            vs = bucket["exact"] or bucket["loose"]
            if not vs:
                continue
            # 主条目：有真实内容的优先，其次有源表字段的
            vs_sorted = sorted(vs, key=lambda v: (not v["real"], not v["fields"]))
            table = next((v["table"] for v in vs_sorted if v["table"]), "")
            fields, logics = [], []
            for v in vs_sorted:
                for f in v["fields"]:
                    if not any(x["col"] == f["col"] and x["table"] == f["table"] for x in fields):
                        fields.append(f)
                if v["logic"] and v["logic"] not in logics:
                    logics.append(v["logic"])
            groups.append({"srcSys": sysname, "table": table, "fields": fields,
                           "logics": logics,
                           "hasData": any(v["fields"] or v["logic"] not in ("", "空值", "常量")
                                          for v in vs),
                           "allEmpty": all(v["empty"] for v in vs),
                           "allConst": all(v["const"] for v in vs),
                           "simple": any(v["simple"] for v in vs),
                           "model": vs_sorted[0]["model"], "taskId": vs_sorted[0]["taskId"]})
        # 有真实内容的来源排前，其余按 SOURCE_PRIORITY / 字母序
        groups.sort(key=lambda g: (not g["hasData"], src_sort_key(g["srcSys"])))
        return groups

    field_map = {}
    for col in mapping["master"]["columns"]:
        f = col["name"]
        dlogic = mapping.get("dmFieldLogic", {}).get(f)
        cands = {f} | (set(model_candidates(dlogic)) if dlogic else set())
        field_map[f] = groups_for(f, cands)
    return field_map


def match_situation(groups, dlogic):
    if dlogic and dlogic.get("multiSource"):
        return "多源判断(仅加工逻辑)"
    if not groups:
        if dlogic:
            deepest = dlogic["chain"][-1].split(": ", 1)[-1] if dlogic["chain"] else dlogic["expr"]
            if RE_LITERAL.match(deepest.strip()) and deepest.strip() != "''":
                return "常量填充"
        return "未匹配(需人工)"
    if all(g["allEmpty"] for g in groups):
        # 各来源全部空值 -> 无真实来源
        return "未匹配(需人工)"
    if all(not g["hasData"] for g in groups):
        # 无任何真实加工（全是常量/空值）
        return "常量填充" if any(g["allConst"] for g in groups) else "未匹配(需人工)"
    data_groups = [g for g in groups if g["hasData"]]
    if len(data_groups) > 1:
        return "多源整合"
    return "单源直传" if data_groups[0]["simple"] else "单源表达式"


def code_flag(col):
    if col.get("codedsetCode"):
        return "是(%s)" % col["codedsetCode"]
    if col["name"].endswith(("_cd", "_flag")):
        return "疑似"
    return "否"


def build(mapping, outdir):
    master = mapping["master"]
    review_path = os.path.join(outdir, "review.json")
    review = json.load(open(review_path, encoding="utf-8")) if os.path.exists(review_path) else {}
    field_map = collect_field_sources(mapping)

    # ---- 来源列全局对齐：固定来源缩写顺序（SOURCE_PRIORITY 优先，其余按字母序），每行同一来源占同一组列 ----
    sys_seen = {g["srcSys"] for groups in field_map.values() for g in groups}
    sys_order = sorted(sys_seen, key=src_sort_key)
    # 每个来源的主源表 = 该来源各字段分组中出现最多的表
    sys_tbl_cnt = {}
    for groups in field_map.values():
        for g in groups:
            if g["table"]:
                sys_tbl_cnt.setdefault(g["srcSys"], {})
                sys_tbl_cnt[g["srcSys"]][g["table"]] = sys_tbl_cnt[g["srcSys"]].get(g["table"], 0) + 1
    sys_main = {s: max(t.items(), key=lambda kv: kv[1])[0] for s, t in sys_tbl_cnt.items()}

    # ---- TSV1 接入源表清单 ----
    usage = {}
    for f, groups in field_map.items():
        for g in groups:
            if not g["hasData"] or not g["table"]:
                continue
            u = usage.setdefault((g["srcSys"], g["table"]), {"n": set()})
            u["n"].add(f)
    odata_cn = {}
    for o in mapping.get("odata", []):
        odata_cn[o["table"].lower()] = o.get("comment", "")
    lines1 = ["\t".join(["来源缩写", "来源表英文名", "来源表中文名", "匹配情况"])]
    for (sysname, tbl), u in sorted(usage.items()):
        lines1.append("\t".join([sysname, tbl.split(".", 1)[1], odata_cn.get(tbl.split(".", 1)[1], ""),
                                 "覆盖%d个主数据字段" % len(u["n"])]))
    p1 = os.path.join(outdir, "1_source_tables.tsv")
    open(p1, "w", encoding="utf-8-sig").write("\n".join(lines1) + "\n")

    # ---- TSV2 详细字段映射宽表（来源列全局对齐；某来源无该字段映射时填「空值」组） ----
    max_src = len(sys_order)
    head = ["表英文名", "表中文名", "字段中文名", "字段英文名", "匹配情况", "是否为码值字段"]
    for i in range(1, max_src + 1):
        head += ["来源%d" % i, "来源%d表名称" % i, "来源%d表字段中文名称" % i,
                 "来源%d表字段英文名称" % i, "来源%d表字段加工逻辑" % i]
    lines2 = ["\t".join(head)]
    for c in master["columns"]:
        f = c["name"]
        real_groups = field_map.get(f, [])
        dlogic = mapping.get("dmFieldLogic", {}).get(f)
        situation = match_situation(real_groups, dlogic)
        rv = review.get(f, {})
        if rv.get("situation"):
            situation = rv["situation"]
        if rv.get("note"):
            situation += "；" + rv["note"]
        row = [master["name"], master["comment"] or "", c["comment"], f, situation, code_flag(c)]
        by_sys = {g["srcSys"]: g for g in real_groups}
        for sysname in sys_order:
            g = by_sys.get(sysname)
            if g is None:  # 该来源未映射此字段：填空值组
                g = {"srcSys": sysname, "table": sys_main.get(sysname, ""),
                     "fields": [], "logics": ["空值"]}
            fcns = "; ".join((x["cn"] or x["col"]) for x in g["fields"])
            fcols = "; ".join(x["col"] for x in g["fields"])
            logic = " | ".join(g["logics"])
            row += [g["srcSys"], g["table"], fcns, fcols, logic]
        lines2.append("\t".join(row))
    p2 = os.path.join(outdir, "2_field_mapping.tsv")
    open(p2, "w", encoding="utf-8-sig").write("\n".join(lines2) + "\n")
    return p1, p2, max_src


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mapping_json")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    mapping = json.load(open(args.mapping_json, encoding="utf-8"))
    outdir = args.out or os.path.dirname(os.path.abspath(args.mapping_json))
    os.makedirs(outdir, exist_ok=True)
    p1, p2, n = build(mapping, outdir)
    fm = collect_field_sources(mapping)
    nomatch = [c["name"] for c in mapping["master"]["columns"]
               if match_situation(fm.get(c["name"], []),
                                  mapping.get("dmFieldLogic", {}).get(c["name"])).startswith("未匹配")]
    print("TSV1:", p1)
    print("TSV2:", p2, "(来源列: %s)" % " > ".join(sorted(
        {g["srcSys"] for groups in fm.values() for g in groups},
        key=src_sort_key)))
    print("未匹配字段(%d): %s" % (len(nomatch), nomatch))
    return 0


if __name__ == "__main__":
    sys.exit(main())
