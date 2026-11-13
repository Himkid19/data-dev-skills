#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Fixture provider: replays recorded payloads, no network.

Fixture file name = operation name + ``.json``.  Either the file *is* the
payload, or it uses::

    {"default": <payload>, "by": {"<key>": <payload>}}

where ``<key>`` is the first identifying argument (table name, guid or task
id).  A key may also be the JSON encoding of all params.
"""

import json
import os

from .base import MetadataAdapter
from .errors import AdapterRequestError

KEY_ORDER = ("name", "guid", "task_id", "taskId")


def _key(params):
    for name in KEY_ORDER:
        value = params.get(name)
        if value not in (None, ""):
            return str(value)
    return ""


class MockAdapter(MetadataAdapter):
    provider_name = "mock"

    def _invoke(self, operation, spec, params):
        path = os.path.join(self.config.fixture_path(operation), operation + ".json")
        if not os.path.exists(path):
            raise AdapterRequestError(
                "缺少 fixture：%s（mock 适配器需要每个 operation 一个 JSON 文件）" % path)
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict) and ("default" in data or "by" in data):
            by = data.get("by") or {}
            encoded = json.dumps(params, sort_keys=True, ensure_ascii=False)
            if encoded in by:
                return by[encoded]
            key = _key(params)
            if key in by:
                return by[key]
            return data.get("default")
        return data
