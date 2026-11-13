#!/usr/bin/env python3
"""data-dev-skills 订阅客户端：一次订阅，持续同步。

把本仓库当作一个 git 订阅式 skill 市场：
  1. 首次订阅：在任意位置 clone 本仓库，然后
       python scripts/subscribe.py --harness claude --harness codex
     会把 marketplace.json 里登记的技能分发到各 harness 的技能目录。
  2. 日常更新：
       python scripts/subscribe.py --sync
     （= git pull + 重新分发 + 版本变化报告）
  3. 自动更新：--register-task 注册每日计划任务；或在 agent 对话里说
     "更新 data-dev 技能"（由 data-dev-skill-sync 元技能触发本脚本）。

无第三方依赖，Python 3.8+。
"""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MARKETPLACE_FILE = "marketplace.json"

# 订阅状态目录（可用 --prefix 覆盖，便于测试）
DEFAULT_PREFIX = Path.home() / ".data-dev-skills"
SUBSCRIPTION_FILE = "subscription.json"
REPO_DIRNAME = "repo"

# 各 harness 的用户级技能目录（相对 Path.home()）
HARNESS_DIRS = {
    "workbuddy": ".codebuddy/skills",  # WorkBuddy（腾讯 CodeBuddy 内核）用户级技能目录
    "agents": ".agents/skills",        # Agent Skills 开放格式通用目录，多数新 harness 可识别
    "zcode": ".zcode/skills",
    "claude": ".claude/skills",
    "codex": ".codex/skills",
    "cursor": ".cursor/skills",
}

# 分发时排除的文件/目录（运行期产物与本地凭据，不进用户技能目录）
EXCLUDE_PATTERNS = [
    ".git", ".pytest_cache", "__pycache__", "*.pyc", ".env",
    "output", ".venv", "venv", ".idea", ".vscode", "node_modules",
    ".DS_Store", "Thumbs.db", "*.egg-info", "*.log",
]

# 先删后拷时目标端要保留的用户本地内容（凭据与运行期交付物，不在仓库里，
# 删了就无法恢复）：.env = 用户填好的环境变量，output/ = 技能运行期产物
PRESERVE_TARGET = [".env", "output"]

TASK_NAME = "data-dev-skills-sync"

# git 报错里出现这些字样，基本可判定为代码平台凭据问题（令牌过期/权限不足）
AUTH_ERROR_MARKERS = (
    "authentication", "401", "403", "could not read username",
    "invalid credentials", "permission denied", "terminal prompts disabled",
)


def eprint(*args):
    print(*args, file=sys.stderr)


def auth_error_hint(detail):
    """git 报错文本像是凭据问题时，返回给用户的修复提示；否则返回空串。"""
    low = (detail or "").lower()
    if not any(marker in low for marker in AUTH_ERROR_MARKERS):
        return ""
    return ("[hint] 看起来是代码托管平台凭据问题：到 GitHub → Settings → Developer settings → "
            "Personal access tokens 重新生成令牌（或确认仓库可见性/SSO 授权），再执行 "
            "git remote set-url origin "
            "\"https://<user>:<new-token>@github.com/<your-org>/data-dev-skills.git\" "
            "后重试。")


def run(cmd, cwd=None, check=True, capture=True):
    result = subprocess.run(
        cmd, cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=True, encoding="utf-8", errors="replace",
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"命令执行失败 {' '.join(map(str, cmd))}\n{detail}")
    return result


def load_marketplace(repo_root):
    path = repo_root / MARKETPLACE_FILE
    data = json.loads(path.read_text(encoding="utf-8"))
    skills = []
    for entry in data.get("skills", []):
        skills.append({
            "name": entry["name"],
            "version": entry.get("version", "0.0.0"),
            "path": (repo_root / entry["path"]).resolve(),
            "summary": entry.get("summary", ""),
            "env": entry.get("env") or {},
        })
    return data, skills


def load_subscription(prefix):
    path = prefix / SUBSCRIPTION_FILE
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_subscription(prefix, sub):
    prefix.mkdir(parents=True, exist_ok=True)
    (prefix / SUBSCRIPTION_FILE).write_text(
        json.dumps(sub, ensure_ascii=False, indent=2), encoding="utf-8")


def git_pull(repo_root, quiet=False):
    """ff-only 拉取远端更新；离线或冲突时保留本地版本并给出警告。"""
    head_before = run(["git", "rev-parse", "HEAD"], cwd=repo_root).stdout.strip()
    fetch = run(["git", "pull", "--ff-only"], cwd=repo_root, check=False)
    if fetch.returncode != 0 and not quiet:
        detail = (fetch.stderr or "").strip()
        eprint(f"[warn] git pull 失败（离线或本地有改动），使用本地版本继续：\n{detail}")
        hint = auth_error_hint(detail)
        if hint:
            eprint(hint)
    head_after = run(["git", "rev-parse", "HEAD"], cwd=repo_root).stdout.strip()
    return head_before, head_after


def new_commit_log(repo_root, before, after):
    if before == after:
        return []
    log = run(["git", "log", "--oneline", f"{before}..{after}"], cwd=repo_root,
              check=False)
    return [line for line in (log.stdout or "").splitlines() if line.strip()]


def harness_targets(harnesses, dest=None):
    """返回 [(harness名, 目标目录)]。dest 提供时作为唯一目标。"""
    if dest:
        return [("custom", Path(dest).expanduser())]
    targets = []
    for name in harnesses:
        if name not in HARNESS_DIRS:
            raise SystemExit(
                f"未知的 harness：{name}。可选：{', '.join(HARNESS_DIRS)}")
        targets.append((name, Path.home() / HARNESS_DIRS[name]))
    return targets


def _preserve_local_state(dst: Path):
    """把目标端用户本地内容（.env / output/）移到临时目录，返回暂存路径或 None。"""
    existing = [name for name in PRESERVE_TARGET if (dst / name).exists()]
    if not existing:
        return None
    staging = Path(tempfile.mkdtemp(prefix="data-dev-skills-"))
    for name in existing:
        shutil.move(str(dst / name), str(staging / name))
    return staging


def _restore_local_state(staging, dst: Path):
    """把暂存的用户本地内容移回目标端；目标若已出现同名（异常情况）则不覆盖。"""
    for item in list(staging.iterdir()):
        target = dst / item.name
        if not target.exists():
            shutil.move(str(item), str(target))
    shutil.rmtree(staging, ignore_errors=True)


def distribute(skills, targets, quiet=False):
    """把技能目录完整镜像到各目标（先删后拷，保证下架的文件同步消失）。

    用户在技能目录里配置的 .env 和运行期 output/ 不在仓库里，删除前先暂存、
    拷贝后还原，更新不丢本地状态。
    """
    ignore = shutil.ignore_patterns(*EXCLUDE_PATTERNS)
    for harness, target in targets:
        for skill in skills:
            if not (skill["path"] / "SKILL.md").exists():
                eprint(f"[warn] 跳过 {skill['name']}：{skill['path']} 下没有 SKILL.md")
                continue
            dst = target / skill["name"]
            staging = _preserve_local_state(dst) if dst.exists() else None
            try:
                if dst.exists():
                    shutil.rmtree(dst)
                target.mkdir(parents=True, exist_ok=True)
                shutil.copytree(skill["path"], dst, ignore=ignore)
            finally:
                if staging is not None:
                    _restore_local_state(staging, dst)
        if not quiet:
            eprint(f"[ok] {harness} -> {target}（{len(skills)} 个技能）")


def env_hints(skills, targets):
    """返回需要提醒配置环境变量的技能：[(技能名, env 元数据)]。

    判定：技能带 .env.example 模板，且本次所有分发目标下都没有用户配好的 .env。
    env 元数据来自 marketplace.json（变量 / 是否必填 / 谁提供 / 获取引导）。
    """
    missing = []
    for skill in skills:
        if not (skill["path"] / ".env.example").exists():
            continue
        if targets and all(not (t / skill["name"] / ".env").exists() for _, t in targets):
            missing.append((skill["name"], skill.get("env") or {}))
    return missing


def cmd_sync(args, prefix):
    repo_root = Path(args.repo).expanduser().resolve() if args.repo else REPO_ROOT
    sub = load_subscription(prefix)

    # 订阅面解析：--harness 显式给出则替换 harness 列表；--dest 给出则替换自定义
    # 目录；否则整体沿用上次保存的（harnesses + custom_dest 并存，全部持续同步）
    if args.harness:
        harnesses = args.harness
        custom_dest = str(Path(args.dest).expanduser()) if args.dest else (sub or {}).get("custom_dest")
    elif args.dest:
        harnesses = (sub or {}).get("harnesses", [])
        custom_dest = str(Path(args.dest).expanduser())
    elif sub and (sub.get("harnesses") or sub.get("custom_dest")):
        harnesses = sub.get("harnesses", [])
        custom_dest = sub.get("custom_dest")
    else:
        raise SystemExit(
            "尚未订阅任何 harness。首次使用请指定，例如：\n"
            "  python scripts/subscribe.py --harness agents --harness claude\n"
            "或用 --dest 指定一个自定义目录。")

    if not (repo_root / MARKETPLACE_FILE).exists():
        raise SystemExit(f"{repo_root} 下没有 {MARKETPLACE_FILE}，请确认仓库路径。")

    old_versions = {s["name"]: s["version"] for s in (sub or {}).get("skills", [])}
    head_before, head_after = git_pull(repo_root, quiet=args.quiet)
    _, skills = load_marketplace(repo_root)

    # 仓库里含 SKILL.md 但未登记 marketplace.json 的目录：提示维护者补录
    listed = {s["name"] for s in skills}
    unlisted = [d.name for d in repo_root.iterdir()
                if d.is_dir() and not d.name.startswith(".")
                and (d / "SKILL.md").exists() and d.name not in listed]
    if unlisted:
        print(f"\n[hint] 仓库中有未上架技能目录：{', '.join(sorted(unlisted))}"
              "（含 SKILL.md 但未登记 marketplace.json）——"
              "请维护者运行 scripts/lint_marketplace.py --fix 入册")

    targets = harness_targets(harnesses, dest=None)
    if custom_dest:
        targets.append(("custom", Path(custom_dest)))
    distribute(skills, targets, quiet=args.quiet)

    save_subscription(prefix, {
        "market": "data-dev-skills",
        "repo": str(repo_root),
        "commit": head_after,
        "synced_at": datetime.now().isoformat(timespec="seconds"),
        "harnesses": harnesses,
        "custom_dest": custom_dest,
        "skills": [{"name": s["name"], "version": s["version"]} for s in skills],
    })

    # 版本变化报告
    print(f"同步完成：commit {head_after[:8]}，分发到 {len(targets)} 个目录")
    for skill in skills:
        old_v = old_versions.get(skill["name"])
        if old_v is None:
            mark = "新增安装"
        elif old_v != skill["version"]:
            mark = f"升级 {old_v} -> {skill['version']}"
        else:
            mark = "已是最新"
        print(f"  - {skill['name']} v{skill['version']}：{mark}")
    commits = new_commit_log(repo_root, head_before, head_after)
    if commits:
        print(f"本次拉取到 {len(commits)} 个新提交：")
        for line in commits[:10]:
            print(f"    {line}")

    # 环境变量配置提醒（未检测到 .env 的技能；已配置的更新时自动保留）
    missing_env = env_hints(skills, targets)
    if missing_env:
        print("\n⚠️  以下技能需要配置环境变量才能运行（未检测到 .env）：")
        for name, meta in missing_env:
            print(f"  - {name}")
            for var in meta.get("vars", []):
                if not var.get("required", True):
                    continue
                who = "你自己填" if var.get("source") == "self" else "找数据平台管理员/开发人员提供"
                guide = f"：{var['guide']}" if var.get("guide") else ""
                print(f"      · {var['name']}（{who}）{guide}")
            print(f"      写入：python scripts/setup_env.py --skill {name} "
                  "--set KEY=VALUE [--set KEY=VALUE ...]")
        print("  逐技能引导见仓库 docs/install.md「环境变量配置」；"
              "配好后的 .env 在后续更新中自动保留，不会被覆盖。")


def cmd_status(args, prefix):
    sub = load_subscription(prefix)
    if not sub:
        print("尚未订阅。首次使用：python scripts/subscribe.py --harness claude")
        return
    print(f"市场：{sub['market']}")
    print(f"仓库：{sub['repo']}")
    print(f"commit：{sub['commit'][:10]}")
    print(f"上次同步：{sub['synced_at']}")
    print(f"分发目标：{', '.join(sub['harnesses'])}"
          + (f" + {sub['custom_dest']}" if sub.get("custom_dest") else ""))
    for s in sub["skills"]:
        print(f"  - {s['name']} v{s['version']}")


def cmd_uninstall(args, prefix):
    sub = load_subscription(prefix)
    if not sub:
        raise SystemExit("没有可卸载的订阅。")
    targets = harness_targets(sub.get("harnesses", []), dest=None)
    if sub.get("custom_dest"):
        targets.append(("custom", Path(sub["custom_dest"])))
    names = [s["name"] for s in sub["skills"]]
    for harness, target in targets:
        for name in names:
            dst = target / name
            if dst.exists():
                shutil.rmtree(dst)
                print(f"[ok] 已从 {target} 移除 {name}")
    (prefix / SUBSCRIPTION_FILE).unlink(missing_ok=True)
    print("订阅已注销。")


def cmd_register_task(args, prefix):
    script = Path(__file__).resolve()
    cmd_str = f'"{sys.executable}" "{script}" --sync --quiet'
    if sys.platform == "win32":
        run(["schtasks", "/Create", "/TN", TASK_NAME, "/TR", cmd_str,
             "/SC", "DAILY", "/ST", "09:30", "/F"], check=False)
        print(f"已注册每日 09:30 的计划任务 {TASK_NAME}（任务计划程序中可修改）。")
    else:
        line = f"30 9 * * * {cmd_str} # {TASK_NAME}"
        current = run(["crontab", "-l"], check=False).stdout or ""
        if TASK_NAME not in current:
            run(["sh", "-c", f"(crontab -l 2>/dev/null; echo '{line}') | crontab -"],
                check=False)
        print(f"已注册每日 09:30 的 crontab 任务 {TASK_NAME}。")


def cmd_unregister_task(args, prefix):
    if sys.platform == "win32":
        run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], check=False)
    else:
        current = run(["crontab", "-l"], check=False).stdout or ""
        kept = "\n".join(l for l in current.splitlines() if TASK_NAME not in l)
        run(["sh", "-c", f"echo '{kept}' | crontab -"], check=False)
    print(f"已移除计划任务 {TASK_NAME}。")


def main():
    if hasattr(sys.stdout, "reconfigure"):  # Windows 控制台中文输出兜底
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--harness", action="append", metavar="NAME",
                        choices=sorted(HARNESS_DIRS),
                        help="订阅到指定 harness（可多次）；可选：" + "/".join(sorted(HARNESS_DIRS)))
    parser.add_argument("--dest", metavar="DIR",
                        help="自定义分发目录（优先于 --harness，如项目级 .claude/skills）")
    parser.add_argument("--repo", metavar="PATH",
                        help="仓库本地路径（默认：脚本所在仓库）")
    parser.add_argument("--sync", action="store_true", help="拉取更新并重新分发（默认动作）")
    parser.add_argument("--status", action="store_true", help="查看订阅状态")
    parser.add_argument("--uninstall", action="store_true", help="移除已分发的技能并注销订阅")
    parser.add_argument("--register-task", action="store_true", help="注册每日自动更新计划任务")
    parser.add_argument("--unregister-task", action="store_true", help="移除自动更新计划任务")
    parser.add_argument("--quiet", action="store_true", help="静默模式（计划任务用）")
    parser.add_argument("--prefix", metavar="DIR", default=str(DEFAULT_PREFIX),
                        help=f"订阅状态目录（默认 {DEFAULT_PREFIX}）")
    args = parser.parse_args()

    prefix = Path(args.prefix).expanduser()
    if args.status:
        cmd_status(args, prefix)
    elif args.uninstall:
        cmd_uninstall(args, prefix)
    elif args.register_task:
        cmd_register_task(args, prefix)
    elif args.unregister_task:
        cmd_unregister_task(args, prefix)
    else:
        cmd_sync(args, prefix)


if __name__ == "__main__":
    main()
