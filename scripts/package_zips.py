#!/usr/bin/env python3
"""把 marketplace.json 登记的技能打包成 WorkBuddy 可上传的 ZIP。

WorkBuddy 上传路径：专家·技能·连接器 → 技能 → 添加技能 → 上传技能。
ZIP 结构要求：压缩包第一层是技能目录，SKILL.md 在第一层子目录内，
即解压后为 <技能名>/SKILL.md。

用法：
  python scripts/package_zips.py                  # 输出到 ./dist
  python scripts/package_zips.py --out /tmp/zips  # 指定输出目录
"""

import argparse
import json
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EXCLUDE_DIRS = {".git", "__pycache__", ".pytest_cache", "output", ".venv",
                "venv", ".idea", ".vscode", "node_modules", ".env"}
EXCLUDE_SUFFIXES = {".pyc", ".log"}


def build_zip(skill_dir: Path, skill_name: str, out_path: Path) -> int:
    count = 0
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(skill_dir.rglob("*")):
            if path.is_dir():
                continue
            if any(part in EXCLUDE_DIRS for part in path.parts):
                continue
            if path.suffix in EXCLUDE_SUFFIXES or path.name == ".env":
                continue
            arcname = f"{skill_dir.name}/{path.relative_to(skill_dir).as_posix()}"
            zf.write(path, arcname=arcname)
            count += 1
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(REPO_ROOT / "dist"),
                        help="输出目录（默认 ./dist）")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    data = json.loads((REPO_ROOT / "marketplace.json").read_text(encoding="utf-8"))

    for entry in data["skills"]:
        skill_dir = (REPO_ROOT / entry["path"]).resolve()
        if not (skill_dir / "SKILL.md").exists():
            print(f"[skip] {entry['name']}：缺少 SKILL.md，不上架打包")
            continue
        version = entry.get("version", "0.0.0")
        out_path = out_dir / f"{entry['name']}-v{version}-workbuddy.zip"
        n = build_zip(skill_dir, entry["name"], out_path)
        print(f"[ok] {out_path.name}（{n} 个文件）")


if __name__ == "__main__":
    main()
