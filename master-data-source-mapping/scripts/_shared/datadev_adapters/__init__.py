#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""data-dev metadata adapters.

A tiny, dependency-free layer that decouples the skills from one specific
metadata service.  See ``base.MetadataAdapter`` for the canonical API and
``config`` for the JSON config format.
"""

from .base import (MetadataAdapter, normalize_candidate, normalize_lineage_item,
                   normalize_table, normalize_task_detail)
from .config import AdapterConfig, load_config
from .errors import AdapterConfigError, AdapterError, AdapterRequestError
from .registry import PROVIDERS, adapter_from_env, create_adapter

__all__ = [
    "AdapterConfig",
    "AdapterConfigError",
    "AdapterError",
    "AdapterRequestError",
    "MetadataAdapter",
    "PROVIDERS",
    "adapter_from_env",
    "create_adapter",
    "load_config",
    "normalize_candidate",
    "normalize_lineage_item",
    "normalize_table",
    "normalize_task_detail",
]

__version__ = "0.1.0"
