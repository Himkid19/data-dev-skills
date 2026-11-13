#!/usr/bin/env python3
"""Vendor the shared adapter package into the skills that use it.

Skills are distributed as standalone directories, so they cannot import
``packages/datadev_adapters`` from the repo root.  The package is therefore
copied to ``<skill>/scripts/_shared/datadev_adapters/``; this script keeps the
copies byte-identical.

    python scripts/sync_adapters.py           # copy / refresh
    python scripts/sync_adapters.py --check   # fail if a copy drifted
"""

import argparse
import filecmp
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SOURCE = REPO / "packages" / "datadev_adapters"
TARGETS = ("master-data-source-mapping", "model-lineage-impact")


def target_dir(skill):
    return REPO / skill / "scripts" / "_shared" / "datadev_adapters"


def source_files():
    return sorted(p for p in SOURCE.glob("*.py"))


def check():
    problems = []
    for skill in TARGETS:
        dest = target_dir(skill)
        if not dest.is_dir():
            problems.append("缺少 %s" % dest.relative_to(REPO))
            continue
        for src in source_files():
            dst = dest / src.name
            if not dst.exists():
                problems.append("缺少 %s" % dst.relative_to(REPO))
            elif not filecmp.cmp(src, dst, shallow=False):
                problems.append("内容漂移 %s" % dst.relative_to(REPO))
        for extra in dest.glob("*.py"):
            if extra.name not in {p.name for p in source_files()}:
                problems.append("多余文件 %s" % extra.relative_to(REPO))
    return problems


def write():
    for skill in TARGETS:
        dest = target_dir(skill)
        dest.mkdir(parents=True, exist_ok=True)
        for stale in dest.glob("*.py"):
            if stale.name not in {p.name for p in source_files()}:
                stale.unlink()
        for src in source_files():
            shutil.copy2(src, dest / src.name)
        for cache in dest.glob("__pycache__"):
            shutil.rmtree(cache, ignore_errors=True)
        print("[synced] %s" % dest.relative_to(REPO))


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="only verify, do not write")
    args = parser.parse_args()
    if args.check:
        problems = check()
        for problem in problems:
            print("[drift] %s" % problem)
        if problems:
            print("适配器副本与 packages/datadev_adapters 不一致，运行 "
                  "python scripts/sync_adapters.py 修复")
            return 1
        print("[ok] 适配器副本一致：%d 个技能，%d 个模块"
              % (len(TARGETS), len(source_files())))
        return 0
    write()
    return 0


if __name__ == "__main__":
    sys.exit(main())
