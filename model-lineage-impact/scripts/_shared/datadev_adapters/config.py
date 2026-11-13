#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Adapter configuration: JSON file + environment + ``.env`` lookup.

Resolution order for the config document:

1. explicit ``path`` argument / ``--config`` flag
2. ``DATADEV_ADAPTER_CONFIG`` environment variable (path, or an inline JSON)
3. ``DATADEV_ADAPTER_JSON`` environment variable (inline JSON)

Secrets are never stored in the repo.  Use ``token_env`` (default
``DATADEV_API_TOKEN``) and reference it from headers as ``${token}``.
"""

import json
import os
import re

from .errors import AdapterConfigError

ENV_FILE = ".env"
DEFAULT_TOKEN_ENV = "DATADEV_API_TOKEN"

DEFAULT_OPERATIONS = {
    "search_table": {
        "tool": "search_table",
        "arguments": {"name": "{name}", "db": "{db}"},
    },
    "get_table": {
        "tool": "get_table",
        "arguments": {"guid": "{guid}"},
    },
    "get_lineage": {
        "tool": "get_lineage",
        "arguments": {"guid": "{guid}", "direction": "{direction}"},
    },
    "get_task_detail": {
        "tool": "get_task_detail",
        "arguments": {"task_id": "{task_id}"},
    },
    "get_task_dependencies": {
        "tool": "get_task_dependencies",
        "arguments": {"task_id": "{task_id}", "scope": "{scope}", "step": "{step}"},
    },
    "get_task_sql": {
        "tool": "get_task_sql",
        "arguments": {"task_id": "{task_id}"},
    },
}

REQUIRED_OPERATIONS = tuple(DEFAULT_OPERATIONS)


def read_env_file(path):
    """Parse a ``.env`` file into a dict (no interpolation, no export magic)."""
    values = {}
    if not path or not os.path.exists(path):
        return values
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            values[key.strip()] = value
    return values


def _skill_root(skill_root=None):
    if skill_root:
        return os.path.abspath(skill_root)
    return os.getcwd()


def resolve_env(env=None, skill_root=None):
    """OS environment wins over the skill's ``.env`` file."""
    merged = read_env_file(os.path.join(_skill_root(skill_root), ENV_FILE))
    merged.update(os.environ if env is None else env)
    return merged


def _default_config_file(env):
    candidate = env.get("DATADEV_ADAPTER_CONFIG") or ""
    if candidate and not candidate.lstrip().startswith("{"):
        return candidate
    return None


def _load_document(env, path=None):
    if path:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle), os.path.abspath(path)
    inline = env.get("DATADEV_ADAPTER_JSON")
    if not inline and env.get("DATADEV_ADAPTER_CONFIG", "").lstrip().startswith("{"):
        inline = env["DATADEV_ADAPTER_CONFIG"]
    if inline:
        return json.loads(inline), "<env:DATADEV_ADAPTER_JSON>"
    raise AdapterConfigError(
        "找不到适配器配置。请设置 DATADEV_ADAPTER_CONFIG 指向一个 JSON 文件，"
        "或在 .env 里配置；仓库 adapter-configs/ 下有 mcp / rest / mock 三个示例。"
    )


def _expand(text, token, env):
    if not isinstance(text, str):
        return text

    def repl(match):
        key = match.group(1)
        if key == "token":
            return token or ""
        return str(env.get(key, ""))

    return re.sub(r"\$\{([A-Za-z_]\w*)\}", repl, text)


def _expand_dict(data, token, env):
    if isinstance(data, dict):
        return {k: _expand_dict(v, token, env) for k, v in data.items()}
    if isinstance(data, list):
        return [_expand_dict(v, token, env) for v in data]
    return _expand(data, token, env)


def merge_operations(operations):
    merged = {name: dict(spec) for name, spec in DEFAULT_OPERATIONS.items()}
    for name, spec in (operations or {}).items():
        base = dict(merged.get(name, {}))
        if isinstance(spec, dict):
            base.update(spec)
        else:  # allow {"search_table": "my.tool"} shorthand
            base["tool"] = spec
        merged[name] = base
    return merged


class AdapterConfig(object):
    """Normalised adapter config.  Unknown keys are kept in ``raw``."""

    def __init__(self, provider="mock", endpoint=None, endpoints=None, token=None,
                 headers=None, timeout=60, retries=3, verify_ssl=True,
                 operations=None, fixtures=None, raw=None, source=None):
        self.provider = provider
        self.endpoint = endpoint
        self.endpoints = endpoints or {}
        self.token = token
        self.headers = headers or {}
        self.timeout = timeout
        self.retries = retries
        self.verify_ssl = verify_ssl
        self.operations = merge_operations(operations)
        self.fixtures = fixtures
        self.raw = raw or {}
        self.source = source
        self.custom_operations = sorted((raw or {}).get("operations") or {})

    def operation(self, name):
        spec = self.operations.get(name)
        if not spec:
            raise AdapterConfigError(
                "适配器配置缺少 operation %r；可用 operation: %s"
                % (name, ", ".join(sorted(self.operations)))
            )
        return spec

    def endpoint_for(self, spec):
        service = spec.get("service") if isinstance(spec, dict) else None
        if service:
            endpoint = self.endpoints.get(service)
            if not endpoint:
                raise AdapterConfigError(
                    "operation 引用了 service=%r，但配置里没有 endpoints.%s" % (service, service))
            return endpoint
        if self.endpoint:
            return self.endpoint
        raise AdapterConfigError("适配器配置缺少 endpoint")

    def fixture_path(self, name):
        if not self.fixtures:
            raise AdapterConfigError("mock 适配器需要在配置里提供 fixtures 目录")
        if not os.path.isabs(self.fixtures):
            base = os.path.dirname(self.source) if self.source and self.source != "<env:DATADEV_ADAPTER_JSON>" else os.getcwd()
            return os.path.join(base, self.fixtures)
        return self.fixtures

    def describe(self):
        return {
            "provider": self.provider,
            "source": self.source,
            "endpoint": self.endpoint,
            "endpoints": sorted(self.endpoints),
            "fixtures": self.fixtures,
            "operations": sorted(self.operations),
            "token_configured": bool(self.token),
        }


def load_config(path=None, env=None, skill_root=None):
    env = resolve_env(env=env, skill_root=skill_root)
    doc, source = _load_document(env, path=path or _default_config_file(env))
    if not isinstance(doc, dict):
        raise AdapterConfigError("适配器配置必须是 JSON 对象，当前为 %s" % type(doc).__name__)

    provider = str(doc.get("provider") or "jsonrpc-mcp").strip().lower()
    aliases = {
        "jsonrpc": "jsonrpc-mcp", "mcp": "jsonrpc-mcp", "json-rpc": "jsonrpc-mcp",
        "http": "rest", "api": "rest", "fixture": "mock", "fixtures": "mock",
    }
    provider = aliases.get(provider, provider)

    token_env = doc.get("token_env") or DEFAULT_TOKEN_ENV
    token = doc.get("token") or env.get(token_env) or env.get("DATADEV_API_TOKEN") or ""
    headers = _expand_dict(doc.get("headers") or {}, token, env)
    if token and not headers and provider != "mock":
        headers = {"Authorization": "Bearer %s" % token}

    operations = doc.get("operations") or {}
    if not isinstance(operations, dict):
        raise AdapterConfigError("operations 必须是对象：{operation: spec}")

    fixtures = doc.get("fixtures")
    if fixtures and source not in (None, "<env:DATADEV_ADAPTER_JSON>"):
        if not os.path.isabs(fixtures):
            fixtures = os.path.join(os.path.dirname(os.path.abspath(source)), fixtures)

    return AdapterConfig(
        provider=provider,
        endpoint=(_expand(doc.get("endpoint"), token, env) if doc.get("endpoint") else None),
        endpoints=_expand_dict(doc.get("endpoints") or {}, token, env),
        token=token,
        headers=headers,
        timeout=int(doc.get("timeout") or 60),
        retries=int(doc.get("retries", 3)),
        verify_ssl=bool(doc.get("verify_ssl", True)),
        operations=operations,
        fixtures=fixtures,
        raw=doc,
        source=source,
    )
