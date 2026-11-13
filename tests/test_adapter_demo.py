"""Offline end-to-end tests for the two metadata skills (mock adapter).

These run the real skill scripts against the synthetic fixtures in
``packages/datadev_adapters/fixtures/demo``.  No network, no credentials.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MOCK_CONFIG = REPO / "adapter-configs" / "example.mock.json"


def run_skill(script, args, cwd):
    env = os.environ.copy()
    env.pop("DATADEV_ADAPTER_CONFIG", None)
    env.pop("DATADEV_API_TOKEN", None)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, str(script), *map(str, args)],
        cwd=str(cwd), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )


def test_source_mapping_end_to_end(tmp_path):
    skill = REPO / "master-data-source-mapping"
    out = tmp_path / "collect"
    result = run_skill(
        skill / "scripts" / "collect_mapping.py",
        ["dm_demo_security", "--adapter-config", MOCK_CONFIG, "--out", out],
        cwd=skill,
    )
    assert result.returncode == 0, result.stderr
    assert "master columns: 4" in result.stdout
    assert "models: 1" in result.stdout

    mapping = json.loads((out / "mapping.json").read_text(encoding="utf-8"))
    assert mapping["master"]["name"] == "dm_demo_security"
    assert len(mapping["models"]) == 1
    assert len(mapping["odata"]) == 2
    assert not mapping["errors"]

    tsv_out = tmp_path / "tsv"
    result = run_skill(
        skill / "scripts" / "build_tsv.py",
        [out / "mapping.json", "--out", tsv_out],
        cwd=skill,
    )
    assert result.returncode == 0, result.stderr
    assert (tsv_out / "1_source_tables.tsv").exists()
    assert (tsv_out / "2_field_mapping.tsv").exists()

    header = (tsv_out / "1_source_tables.tsv").read_text(
        encoding="utf-8-sig").splitlines()[0]
    assert "来源" in header


def test_lineage_impact_end_to_end(tmp_path):
    skill = REPO / "model-lineage-impact"
    out = tmp_path / "lineage"
    result = run_skill(
        skill / "scripts" / "lineage_impact.py",
        ["--task", "1001", "--depth", "1", "--field", "scr_cn_full_name",
         "--names", "--adapter-config", MOCK_CONFIG, "--out", out],
        cwd=skill,
    )
    assert result.returncode == 0, result.stderr
    assert "下游任务" in result.stdout

    for name in ("0_scope.tsv", "1_downstream_tasks.tsv",
                 "2_field_impact.tsv", "3_reference_detail.tsv", "impact.json"):
        assert (out / name).exists(), name

    impact = json.loads((out / "impact.json").read_text(encoding="utf-8"))
    assert impact["target"]["table"] == "pdata_demo_security"
    assert impact["scope"]["downstreamTasks"] == 1
    assert "scr_cn_full_name" in impact["target"]["scanFields"]
