#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Declarative request/response mapping helpers.

The adapter layer never hard-codes one vendor's JSON shape.  A config file
describes, per logical operation:

* how to build the request (``arguments`` / ``query`` / ``body``), and
* how to read the response (``response.path`` + ``response.item`` / ``pick``).

Templates look like ``"{name}"`` / ``"/tables/{guid}"``.  When a template is
exactly one placeholder the underlying value keeps its type (int, bool, ...).
"""

import re

_PLACEHOLDER = re.compile(r"\{([A-Za-z_][\w]*)\}")


def get_path(obj, path):
    """Read a dotted path from nested dicts/lists.  ``""``/``"."``/``"$"`` = self."""
    if not path or path in (".", "$"):
        return obj
    cur = obj
    for part in str(path).lstrip("$").strip(".").split("."):
        if part == "":
            continue
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, (list, tuple)):
            if part.isdigit() and int(part) < len(cur):
                cur = cur[int(part)]
            else:
                return None
        else:
            return None
        if cur is None:
            return None
    return cur


def render(value, params):
    """Recursively substitute ``{placeholder}`` in strings/keys/values."""
    if isinstance(value, str):
        whole = _PLACEHOLDER.fullmatch(value)
        if whole:
            return params.get(whole.group(1), value)
        return _PLACEHOLDER.sub(
            lambda m: "" if params.get(m.group(1)) is None else str(params[m.group(1)]),
            value,
        )
    if isinstance(value, dict):
        return {k: render(v, params) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, params) for v in value]
    return value


def as_list(value, keys=()):
    """Coerce a payload into a list, unwrapping the usual envelope keys."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in keys:
            got = get_path(value, key)
            if isinstance(got, list):
                return got
        for key in ("records", "items", "list", "rows", "data", "result"):
            got = value.get(key)
            if isinstance(got, list):
                return got
        return [value]
    return [value]


def extract(payload, response, params=None):
    """Apply a ``response`` spec to a raw payload.

    Supported spec keys::

        {"path": "data.records"}                  # pick a subtree
        {"path": "data", "pick": {"UPSTREAM": "upstreamTaskIds"}}
        {"path": "data", "item": {"id": "guid"}}  # map each list element
        {"path": "data", "item": "."}             # keep scalar elements
    """
    params = params or {}
    if not response:
        return payload
    data = payload
    if response.get("path"):
        data = get_path(data, render(response["path"], params))
    pick = response.get("pick")
    if pick:
        key = pick.get(params.get("scope")) or pick.get(params.get("direction"))
        if key:
            data = get_path(data, key)
    if "item" in response:
        item = response["item"]
        rows = as_list(data)
        if isinstance(item, dict):
            return [{k: get_path(row, v) for k, v in item.items()} for row in rows]
        if item in (".", "$", ""):
            return rows
        return [get_path(row, item) for row in rows]
    return data


def pick_first(mapping, keys, default=None):
    """Return the first present, non-empty value for ``keys``."""
    for key in keys:
        if isinstance(mapping, dict) and mapping.get(key) not in (None, ""):
            return mapping[key]
    return default
