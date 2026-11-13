"""setup_env.py 的行为测试：从 .env.example 生成/原地更新 .env，值不回显。"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "setup_env.py"


def run_script(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, encoding="utf-8")


@pytest.fixture()
def skill_root(tmp_path):
    repo = tmp_path / "repo"
    skill = repo / "demo-skill"
    skill.mkdir(parents=True)
    (skill / ".env.example").write_text(
        "# 注释行\n"
        "MD_OPERATOR=你的账号\n"
        "MYSQL_HOST=REPLACE_ME\n"
        "# MYSQL_PORT=3304\n",
        encoding="utf-8")
    (repo / "marketplace.json").write_text(json.dumps({
        "name": "t",
        "skills": [{
            "name": "demo-skill", "version": "1.0.0", "path": "./demo-skill",
            "env": {"vars": [
                {"name": "MD_OPERATOR", "required": True, "source": "self",
                 "guide": "填你自己的账号"},
                {"name": "MYSQL_HOST", "required": True, "source": "developer"},
            ]},
        }],
    }, ensure_ascii=False), encoding="utf-8")
    return repo, skill


def test_list_reports_status_and_who(skill_root):
    repo, skill = skill_root
    out = run_script("--repo", str(repo), "--skill", "demo-skill",
                     "--dir", str(skill), "--list")
    assert out.returncode == 0, out.stderr
    assert "MD_OPERATOR" in out.stdout and "待填(占位符)" in out.stdout
    assert "MYSQL_HOST" in out.stdout and "找数据平台管理员" in out.stdout


def test_set_writes_env_from_example(skill_root):
    repo, skill = skill_root
    out = run_script("--repo", str(repo), "--skill", "demo-skill", "--dir", str(skill),
                     "--set", "MD_OPERATOR=zhangsan", "--set", "MYSQL_HOST=10.0.0.1")
    assert out.returncode == 0, out.stderr
    env = (skill / ".env").read_text(encoding="utf-8")
    assert "MD_OPERATOR=zhangsan" in env
    assert "MYSQL_HOST=10.0.0.1" in env
    assert "# 注释行" in env           # 注释保留
    assert "# MYSQL_PORT=3304" in env  # 注释行不被误当变量覆盖
    assert "zhangsan" not in out.stdout  # 值不回显到终端
    assert (skill / ".env.example").exists()  # 模板保留


def test_set_updates_existing_env_in_place(skill_root):
    repo, skill = skill_root
    (skill / ".env").write_text("MD_OPERATOR=old\nMYSQL_HOST=keep\n", encoding="utf-8")
    out = run_script("--repo", str(repo), "--skill", "demo-skill", "--dir", str(skill),
                     "--set", "MD_OPERATOR=new")
    assert out.returncode == 0, out.stderr
    env = (skill / ".env").read_text(encoding="utf-8")
    assert "MD_OPERATOR=new" in env and "old" not in env
    assert "MYSQL_HOST=keep" in env   # 未指定的键原样保留
    listed = run_script("--repo", str(repo), "--skill", "demo-skill",
                        "--dir", str(skill), "--list")
    assert "已配置" in listed.stdout
