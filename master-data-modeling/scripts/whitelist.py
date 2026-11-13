#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
金融缩写白名单 CRUD（mpc-naming-rules.md §2 界定标准的执行入口）。

白名单内的缩写跳过词根处理直接使用（如 PE、TTM）。维护规则：
  - 新增/修改涉及专业词汇时，必须先和用户沟通确认，再执行写入，之后才能继续模型设计；
  - basis 必填，取值 1-4（见 mpc-naming-rules.md §2 界定标准）；
  - 文件为 references/fin_abbr_whitelist.csv，随 skill 版本管理；roots.py translate 自动加载。

子命令：
  list [--basis N]                  查看白名单（可按界定标准过滤）
  add <abbr> <full_name> <zh> --basis N [--note ...] [--force]
  update <abbr> [--full-name ...] [--zh ...] [--basis N] [--note ...]
  remove <abbr>
"""
import argparse
import csv
import datetime
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
WHITELIST_CSV = os.path.join(os.path.dirname(BASE), "references", "fin_abbr_whitelist.csv")
FIELDS = ["abbr", "full_name", "zh", "basis", "added_by", "added_at", "note"]
BASIS_DESC = {
    "1": "国际/行业标准编码（ISO 4217/6166/10383/17442/10962 等）",
    "2": "证券期货业通用术语（PE/PB/EPS/ROE/TTM 等无歧义惯例缩写）",
    "3": "仓库现有模型高频使用（可用 rg 在模型目录检索佐证）",
    "4": "词根治理组登记认可的缩写",
}


def _operator():
    return os.environ.get("MD_OPERATOR") or "unknown"


def _read():
    if not os.path.exists(WHITELIST_CSV):
        return []
    with open(WHITELIST_CSV, encoding="utf-8-sig") as f:
        return [r for r in csv.DictReader(f) if (r.get("abbr") or "").strip()]


def _write(rows):
    with open(WHITELIST_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def _find(rows, abbr):
    return next((r for r in rows if r["abbr"].lower() == abbr.lower()), None)


def cmd_list(args):
    rows = _read()
    if args.basis:
        rows = [r for r in rows if r.get("basis") == args.basis]
    if not rows:
        print("（白名单为空）")
        return 0
    print("abbr\tfull_name\tzh\tbasis\tadded_by\tadded_at\tnote")
    for r in rows:
        print(f"{r['abbr']}\t{r.get('full_name','')}\t{r.get('zh','')}\t{r.get('basis','')}"
              f"\t{r.get('added_by','')}\t{r.get('added_at','')}\t{r.get('note','')}")
    print(f"\n共 {len(rows)} 条。界定标准：" + "；".join(f"{k}={v}" for k, v in BASIS_DESC.items()))
    return 0


def cmd_add(args):
    if args.basis not in BASIS_DESC:
        print(f"⚠️ basis 必须是 1-4：{'；'.join(f'{k}={v}' for k, v in BASIS_DESC.items())}")
        return 2
    rows = _read()
    if _find(rows, args.abbr) and not args.force:
        print(f"⚠️ 白名单已存在 {args.abbr}，确认修改请加 --force（或用 update）。")
        return 2
    today = datetime.date.today().isoformat()
    entry = {"abbr": args.abbr, "full_name": args.full_name, "zh": args.zh,
             "basis": args.basis, "added_by": _operator(), "added_at": today,
             "note": args.note or BASIS_DESC[args.basis]}
    rows = [r for r in rows if r["abbr"].lower() != args.abbr.lower()]
    rows.append(entry)
    _write(rows)
    print(f"已加入白名单：{args.abbr}（{args.zh}，依据条款{args.basis}）。"
          f"translate 将跳过词根处理直接使用该缩写。")
    return 0


def cmd_update(args):
    rows = _read()
    r = _find(rows, args.abbr)
    if not r:
        print(f"⚠️ 白名单不存在 {args.abbr}，用 add 新增。")
        return 2
    if args.full_name:
        r["full_name"] = args.full_name
    if args.zh:
        r["zh"] = args.zh
    if args.basis:
        if args.basis not in BASIS_DESC:
            print(f"⚠️ basis 必须是 1-4")
            return 2
        r["basis"] = args.basis
    if args.note:
        r["note"] = args.note
    _write(rows)
    print(f"已更新：{r['abbr']} → 全称={r['full_name']} 中文={r['zh']} 依据条款={r['basis']}")
    return 0


def cmd_remove(args):
    rows = _read()
    r = _find(rows, args.abbr)
    if not r:
        print(f"⚠️ 白名单不存在 {args.abbr}。")
        return 2
    rows = [x for x in rows if x["abbr"].lower() != args.abbr.lower()]
    _write(rows)
    print(f"已移除：{args.abbr}（{r.get('zh','')}）。此后该缩写不再跳过词根处理。")
    return 0


def main():
    p = argparse.ArgumentParser(description="金融缩写白名单 CRUD")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("list"); s.add_argument("--basis", choices=["1", "2", "3", "4"])
    s.set_defaults(fn=cmd_list)
    s = sub.add_parser("add")
    s.add_argument("abbr"); s.add_argument("full_name"); s.add_argument("zh")
    s.add_argument("--basis", required=True, help="界定依据条款 1-4")
    s.add_argument("--note"); s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_add)
    s = sub.add_parser("update")
    s.add_argument("abbr")
    s.add_argument("--full-name", dest="full_name"); s.add_argument("--zh")
    s.add_argument("--basis"); s.add_argument("--note")
    s.set_defaults(fn=cmd_update)
    s = sub.add_parser("remove"); s.add_argument("abbr"); s.set_defaults(fn=cmd_remove)
    args = p.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
