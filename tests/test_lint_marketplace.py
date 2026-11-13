"""lint_marketplace.py 的一致性检测测试。

覆盖：UNLISTED 自动入册、DEAD 自动移除、DRIFT（内容改动但版本未 bump）检测与
bump 后的基线刷新。全部在 tmp 仓库内进行，不影响真实 marketplace.json。
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "lint_marketplace", REPO / "scripts" / "lint_marketplace.py")
lint = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lint)


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True,
                   text=True)


def commit_all(repo, msg):
    git("add", "-A", cwd=repo)
    git("commit", "-qm", msg, cwd=repo)


def make_skill(repo, name, body="# demo\n"):
    d = repo / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: 测试技能 {name}。\n---\n\n{body}",
        encoding="utf-8")


def write_marketplace(repo, skills):
    (repo / "marketplace.json").write_text(json.dumps({
        "name": "test-market", "skills": skills,
    }, ensure_ascii=False), encoding="utf-8")


@pytest.fixture()
def market_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    write_marketplace(repo, [])
    git("init", "-q", cwd=repo)
    git("config", "user.email", "t@t", cwd=repo)
    git("config", "user.name", "t", cwd=repo)
    commit_all(repo, "init")
    return repo


def codes(issues):
    return sorted(i["code"] for i in issues)


def test_unlisted_autolist_and_clean(market_repo):
    make_skill(market_repo, "skill-a")
    commit_all(market_repo, "add skill-a")

    issues, _, _ = lint.analyze(market_repo)
    assert codes(issues) == ["UNLISTED"]

    # --fix：自动入册，摘要取自 SKILL.md description
    data = lint.load(market_repo)
    fixed, refused = lint.apply_fixes(issues, data, dict(lint.skill_dirs(market_repo)),
                                      root=market_repo)
    assert not refused and any("入册 skill-a" in f for f in fixed)
    lint.save(market_repo, data)

    data = lint.load(market_repo)
    entry = data["skills"][0]
    assert entry["name"] == "skill-a"
    assert entry["version"] == "0.1.0"
    assert "测试技能" in entry["summary"]
    assert entry["baseline"]["commit"]

    issues, _, _ = lint.analyze(market_repo)
    assert issues == []


def test_dead_entry_removed(market_repo):
    make_skill(market_repo, "skill-a")
    write_marketplace(market_repo, [
        {"name": "skill-a", "version": "1.0.0", "path": "./skill-a"},
        {"name": "ghost", "version": "1.0.0", "path": "./ghost"},
    ])
    commit_all(market_repo, "register")

    issues, _, _ = lint.analyze(market_repo)
    assert "DEAD" in codes(issues) and "NOBASELINE" in codes(issues)

    data = lint.load(market_repo)
    fixed, _ = lint.apply_fixes(issues, data, dict(lint.skill_dirs(market_repo)),
                                root=market_repo)
    lint.save(market_repo, data)
    assert [e["name"] for e in lint.load(market_repo)["skills"]] == ["skill-a"]
    issues, _, _ = lint.analyze(market_repo)
    assert issues == []


def test_drift_requires_manual_bump(market_repo):
    make_skill(market_repo, "skill-a")
    write_marketplace(market_repo, [
        {"name": "skill-a", "version": "1.0.0", "path": "./skill-a"},
    ])
    commit_all(market_repo, "register")

    # 建立基线（--fix 的 NOBASELINE 路径）
    issues, data, dirs = lint.analyze(market_repo)
    lint.apply_fixes(issues, data, dirs, root=market_repo)
    lint.save(market_repo, data)
    commit_all(market_repo, "baseline")
    assert lint.analyze(market_repo)[0] == []

    # 技能内容有新提交但版本未变：DRIFT，--fix 拒绝代劳
    make_skill(market_repo, "skill-a", body="# demo v2，规则改了\n")
    commit_all(market_repo, "touch skill")
    issues, _, _ = lint.analyze(market_repo)
    assert codes(issues) == ["DRIFT"]
    data2 = lint.load(market_repo)
    _, refused = lint.apply_fixes(issues, data2, dict(lint.skill_dirs(market_repo)),
                                  root=market_repo)
    assert refused

    # 人工 bump 后 --fix 刷新基线，恢复全绿
    write_marketplace(market_repo, [
        {"name": "skill-a", "version": "1.1.0", "path": "./skill-a",
         "summary": "s", "baseline": lint.load(market_repo)["skills"][0]["baseline"]},
    ])
    issues, data, dirs = lint.analyze(market_repo)
    assert codes(issues) == ["BASELINE_STALE"]
    lint.apply_fixes(issues, data, dirs, root=market_repo)
    lint.save(market_repo, data)
    issues, _, _ = lint.analyze(market_repo)
    assert issues == []
