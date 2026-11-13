#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
MySQL 连接层（roots.py / code_value.py 共用）。

配置来源（优先级）：环境变量 MYSQL_* → skill 根目录 .env。
缓存/码值表是团队共享资产，连接失败必须报错终止，禁止静默降级到本地 CSV
（那会读到过期数据，破坏"同义字段同名"的一致性）。

脱敏说明：本文件不内置任何真实主机、账号或库名，全部由 MYSQL_* 提供；
只有端口有通用默认值 3306。
"""
import os
import sys
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"

_DEFAULTS = {
    "MYSQL_PORT": "3306",
}


def _load_env_file():
    """轻量 .env 解析（KEY=VALUE，支持单/双引号包裹，# 注释行）。"""
    if not ENV_FILE.exists():
        return {}
    out = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        raw = v.strip()
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "'\"":
            v = raw[1:-1]  # 引号包裹的值原样保留（密码含 # 的唯一正确写法）
        elif "#" in raw:
            v = raw.split("#", 1)[0].strip()  # 未加引号时 # 才是行内注释
        else:
            v = raw
        out[k.strip()] = v
    return out


def get(key: str) -> str:
    env = os.environ.get(key)
    if env:
        return env
    return _load_env_file().get(key, _DEFAULTS.get(key, ""))


def connect():
    try:
        import pymysql
    except ImportError:
        sys.exit("[db] 缺少 pymysql：pip install -r requirements.txt")
    host, port, user = get("MYSQL_HOST"), int(get("MYSQL_PORT")), get("MYSQL_USER")
    pwd, db = get("MYSQL_PASS"), get("MYSQL_DB")
    if not (host and user and pwd and db):
        sys.exit("[db] MySQL 配置不完整：检查 .env 的 MYSQL_HOST/PORT/USER/PASS/DB")
    try:
        return pymysql.connect(host=host, port=port, user=user, password=pwd,
                               database=db, charset="utf8mb4",
                               connect_timeout=5, autocommit=True)
    except Exception as e:
        sys.exit(f"[db] MySQL 连接失败（{host}:{port}/{db}）：{e}\n"
                 f"     缓存/码值表是多用户共享资产，连接失败时禁止回退本地 CSV（会读到过期数据）。"
                 f"请检查网络与 .env 配置。")
