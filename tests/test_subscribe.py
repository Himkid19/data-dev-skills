"""subscribe.py 的端到端冒烟测试。

在临时目录里构造一个假市场仓库（git init + marketplace.json + 假技能），
验证：首次订阅分发 → 版本升级报告 → 卸载清理。
不触碰用户 home：订阅前缀与分发目录都指向 tmp_path。
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "subscribe.py"


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True,
                   text=True)


@pytest.fixture()
def fake_market(tmp_path):
    """构造带一个假技能的 git 仓库，返回 (repo_dir, prefix, dest)。"""
    repo = tmp_path / "market-repo"
    (repo / "demo-skill").mkdir(parents=True)
    (repo / "demo-skill" / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: 测试技能。\n---\n\n# Demo\n",
        encoding="utf-8")
    # 应被排除的运行期文件，以及环境变量模板（模板要随分发带过去）
    (repo / "demo-skill" / ".env").write_text("SECRET=1", encoding="utf-8")
    (repo / "demo-skill" / ".env.example").write_text("SECRET=REPLACE_ME",
                                                      encoding="utf-8")
    (repo / "demo-skill" / "output").mkdir()
    (repo / "demo-skill" / "output" / "x.txt").write_text("x", encoding="utf-8")
    (repo / "marketplace.json").write_text(json.dumps({
        "name": "fake-market",
        "skills": [{
            "name": "demo-skill",
            "version": "1.0.0",
            "path": "./demo-skill",
            "summary": "测试技能",
        }],
    }, ensure_ascii=False), encoding="utf-8")
    git("init", "-q", cwd=repo)
    git("config", "user.email", "t@t", cwd=repo)
    git("config", "user.name", "t", cwd=repo)
    git("add", "-A", cwd=repo)
    git("commit", "-qm", "init", cwd=repo)
    return repo, tmp_path / "prefix", tmp_path / "dest"


def run_script(*args, expect_ok=True):
    result = subprocess.run([sys.executable, str(SCRIPT), *args],
                            capture_output=True, text=True, encoding="utf-8")
    if expect_ok:
        assert result.returncode == 0, result.stderr
    return result


def test_subscribe_update_uninstall(fake_market):
    repo, prefix, dest = fake_market

    # 1) 首次订阅到自定义目录
    out = run_script("--repo", str(repo), "--dest", str(dest), "--prefix", str(prefix))
    assert (dest / "demo-skill" / "SKILL.md").exists()
    assert not (dest / "demo-skill" / ".env").exists(), ".env 必须被排除"
    assert not (dest / "demo-skill" / "output").exists(), "output/ 必须被排除"
    assert "新增安装" in out.stdout

    sub = json.loads((prefix / "subscription.json").read_text(encoding="utf-8"))
    assert sub["market"] == "data-dev-skills"
    assert sub["skills"][0]["version"] == "1.0.0"

    # 2) bump 版本 + 加一个应被同步删除的残留文件，验证升级报告与完整镜像
    (repo / "demo-skill" / "extra.md").write_text("new file", encoding="utf-8")
    data = json.loads((repo / "marketplace.json").read_text(encoding="utf-8"))
    data["skills"][0]["version"] = "1.1.0"
    (repo / "marketplace.json").write_text(json.dumps(data), encoding="utf-8")
    git("add", "-A", cwd=repo)
    git("commit", "-qm", "bump 1.1.0", cwd=repo)
    stale = dest / "demo-skill" / "stale.txt"
    stale.write_text("stale", encoding="utf-8")

    out = run_script("--sync", "--repo", str(repo), "--prefix", str(prefix))
    assert "升级 1.0.0 -> 1.1.0" in out.stdout
    assert (dest / "demo-skill" / "extra.md").exists()
    assert not stale.exists(), "分发必须是先删后拷的完整镜像"

    # 3) status / uninstall
    out = run_script("--status", "--prefix", str(prefix))
    assert "demo-skill v1.1.0" in out.stdout
    run_script("--uninstall", "--prefix", str(prefix))
    assert not (dest / "demo-skill").exists()
    assert not (prefix / "subscription.json").exists()


def test_unknown_harness_rejected(fake_market):
    repo, prefix, _ = fake_market
    result = run_script("--repo", str(repo), "--harness", "nope",
                        "--prefix", str(prefix), expect_ok=False)
    assert result.returncode != 0


def test_env_preserved_and_hint(fake_market):
    repo, prefix, dest = fake_market

    # 1) 首次分发：源端 .env 被排除，目标只有 .env.example，输出环境变量提醒
    out = run_script("--repo", str(repo), "--dest", str(dest), "--prefix", str(prefix))
    assert (dest / "demo-skill" / ".env.example").exists()
    assert "需要配置环境变量" in out.stdout

    # 2) 用户配置 .env、产出交付物后再次同步：本地状态必须保留，提醒消失
    (dest / "demo-skill" / ".env").write_text("SECRET=real", encoding="utf-8")
    (dest / "demo-skill" / "output").mkdir()
    (dest / "demo-skill" / "output" / "delivery.tsv").write_text("x", encoding="utf-8")
    out = run_script("--sync", "--repo", str(repo), "--prefix", str(prefix))
    assert (dest / "demo-skill" / ".env").read_text(encoding="utf-8") == "SECRET=real"
    assert (dest / "demo-skill" / "output" / "delivery.tsv").exists()
    assert "需要配置环境变量" not in out.stdout


def _load_subscribe_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("subscribe_mod", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_auth_error_hint_only_for_credential_errors():
    mod = _load_subscribe_module()
    # 凭据类报错 → 给出令牌修复提示
    assert "Personal access tokens" in mod.auth_error_hint(
        "fatal: Authentication failed for 'https://github.com/org/repo.git/'")
    assert "Personal access tokens" in mod.auth_error_hint(
        "error: The requested URL returned error: 403")
    assert "Personal access tokens" in mod.auth_error_hint(
        "fatal: could not read Username for 'https://github.com': terminal prompts disabled")
    # 非凭据报错（网络）→ 不误报
    assert mod.auth_error_hint("fatal: unable to access '...': Could not resolve host") == ""


def test_market_identity_defaults():
    mod = _load_subscribe_module()
    assert mod.DEFAULT_PREFIX.name == ".data-dev-skills"
    assert mod.TASK_NAME == "data-dev-skills-sync"
