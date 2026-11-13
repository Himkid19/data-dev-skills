#!/usr/bin/env python3
"""把收集到的环境变量写进某个技能的 .env。

安装 data-dev-skills 后，按每个技能的引导收集到凭据值，用本脚本一次性写入
技能目录的 .env（从 .env.example 生成，只覆盖给定 KEY，其余行保留）。
值不回显到终端，避免凭据落进对话/日志。--list 先看某技能需要哪些变量、当前缺哪些。

用法：
  # 先看需要哪些变量、当前状态（不显示值）
  python scripts/setup_env.py --skill master-data-modeling --list

  # 写入（可多次 --set；目标目录为已订阅分发的技能目录）
  python scripts/setup_env.py --skill master-data-source-mapping \
      --set DATADEV_API_TOKEN=<你的令牌>
  python scripts/setup_env.py --skill master-data-modeling \
      --set MD_OPERATOR=zhangsan --set MYSQL_HOST=10.x.x.x \
      --set MYSQL_USER=datadev --set MYSQL_PASS=<管理员给的密码>

目标目录解析（按优先级）：
  --dir <技能目录>        直接指定技能目录（最高优先级，测试/项目级用）
  --harness claude …      指定 harness，写其用户级技能目录
  --prefix <状态目录>     读 subscription.json 里的 harnesses / custom_dest（默认与订阅一致）
  都没有时回退到仓库内的技能目录（开发调试用）

无第三方依赖，Python 3.8+。
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import subscribe  # noqa: E402  复用 HARNESS_DIRS / load_subscription / load_marketplace

REPO_ROOT = Path(__file__).resolve().parent.parent
PLACEHOLDER_MARKERS = ("REPLACE_ME", "你的", "todo", "TODO")


def load_skill_env(repo_root, skill_name):
    """返回该技能在 marketplace.json 里的 env 元数据（可能为空 dict）。"""
    market = repo_root / "marketplace.json"
    if not market.exists():
        return {}
    data = json.loads(market.read_text(encoding="utf-8"))
    for entry in data.get("skills", []):
        if entry.get("name") == skill_name:
            return entry.get("env") or {}
    return {}


def harness_skill_dir(harness, skill_name):
    if harness not in subscribe.HARNESS_DIRS:
        raise SystemExit(
            f"未知的 harness：{harness}。可选：{', '.join(sorted(subscribe.HARNESS_DIRS))}")
    return Path.home() / subscribe.HARNESS_DIRS[harness] / skill_name


def resolve_targets(args, repo_root):
    """返回要写入的技能目录列表（去重、保序）。"""
    if args.dir:
        return [Path(args.dir).expanduser()]
    sub = subscribe.load_subscription(Path(args.prefix).expanduser())
    harnesses = args.harness or (sub or {}).get("harnesses", [])
    custom = (sub or {}).get("custom_dest")
    targets = [harness_skill_dir(h, args.skill) for h in harnesses]
    if custom:
        targets.append(Path(custom) / args.skill)
    if not targets:
        # 未订阅（或状态目录不可见）时回退到仓库内技能目录，便于开发调试
        fallback = repo_root / args.skill
        print(f"[hint] 未找到订阅面，写入仓库内目录：{fallback}", file=sys.stderr)
        targets.append(fallback)
    unique, seen = [], set()
    for t in targets:
        key = str(t)
        if key not in seen:
            seen.add(key)
            unique.append(t)
    return unique


def read_env_lines(skill_dir):
    """读技能目录现有 .env；没有就从 .env.example 取模板；都没有则空。"""
    env_path = skill_dir / ".env"
    if env_path.exists():
        return env_path.read_text(encoding="utf-8").splitlines()
    example = skill_dir / ".env.example"
    if example.exists():
        return example.read_text(encoding="utf-8").splitlines()
    return []


def _index_of(lines):
    index = {}
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in line:
            index[line.split("=", 1)[0].strip()] = i
    return index


def write_env(skill_dir, assignments):
    if not skill_dir.exists():
        raise SystemExit(f"技能目录不存在：{skill_dir}（先订阅，或用 --dir 指定）")
    lines = read_env_lines(skill_dir)
    index = _index_of(lines)
    for key, val in assignments:
        if key in index:
            lines[index[key]] = f"{key}={val}"
        else:
            index[key] = len(lines)
            lines.append(f"{key}={val}")
    (skill_dir / ".env").write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")


def var_status(skill_dir, name):
    """返回该变量当前状态：缺失 / 待填(占位符) / 已配置（从不回显真实值）。"""
    value = None
    for line in read_env_lines(skill_dir):
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            if key.strip() == name:
                value = val.strip().strip("'\"")
    if value is None or value == "":
        return "缺失"
    if any(m in value for m in PLACEHOLDER_MARKERS):
        return "待填(占位符)"
    return "已配置"


def cmd_list(args, targets, env_meta):
    vars_ = env_meta.get("vars") or []
    if not vars_:
        print(f"{args.skill}：marketplace.json 未登记环境变量（或该技能无需配置）")
    for target in targets:
        print(f"\n{args.skill} · {target}")
        if not target.exists():
            print("  （目录不存在，尚未订阅分发到这里）")
            continue
        for v in vars_:
            flag = "必填" if v.get("required", True) else "可选"
            who = "自己填" if v.get("source") == "self" else "找数据平台管理员"
            guide = f"  # {v['guide']}" if v.get("guide") else ""
            print(f"  [{flag}|{who}] {v['name']}：{var_status(target, v['name'])}{guide}")
    if vars_:
        required = [v["name"] for v in vars_ if v.get("required", True)]
        print(f"\n必填项：{', '.join(required)}")
        print("写入示例：python scripts/setup_env.py --skill "
              f"{args.skill} --set KEY=VALUE [--set KEY=VALUE ...]")


def cmd_write(args, targets):
    assignments = []
    for pair in args.set:
        if "=" not in pair:
            raise SystemExit(f"--set 需要 KEY=VALUE 形式：{pair}")
        key, _, val = pair.partition("=")
        assignments.append((key.strip(), val))
    for target in targets:
        write_env(target, assignments)
        keys = "、".join(k for k, _ in assignments)
        print(f"[ok] 已写入 {target / '.env'}：{keys}（值不回显）")
    print("提示：验证时直接运行技能命令；配置好的 .env 在后续同步更新中自动保留。")


def main():
    if hasattr(sys.stdout, "reconfigure"):  # Windows 控制台中文输出兜底
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--skill", required=True, help="技能名，如 master-data-modeling")
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                        help="写入一个环境变量（可多次）")
    parser.add_argument("--list", action="store_true", help="列出该技能的变量与当前状态")
    parser.add_argument("--dir", metavar="DIR", help="直接指定技能目录（优先）")
    parser.add_argument("--harness", action="append", choices=sorted(subscribe.HARNESS_DIRS),
                        help="写入指定 harness 的技能目录（可多次）")
    parser.add_argument("--prefix", metavar="DIR", default=str(subscribe.DEFAULT_PREFIX),
                        help=f"订阅状态目录（默认 {subscribe.DEFAULT_PREFIX}）")
    parser.add_argument("--repo", metavar="PATH", help="仓库本地路径（默认脚本所在仓库）")
    args = parser.parse_args()

    repo_root = Path(args.repo).expanduser().resolve() if args.repo else REPO_ROOT
    env_meta = load_skill_env(repo_root, args.skill)
    targets = resolve_targets(args, repo_root)

    if args.list:
        cmd_list(args, targets, env_meta)
    elif args.set:
        cmd_write(args, targets)
    else:
        raise SystemExit("请指定要写入的变量（--set KEY=VALUE）或用 --list 查看需要哪些变量。")


if __name__ == "__main__":
    main()
