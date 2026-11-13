#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Provider registry."""

from .config import load_config
from .errors import AdapterConfigError
from .jsonrpc_mcp import JsonRpcMcpAdapter
from .mock import MockAdapter
from .rest import RestAdapter

PROVIDERS = {
    "jsonrpc-mcp": JsonRpcMcpAdapter,
    "rest": RestAdapter,
    "mock": MockAdapter,
}


def create_adapter(config):
    factory = PROVIDERS.get(config.provider)
    if factory is None:
        raise AdapterConfigError(
            "未知 provider %r；可选：%s" % (config.provider, ", ".join(sorted(PROVIDERS))))
    return factory(config)


def adapter_from_env(path=None, env=None, skill_root=None):
    """Build an adapter from the configured provider (see ``config.load_config``)."""
    return create_adapter(load_config(path=path, env=env, skill_root=skill_root))
