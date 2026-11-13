#!/usr/bin/env python3
"""marketplace.json 与技能目录的一致性检查 / 自动修复。

解决的问题：技能目录合入 master 后忘记登记 marketplace.json（UNLISTED）、
登记的目录被删（DEAD）、技能内容改了但版本没 bump（DRIFT）。

规则（error 会让 --check 非零退出，用于 CI 挡漂移）：
  UNLISTED        目录含 SKILL.md 但未登记        --fix 自动入册（0.1.0 起步）
  DEAD            登记的目录不存在/无 SKILL.md     --fix 自动移除条目
  NOBASELINE      条目缺 baseline 基线            --fix 以当前状态建立基线
  BASELINE_STALE  版本已 bump 但基线未刷新         --fix 自动刷新
  DRIFT           技能内容有新提交但版本未变        需人工 bump 版本，--fix 拒绝代劳

baseline 字段（{"version", "commit"}）记录"哪个版本在哪个 commit 上架"，
是 DRIFT 判定的依据；订阅端 subscribe.py 不依赖该字段。

用法：
  python scripts/lint_marketplace.py            # 只读报告
  python scripts/lint_marketplace.py --check    # 校验模式：有 error 即退出码 1（CI 用）
  python scripts/lint_marketplace.py --fix      # 自动修复可修复项并回写 marketplace.json
"""

import argparse
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MARKETPLACE_FILE = "marketplace.json"
INITIAL_VERSION = "0.1.0"
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def run_git(args, cwd=None):
    result = subprocess.run(["git", *args], cwd=str(cwd or REPO_ROOT),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace")
    return result.returncode, (result.stdout or "").strip()


def head_commit(root=REPO_ROOT):
    code, out = run_git(["rev-parse", "HEAD"], cwd=root)
    return out if code == 0 else None


def skill_dirs(root):
    """仓库一级目录中包含 SKILL.md 的目录：[(name, path)]，按名称排序。"""
    found = []
    for child in sorted(root.iterdir()):
        if child.is_dir() and not child.name.startswith(".") and (child / "SKILL.md").exists():
            found.append((child.name, child))
    return found


def skill_summary(skill_dir):
    """从 SKILL.md frontmatter 提取 description 作为入册摘要（截断到 80 字）。"""
    desc = ""
    try:
        text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        match = re.search(r"^description:\s*(.+)$", text, re.MULTILINE)
        if match:
            desc = match.group(1).strip().strip('"')
    except OSError:
        pass
    return desc[:80] + ("…" if len(desc) > 80 else "")


def load(root):
    return json.loads((root / MARKETPLACE_FILE).read_text(encoding="utf-8"))


def save(root, data):
    data["updated"] = date.today().isoformat()
    (root / MARKETPLACE_FILE).write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def last_change_commit(skill_path, root=REPO_ROOT):
    """技能目录最后一次内容改动的 commit；无 git 历史时返回 None。"""
    code, out = run_git(["log", "-1", "--format=%H", "--", str(skill_path)], cwd=root)
    return out if code == 0 and out else None


def analyze(root=REPO_ROOT):
    """返回 (issues, data, dirs)。issue: {code, level, skill, msg, fixable}。"""
    data = load(root)
    entries = data.get("skills", [])
    by_name = {e["name"]: e for e in entries}
    dirs = dict(skill_dirs(root))
    issues = []

    for name in dirs:
        if name not in by_name:
            issues.append({"code": "UNLISTED", "level": "error", "skill": name,
                           "msg": f"{name}/ 含 SKILL.md 但未登记到 {MARKETPLACE_FILE}",
                           "fixable": True})

    for entry in entries:
        name = entry["name"]
        path = (root / entry["path"]).resolve()
        if name not in dirs:
            issues.append({"code": "DEAD", "level": "error", "skill": name,
                           "msg": f"已登记的 {entry['path']} 不存在或没有 SKILL.md",
                           "fixable": True})
            continue
        if not SEMVER.match(entry.get("version", "")):
            issues.append({"code": "BADVERSION", "level": "error", "skill": name,
                           "msg": f"version 必须是 x.y.z，当前为 {entry.get('version')!r}",
                           "fixable": False})
        baseline = entry.get("baseline") or {}
        if not baseline.get("commit"):
            issues.append({"code": "NOBASELINE", "level": "error", "skill": name,
                           "msg": "缺少 baseline 基线，无法检测内容漂移", "fixable": True})
            continue
        if entry.get("version") != baseline.get("version"):
            issues.append({"code": "BASELINE_STALE", "level": "error", "skill": name,
                           "msg": f"版本 {baseline.get('version')} -> {entry.get('version')} "
                                  "已 bump，基线未刷新", "fixable": True})
            continue
        last = last_change_commit(path, root)
        if last and last != baseline["commit"] and head_commit(root) and \
                not _commit_contains(last, baseline["commit"], root):
            issues.append({"code": "DRIFT", "level": "error", "skill": name,
                           "msg": f"{name}/ 在基线 {baseline['commit'][:8]} 之后有内容改动，"
                                  "但版本未变——请先 bump marketplace.json 里的 version "
                                  "（并记 CHANGELOG），再运行 --fix 刷新基线",
                           "fixable": False})
    return issues, data, dirs


def _commit_contains(ancestor, descendant, root=REPO_ROOT):
    """descendant 是否包含 ancestor（同一提交或其后代）：基线提交恰好就是技能
    最后一次改动时不算漂移（改技能和入册在同一提交）。"""
    if ancestor == descendant:
        return True
    code, _ = run_git(["merge-base", "--is-ancestor", ancestor, descendant], cwd=root)
    return code == 0


def apply_fixes(issues, data, dirs, root=REPO_ROOT):
    """修复 UNLISTED/DEAD/NOBASELINE/BASELINE_STALE，返回 (修复列表, 拒绝列表)。"""
    head = head_commit(root)
    fixed, refused = [], []
    skills = data["skills"]
    for issue in issues:
        code, name = issue["code"], issue["skill"]
        if code == "UNLISTED":
            skills.append({
                "name": name,
                "version": INITIAL_VERSION,
                "path": f"./{name}",
                "summary": skill_summary(dirs[name]),
                "baseline": {"version": INITIAL_VERSION, "commit": head},
            })
            fixed.append(f"入册 {name} v{INITIAL_VERSION}")
        elif code == "DEAD":
            skills[:] = [e for e in skills if e["name"] != name]
            fixed.append(f"移除失效条目 {name}")
        elif code in ("NOBASELINE", "BASELINE_STALE"):
            for e in skills:
                if e["name"] == name:
                    e["baseline"] = {"version": e["version"], "commit": head}
            fixed.append(f"{name} 基线刷新到 {e['version']} @ {head[:8] if head else '?'}"
                         if code == "BASELINE_STALE" else f"{name} 建立基线")
        elif code == "DRIFT":
            refused.append(issue["msg"])
    return fixed, refused


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true",
                        help="校验模式：存在 error 时退出码 1（CI / 提交前用）")
    parser.add_argument("--fix", action="store_true",
                        help="自动修复 UNLISTED/DEAD/基线问题并回写 marketplace.json")
    args = parser.parse_args()

    issues, data, dirs = analyze()
    errors = [i for i in issues if i["level"] == "error"]

    if not issues:
        print(f"[ok] 市场一致性通过：{len(dirs)} 个技能目录，{len(data['skills'])} 条登记")
        return 0

    for issue in issues:
        print(f"[{issue['code']}] {issue['skill']}：{issue['msg']}")

    if args.fix:
        fixed, refused = apply_fixes(issues, data, dirs)
        for line in fixed:
            print(f"[fixed] {line}")
        for line in refused:
            print(f"[refused] {line}")
        if fixed:
            save(REPO_ROOT, data)
            print(f"已回写 {MARKETPLACE_FILE}，重新校验：")
            remaining, _, _ = analyze()
            for issue in remaining:
                print(f"  [{issue['code']}] {issue['skill']}：{issue['msg']}")
            return 1 if remaining else 0

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
