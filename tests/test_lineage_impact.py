"""model-lineage-impact 引用解析单测。

覆盖：锚点写出表/分区推断、限定字段命中、裸列疑似命中、跨表同名列不误报、
子查询别名不外泄、别名冲突降级为疑似、where 片段不越界吞掉后续语句、TSV 落盘格式。
"""

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "lineage_impact", REPO / "model-lineage-impact" / "scripts" / "lineage_impact.py")
li = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(li)


def kinds(refs, field):
    return {h["kind"] for h in refs["hits"].get(field, [])}


def test_anchor_table_prefers_non_tmp_and_src_id():
    stmts = [
        "CREATE TABLE IF NOT EXISTS t_x (a string) partitioned by (src_id string)",
        "DROP TABLE IF EXISTS t_x_newest_VENDOR_A_TMP;",
        "insert overwrite table t_x_newest_VENDOR_A_TMP select 1 as a",
        "insert overwrite table t_x partition (src_id = 'VENDOR_A') select 1 as a",
    ]
    assert li.anchor_table(stmts) == "t_x"
    assert li.anchor_src_ids(stmts) == ["VENDOR_A"]


def test_qualified_field_hit_is_high_confidence():
    sql = "select a.mkt_cn_name as x from pdata_demo.ods_scr_base_info a where a.src_id = 'VENDOR_A'"
    refs = li.statement_refs(sql, "ods_scr_base_info", {"mkt_cn_name", "src_id"})
    assert refs["targetAliases"] == ["a"]
    assert refs["srcIds"] == ["VENDOR_A"]
    assert kinds(refs, "mkt_cn_name") == {"限定"}
    assert refs["hits"]["mkt_cn_name"][0]["downField"] == "x"


def test_bare_column_in_single_source_block_is_qualified():
    sql = "select mkt_cn_name as x from ods_scr_base_info where src_id = 'VENDOR_A'"
    refs = li.statement_refs(sql, "ods_scr_base_info", {"mkt_cn_name"})
    assert kinds(refs, "mkt_cn_name") == {"限定"}


def test_bare_column_in_multi_source_block_is_suspect():
    sql = ("select secu_id as z from ods_scr_base_info a "
           "join odata_vendor_a.other_tbl o on a.in_code = o.in_code")
    refs = li.statement_refs(sql, "ods_scr_base_info", {"secu_id"})
    assert kinds(refs, "secu_id") == {"疑似"}


def test_same_column_from_other_table_not_attributed():
    sql = "select b.mkt_cn_name as x from odata_vendor_a.other_tbl b"
    refs = li.statement_refs(sql, "ods_scr_base_info", {"mkt_cn_name"})
    assert "mkt_cn_name" not in refs["hits"]


def test_subquery_alias_does_not_leak():
    sql = "select N.mkt_cn_name as y from (select * from ods_scr_base_info_newest_VENDOR_A_TMP) N"
    refs = li.statement_refs(sql, "ods_scr_base_info", {"mkt_cn_name"})
    assert "mkt_cn_name" not in refs["hits"]


def test_alias_conflict_downgrades_to_suspect():
    sql = ("select a.mkt_cn_name as z from other_n.ods_scr_base_info a "
           "join odata_vendor_a.zzz a on 1 = 1")
    refs = li.statement_refs(sql, "ods_scr_base_info", {"mkt_cn_name"})
    assert refs["targetAliases"] == []
    assert kinds(refs, "mkt_cn_name") == {"疑似"}


def test_where_fragment_does_not_swallow_next_statement():
    sql = ("WITH c AS (select 1 as a from t where src_id = 'VENDOR_A') "
           "insert overwrite table t select 1 as a from c")
    frags = li.where_fragments(li.strip_comments(sql))
    assert frags and all("select" not in f.lower() for f in frags)
    assert len(frags[0]) < 40


def test_bare_column_counts_only_inside_target_block():
    sql = ("select t.remark as r from odata_vendor_a.other_tbl t where remark = 'x' ; "
           "select secu_id as z from ods_scr_base_info where src_id = 'VENDOR_A'")
    refs = li.statement_refs(sql, "ods_scr_base_info", {"secu_id", "remark"})
    assert kinds(refs, "secu_id") == {"限定"}      # 目标表单源块内的裸列
    assert "remark" not in refs["hits"]            # 其它表的同名裸列不算


def test_same_name_output_alias_is_flagged_separately():
    sql = "select b.mkt_ch_name as mkt_cn_name from ods_scr_base_info b where src_id = 'VENDOR_A'"
    refs = li.statement_refs(sql, "ods_scr_base_info", {"mkt_cn_name", "mkt_ch_name"})
    assert kinds(refs, "mkt_cn_name") == {"同名"}
    assert kinds(refs, "mkt_ch_name") == {"限定"}


def test_star_subquery_passes_through_as_qualified():
    sql = "select b.secu_id as s from (select * from ods_scr_base_info where src_id = 'VENDOR_A') b"
    refs = li.statement_refs(sql, "ods_scr_base_info", {"secu_id"})
    assert kinds(refs, "secu_id") == {"限定"}


def test_derived_subquery_column_downgrades_to_suspect():
    sql = ("select index.grp_val as v from (select a.secu_id grp_val from "
           "ods_scr_base_info a where src_id = 'VENDOR_A') index")
    refs = li.statement_refs(sql, "ods_scr_base_info", {"grp_val", "secu_id"})
    # index.grp_val 是子查询加工出来的列：不得判为"限定引用目标表字段"
    assert kinds(refs, "grp_val") == {"疑似", "同名"}


def test_write_tsv_uses_bom_and_flattens_whitespace(tmp_path):
    path = li.write_tsv(tmp_path / "a.tsv", ["甲", "乙"], [["1", "a\tb\nc"]])
    raw = Path(path).read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    assert "甲\t乙".encode("utf-8") in raw
    assert "1\ta b c".encode("utf-8") in raw
