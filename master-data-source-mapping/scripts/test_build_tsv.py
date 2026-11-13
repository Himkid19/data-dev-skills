#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""来源分组的自检：同一来源优先取「select 别名 = 主数据字段」的项，
dm 层 CASE 的判断输入列不得混入（demo_bond_base_info.coup_var 的回归点）。
    python scripts/test_build_tsv.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_tsv import collect_field_sources, match_situation

CASE = ("case when a.B_COUPON_TYPE = '1' then '固定利率' "
        "when a.B_COUPON_TYPE = '2' then '浮动利率' end")


def item(field, expr, col=None):
    return {"field": field, "expr": expr,
            "srcRefs": [{"db": "odata_vendor_a", "table": "vendor_bond_master", "column": col,
                         "alias": "a"}] if col else []}


def stmt(items):
    return {"kind": "insert", "items": items, "joinText": {},
            "aliases": {"a": {"db": "odata_vendor_a", "table": "vendor_bond_master"}},
            "mainTable": {"db": "odata_vendor_a", "table": "vendor_bond_master"}}


MAPPING = {
    "master": {"name": "t", "comment": "", "columns": [
        {"name": "coup_var", "comment": "息票品种", "codedsetCode": None},
        {"name": "alt_rate", "comment": "别名承载", "codedsetCode": None}]},
    "odata": [{"db": "odata_vendor_a", "table": "vendor_bond_master", "comment": "债券表",
               "qualifiedName": "odata_vendor_a.vendor_bond_master",
               "columns": {"b_coupon_type": "息票类型", "b_coupon": "票面利率"}}],
    "models": [{"name": "ods_bond_base_info", "tasks": [{
        "taskId": "1", "taskName": "ods_bond_base_info_VENDOR_A", "srcId": "VENDOR_A",
        "inDmUpstream": True, "statements": [stmt([
            item("coup_var", CASE, "b_coupon_type"),            # 精确同名项 = 值源
            item("ref_yield", "a.REF_YIELD*100", "b_coupon"),   # 仅被 dm 判断条件引用
            item("alt_rate", "a.B_COUPON*100", "b_coupon")])]}]}],
    "dmFieldLogic": {"coup_var": {
        "expr": "coup_var2", "multiSource": True, "alias": "coup_var",
        "chain": ["tmp: case when src_id = 'VENDOR_A' and coalesce(ref_yield, alt_rate) = '0' "
                  "then '零息' else trim(coup_var) end"]}},
}

groups = collect_field_sources(MAPPING)
g = groups["coup_var"][0]
assert [f["col"] for f in g["fields"]] == ["b_coupon_type"], g["fields"]
assert g["logics"] == [CASE], g["logics"]
assert match_situation(groups["coup_var"], MAPPING["dmFieldLogic"]["coup_var"]) == "多源判断(仅加工逻辑)"
# 无同名项时仍回退 dm 链候选（alt_rate 只在 loose 命中）
g2 = groups["alt_rate"][0]
assert [f["col"] for f in g2["fields"]] == ["b_coupon"], g2["fields"]
assert g2["logics"] == ["a.B_COUPON*100"], g2["logics"]
print("test_build_tsv OK")
