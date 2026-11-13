#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Small CLI for adapter diagnostics.

    python -m datadev_adapters config [--config FILE]
    python -m datadev_adapters doctor [--table NAME] [--config FILE]
    python -m datadev_adapters tools  [--config FILE]

``doctor`` performs a real call only when ``--table`` is given, so it is safe
to run before the endpoint is reachable (it then just prints the resolved
config, with secrets masked).
"""

import argparse
import json
import sys

from .config import load_config
from .errors import AdapterError
from .registry import PROVIDERS, create_adapter


def _print(data):
    print(json.dumps(data, ensure_ascii=False, indent=2))


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        prog="python -m datadev_adapters",
        description="Inspect and smoke-test the data-dev metadata adapter.")
    parser.add_argument("--config", default=None, help="adapter config JSON path")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("config", help="print the resolved config")
    doctor = sub.add_parser("doctor", help="validate config and optionally call search_table")
    doctor.add_argument("--table", default=None, help="table name to look up")
    sub.add_parser("tools", help="list MCP tools (jsonrpc-mcp provider only)")
    args = parser.parse_args(argv)

    try:
        config = load_config(path=args.config)
        if args.command in (None, "config"):
            info = config.describe()
            info["providers"] = sorted(PROVIDERS)
            _print(info)
            return 0
        adapter = create_adapter(config)
        if args.command == "doctor":
            _print(adapter.ping(table=args.table))
            return 0
        if args.command == "tools":
            if not hasattr(adapter, "list_tools"):
                print("tools 只支持 jsonrpc-mcp provider", file=sys.stderr)
                return 2
            _print(adapter.list_tools())
            return 0
    except AdapterError as exc:
        print("[adapter] %s" % exc, file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
