#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""model_builder 纯逻辑自检（不连 MySQL、不落盘）：python scripts/test_model_builder.py
覆盖：字段清单解析 / 默认归一 / 类词收尾校验 / 团队约定闸门 / 盖章。改这几个函数后先跑它。"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")
import model_builder as mb  # noqa: E402


def _plan(rows):
    """rows: [(src_en, src_ch, mdl_ch, mdl_en)] → validate() 能吃的 plan。"""
    out = []
    for src_en, src_ch, mdl_ch, en in rows:
        out.append({"src_en": src_en, "src_ch": src_ch, "mdl_ch": mdl_ch, "en": en,
                    "src": "root-compose", "roots_used": "", "note": "", "parsing": ("", "", "")})
    return out


def test_fields_tsv():
    with tempfile.TemporaryDirectory() as d:
        p4 = os.path.join(d, "a.tsv")
        open(p4, "w", encoding="utf-8").write(
            "源表中文名\t源表英文名\t字段中文名\t源字段英文名\n"
            "交易日历表\tdemo_scr_trd_cal\t数据日期\tbusi_date\n"
            "交易日历表\tdemo_scr_trd_cal\t备注\tremark\n")
        flds, meta = mb.parse_fields_tsv(p4)
        assert flds == [("busi_date", "数据日期"), ("remark", "备注")], flds
        assert meta == {"src_ch": "交易日历表", "src_en": "demo_scr_trd_cal"}, meta
        p2 = os.path.join(d, "b.tsv")
        open(p2, "w", encoding="utf-8").write("交易市场代码\tmkt_cd\n\n")
        flds, meta = mb.parse_fields_tsv(p2)
        assert flds == [("mkt_cd", "交易市场代码")], flds


def test_default_norm():
    assert mb.default_mdl_ch("是否交易日", set()) == ("交易日标志", "", "")
    assert mb.default_mdl_ch("上市日", set()) == ("上市日期", "", "")
    assert mb.default_mdl_ch("成交金额（万元）", set()) == ("成交金额", "单位万元；", "")
    assert mb.default_mdl_ch("认购（元）", set()) == ("认购金额", "单位元；", "金额")
    # 中文已含度量词尾时不重复追加（价格（元）≠ 价格金额）
    assert mb.default_mdl_ch("发行价格（元）", set())[0] == "发行价格"


def test_classifier_tail():
    tails = mb.classifier_tails()
    assert "no" in tails and "flag" in tails and "date" in tails
    # 裁决 A：ind 不再自动放行，标识按语义分流 id/flag
    assert "ind" not in tails
    # 号/号码/序号类中文必须带 no 类词
    import roots
    coll = roots.load_collocations()
    for zh in ("序号", "次序", "顺序", "序列"):
        assert coll.get(zh) == "seq_no", (zh, coll.get(zh))
    #  团队约定：次数并入 cnt，类词表里的 times 行删除
    import csv
    assert coll.get("次数") == "cnt", coll.get("次数")
    with open(os.path.join(mb.SKILL_DIR, "references", "mpc_classifiers.csv"), encoding="utf-8-sig") as f:
        abbrs = {(r.get("英文") or "").strip().lower() for r in csv.DictReader(f)}
    assert "cnt" in abbrs and "times" not in abbrs, abbrs

    def tail_errs(plan, flds):
        # 只看类词相关结论：标准字段必备校验与本用例无关
        return [e for e in mb.validate(plan, flds) if "缺类词" in e or "日期类" in e]

    # 用户  指出的缺陷：序号类必须以 no 收尾
    bad = _plan([("wk_trd_date_no", "该日期所在周第几个交易日", "周交易日序号", "wk_trd_day_seq")])
    errs = tail_errs(bad, [("wk_trd_date_no", "该日期所在周第几个交易日")])
    assert any("缺类词" in e for e in errs), errs
    # 修好后必须放行（且不再误报 _date）
    good = _plan([("wk_trd_date_no", "该日期所在周第几个交易日", "周交易日序号", "wk_trd_day_seq_no")])
    errs = tail_errs(good, [("wk_trd_date_no", "该日期所在周第几个交易日")])
    assert errs == [], errs
    # 标准字段（§4 无类词的既有名）与「类词豁免」标注放行
    std = _plan([("remark", "备注", "备注", "remark")])
    assert tail_errs(std, [("remark", "备注")]) == []
    exc = _plan([("a", "某某", "某某", "abc")])
    exc[0]["note"] = "类词豁免：存量兼容"
    assert tail_errs(exc, [("a", "某某")]) == []
    # _ind 结尾（历史旧根 标识→Ind）必须拦下，交团队约定或改 id/flag
    ind = _plan([("a", "内部标识", "内部标识", "inr_ind")])
    assert any("缺类词" in e for e in tail_errs(ind, [("a", "内部标识")])), ind
    ind[0]["note"] = "类词豁免：存量兼容 fnd_base_info"
    assert tail_errs(ind, [("a", "内部标识")]) == []


def test_src_tail():
    # 源英文尾 → 目标同类词尾：corp_id→co_id、subordinate_flag→…_flag
    plan = [{"src_en": "corp_id", "src_ch": "机构编号", "mdl_ch": "机构编号", "en": "co_no",
             "src": "root-compose", "roots_used": "机构→co", "note": "", "src_tail": "id"},
            {"src_en": "subordinate_flag", "src_ch": "次级债混合资本债标识", "mdl_ch": "次级债混合资本债标识",
             "en": "subordinate_compd_cptl_bond_ind", "src": "root-compose", "roots_used": "",
             "note": "", "src_tail": "flag"}]
    mb.apply_src_tail(plan)
    assert [p["en"] for p in plan] == ["co_id", "subordinate_compd_cptl_bond_flag"], plan
    # 已同类词尾、或源英文尾无关的一律不动
    plan = [{"src_en": "corp_id", "src_ch": "机构编号", "mdl_ch": "机构编号", "en": "co_id",
             "src": "root-compose", "roots_used": "", "note": "", "src_tail": "id"},
            {"src_en": "corp_no", "src_ch": "机构编号", "mdl_ch": "机构编号", "en": "co_no",
             "src": "root-compose", "roots_used": "", "note": "", "src_tail": ""}]
    mb.apply_src_tail(plan)
    assert [p["en"] for p in plan] == ["co_id", "co_no"]
    # 转换后必须过类词校验
    assert all("缺类词" not in e for e in mb.validate(plan, [("corp_id", "机构编号"), ("corp_no", "机构编号")]))


def test_approval_gate():
    # 超 25 位 / 超 5 段：默认拒跑，用户审批留痕后放行
    long_en = "subordinate_compd_cptl_debt_flag"
    plan = _plan([("subordinate_flag", "次级债混合资本债标志", "次级债混合资本债标志", long_en)])
    plan[0]["src"] = "needs-approval(7段>5)"
    errs = mb.validate(plan, [("subordinate_flag", "次级债混合资本债标志")])
    assert any("超5段需审批" in e for e in errs) and any("超25位需审批" in e for e in errs), errs
    plan[0]["note"] = "已审批：权威案例 subordinate_compd_cptl_debt_flag"
    errs = [e for e in mb.validate(plan, [("subordinate_flag", "次级债混合资本债标志")]) if "需审批" in e]
    assert errs == [], errs


def test_confirm_gate_and_stamp():
    norm = {"busi_date": (None, None, "", "")}
    assert mb.check_confirmed([("busi_date", "数据日期"), ("mkt_cd", "交易市场代码")], norm) == ["mkt_cd"]
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "norm.tsv")
        open(p, "w", encoding="utf-8").write("src_fld_en\tmdl_fld_ch\tmdl_fld_en\tnote\nmkt_cd\t交易市场代码\tmkt_cd\t\n")
        mb.stamp_confirmed(p, "徐高飞")
        rows = [r for r in open(p, encoding="utf-8").read().splitlines() if r.strip()]
        assert rows[0].split("\t")[4] == "confirmed", rows[0]
        assert rows[1].split("\t")[4].startswith("徐高飞 "), rows[1]
        assert mb.check_confirmed([("mkt_cd", "交易市场代码")], mb.load_norm(p)) == []


if __name__ == "__main__":
    for fn in [test_fields_tsv, test_default_norm, test_classifier_tail, test_src_tail,
               test_approval_gate, test_confirm_gate_and_stamp]:
        fn()
        print(f"✓ {fn.__name__}")
    print("全部通过")
