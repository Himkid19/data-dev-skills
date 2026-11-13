#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
码值（枚举）查询与多源对齐工具 —— 数据存 MySQL（多用户共享），表结构：

  md_code_values           标准码值表（code_domain, std_value, std_label, std_label_en, source_of_truth）
  md_code_value_mappings   源映射表（code_domain, src_sys, src_field, src_value, src_label, std_value, note）

连接配置见 db.py（环境变量 MYSQL_* 或 skill 根目录 .env）。
references/ 下的同名 CSV 仅作首次引导种子，不再是数据源。

子命令：
  domains                       列出全部标准码域
  lookup <码域...> [--src 源]   批量查标准码值 + 各源映射（空格分隔多个码域，
                                单进程单连接一次查完；--src 只看单源）
  align <码域> <源系统> <源字段> "1=未上市,3=上市" [--note 备注]
                                提交一批源码值对齐：匹配标准值则落映射表，
                                匹配不上报缺口（需人工裁决/提案新增标准值）
  validate <码域>               校验映射完整性（无悬空映射、覆盖源系统清单）

多操作人：align/validate 记录操作人，取环境变量 MD_OPERATOR（缺省 unknown）。
"""
import argparse
import os
import sys

import db

STD_FIELDS = ["code_domain", "std_value", "std_label", "std_label_en", "source_of_truth"]
MAP_FIELDS = ["code_domain", "src_sys", "src_field", "src_value", "src_label", "std_value", "note"]


def _operator():
    return os.environ.get("MD_OPERATOR") or db.get("MD_OPERATOR") or "unknown"


def _fetch(sql, args=()):
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, args)
            return cur.fetchall()
    finally:
        conn.close()


def cmd_domains(_):
    rows = _fetch("SELECT code_domain, COUNT(*) FROM md_code_values GROUP BY code_domain "
                  "UNION SELECT code_domain, 0 FROM md_code_value_mappings GROUP BY code_domain")
    domains = {}
    for d, n in rows:
        domains[d] = max(domains.get(d, 0), n)
    print("码域\t标准值数")
    for d in sorted(domains):
        print(f"{d}\t{domains[d]}")


def cmd_lookup(args):
    """批量查码域：多个码域共用一次连接，减少冷启动开销。"""
    miss = 0
    for domain in args.domain:
        domain = domain.strip()
        if not domain:
            continue
        std = _fetch("SELECT std_value, std_label, std_label_en FROM md_code_values "
                     "WHERE code_domain=%s ORDER BY std_value", (domain,))
        sql = "SELECT src_sys, src_field, src_value, src_label, std_value, note FROM md_code_value_mappings WHERE code_domain=%s"
        params = [domain]
        if args.src:
            sql += " AND src_sys=%s"
            params.append(args.src)
        mp = _fetch(sql, params)
        if not std and not mp:
            miss += 1
            print(f"【{domain}】码域不存在。需先在 md_code_values 登记标准码值"
                  f"（走知识库提案流程），禁止现场编造。")
            continue
        print(f"【{domain}】")
        if std:
            print("── 标准码值 ──")
            for v, label, en in std:
                print(f"  {v}\t{label}\t({en})")
        if mp:
            print("── 源映射 ──")
            for sys_, fld, v, label, std_v, note in mp:
                print(f"  [{sys_}] {fld}: {v}={label} → std={std_v}" + (f"  // {note}" if note else ""))
    return 0 if not miss else 2


def cmd_align(args):
    """把一批源码值对齐到标准码域。匹配不上的报缺口，绝不猜。"""
    domain, src_sys, src_field = args.domain, args.src_sys, args.src_field
    pairs = []
    for item in args.values.replace("，", ",").split(","):
        if "=" not in item:
            print(f"格式错误：{item}（应为 值=含义）", file=sys.stderr)
            return 2
        v, label = item.split("=", 1)
        pairs.append((v.strip(), label.strip()))
    std = _fetch("SELECT std_value, std_label FROM md_code_values WHERE code_domain=%s", (domain,))
    if not std:
        print(f"码域「{domain}」尚未建立标准码值。流程：① 先定标准值（提案 → 审核 → 并入 "
              f"md_code_values）② 再回来跑 align。禁止跳过标准值直接建映射。")
        return 2
    std_by_label = {label: v for v, label in std}
    existing = {r[0] for r in _fetch(
        "SELECT src_value FROM md_code_value_mappings WHERE code_domain=%s AND src_sys=%s AND src_field=%s",
        (domain, src_sys, src_field))}
    added, gaps = 0, []
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            for v, label in pairs:
                if v in existing:
                    print(f"  跳过：[{src_sys}] {src_field} {v}={label}（已有映射）")
                    continue
                std_v = std_by_label.get(label)
                if std_v is None:
                    near = [(sv, sl) for sv, sl in std if label in sl or sl in label]
                    if near:
                        print(f"⚠️ 「{v}={label}」无精确标准值，近似候选："
                              + "；".join(f"{sv}={sl}" for sv, sl in near)
                              + " —— 需人工裁决后再 align。", file=sys.stderr)
                    gaps.append((v, label))
                    continue
                cur.execute(
                    "INSERT INTO md_code_value_mappings "
                    "(code_domain,src_sys,src_field,src_value,src_label,std_value,note,updated_by) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (domain, src_sys, src_field, v, label, std_v, args.note or "", _operator()))
                added += 1
    finally:
        conn.close()
    print(f"对齐完成（操作人 {_operator()}）：新增 {added} 条；缺口 {len(gaps)} 个。")
    for v, label in gaps:
        print(f"  缺口：[{src_sys}] {src_field} {v}={label} → 无精确标准值，禁止硬映射。"
              f"处理：确认该含义是否属于码域「{domain}」，是则提案新增标准值，否则另建码域。")
    return 0 if not gaps else 2


def cmd_validate(args):
    std_vals = {r[0] for r in _fetch("SELECT std_value FROM md_code_values WHERE code_domain=%s", (args.domain,))}
    mp = _fetch("SELECT src_sys, src_value, std_value FROM md_code_value_mappings WHERE code_domain=%s", (args.domain,))
    if not std_vals and not mp:
        print(f"码域「{args.domain}」不存在")
        return 2
    srcs = {}
    bad = []
    for sys_, v, std_v in mp:
        srcs[sys_] = srcs.get(sys_, 0) + 1
        if std_v not in std_vals:
            bad.append((sys_, v, std_v))
    print(f"码域「{args.domain}」：标准值 {len(std_vals)} 个；映射 {len(mp)} 条，覆盖源系统 {len(srcs)} 个")
    for s, n in sorted(srcs.items()):
        print(f"  [{s}] {n} 条")
    for sys_, v, std_v in bad:
        print(f"  ⚠️ 悬空映射：[{sys_}] {v} → std={std_v}（标准值不存在）")
    return 0 if not bad else 2


def main():
    p = argparse.ArgumentParser(description="码值查询与多源对齐（MySQL 共享版）")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("domains").set_defaults(fn=cmd_domains)
    s = sub.add_parser("lookup"); s.add_argument("domain", nargs="+", help="一个或多个码域（空格分隔），批量一次查"); s.add_argument("--src"); s.set_defaults(fn=cmd_lookup)
    s = sub.add_parser("align")
    s.add_argument("domain"); s.add_argument("src_sys"); s.add_argument("src_field"); s.add_argument("values")
    s.add_argument("--note"); s.set_defaults(fn=cmd_align)
    s = sub.add_parser("validate"); s.add_argument("domain"); s.set_defaults(fn=cmd_validate)
    args = p.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
