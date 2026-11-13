#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Vendor-neutral metadata interface used by the data-dev skills.

Six logical operations cover the whole workflow:

======================  =====================================================
operation               canonical result
======================  =====================================================
search_table(name)      ``[{"guid", "name", "qualifiedName", "db"}]``
get_table(guid)         ``{"guid", "name", "db", "comment", "columns", "tasks"}``
get_lineage(guid)       ``[{"guid", "qualifiedName", "table"}]``
get_task_detail(id)     ``{"taskId", "taskName", "taskDesc"}``
get_task_dependencies   ``["task-id", ...]``
get_task_sql(id)        ``["insert ...", ...]``
======================  =====================================================

Providers only implement raw transport (``_invoke``).  Response unfolding and
canonicalisation happen here, so every provider behaves identically for the
skills and for tests.
"""

from . import mapping
from .config import REQUIRED_OPERATIONS


def _first(mapping_obj, keys, default=None):
    return mapping.pick_first(mapping_obj, keys, default)


def _unwrap(value, keys):
    """Descend through the common ``{"data": ...}`` envelopes."""
    seen = 0
    while isinstance(value, dict) and seen < 3:
        seen += 1
        for key in keys:
            if key in value and value[key] is not None:
                value = value[key]
                break
        else:
            break
    return value


def normalize_candidate(row):
    if not isinstance(row, dict):
        return {"guid": None, "name": str(row), "qualifiedName": None, "db": None}
    return {
        "guid": _first(row, ("guid", "id", "tableGuid", "table_guid")),
        "name": _first(row, ("name", "tableName", "table_name", "assetName")),
        "qualifiedName": _first(row, ("qualifiedName", "qualified_name", "fullName")),
        "db": _first(row, ("db", "dbName", "db_name", "database")) or "",
    }


def normalize_column(row):
    if isinstance(row, str):
        return {"name": row.lower(), "comment": "", "codedsetCode": None, "codedsetChnName": None}
    return {
        "name": (_first(row, ("name", "columnName", "column_name", "field")) or "").lower(),
        "comment": _first(row, ("comment", "description", "desc", "remark")) or "",
        "codedsetCode": _first(row, ("codedsetCode", "codedset_code", "codeSet")),
        "codedsetChnName": _first(row, ("codedsetChnName", "codedset_chn_name", "codeSetName")),
    }


def normalize_task(row):
    if isinstance(row, (str, int)):
        return {"taskId": str(row), "taskName": ""}
    return {
        "taskId": str(_first(row, ("taskId", "task_id", "id", "schedulingId")) or ""),
        "taskName": _first(row, ("taskName", "task_name", "name")) or "",
    }


def normalize_table(payload):
    data = _unwrap(payload, ("data", "table", "result"))
    if not isinstance(data, dict):
        return {"guid": None, "name": "", "db": "", "qualifiedName": "", "comment": "",
                "columns": [], "tasks": []}
    tech = data.get("technicalMetadata") or data.get("technical_metadata") or {}
    if not isinstance(tech, dict):
        tech = {}
    raw_columns = data.get("columns") or tech.get("columns") or []
    columns = []
    if isinstance(raw_columns, dict):
        columns = [{"name": str(k).lower(), "comment": v or "", "codedsetCode": None,
                    "codedsetChnName": None} for k, v in raw_columns.items()]
    else:
        columns = [normalize_column(c) for c in raw_columns if c is not None]
    tasks = data.get("tasks") or tech.get("horaeTasks") or tech.get("tasks") or []
    return {
        "guid": _first(data, ("guid", "id", "tableGuid")),
        "name": _first(data, ("name", "tableName", "table_name")) or "",
        "db": _first(data, ("db", "dbName", "db_name")) or _first(tech, ("dbName", "db")) or "",
        "qualifiedName": _first(data, ("qualifiedName", "qualified_name", "fullName")) or "",
        "comment": _first(data, ("comment", "description", "desc")) or "",
        "columns": [c for c in columns if c.get("name")],
        "tasks": [t for t in (normalize_task(t) for t in tasks if t is not None) if t.get("taskId")],
    }


def normalize_lineage_item(row):
    if isinstance(row, str):
        return {"guid": None, "qualifiedName": row, "table": row.split(".")[-1].lower()}
    item = {
        "guid": _first(row, ("guid", "id", "tableGuid")),
        "qualifiedName": _first(row, ("qualifiedName", "qualified_name", "fullName")) or "",
    }
    item["table"] = (item["qualifiedName"] or item["guid"] or "").split(".")[-1].lower()
    return item


def normalize_task_detail(payload):
    data = _unwrap(payload, ("data", "task", "result"))
    if isinstance(data, list) and data:
        data = data[0]
    if not isinstance(data, dict):
        return {"taskId": "", "taskName": "", "taskDesc": ""}
    return {
        "taskId": str(_first(data, ("taskId", "task_id", "id", "schedulingId")) or ""),
        "taskName": _first(data, ("taskName", "task_name", "name")) or "",
        "taskDesc": _first(data, ("taskDesc", "task_desc", "description", "desc")) or "",
    }


class MetadataAdapter(object):
    """Base class: normalises requests/results around :meth:`_invoke`."""

    provider_name = "base"

    def __init__(self, config):
        self.config = config
        self.calls = 0
        self.last_endpoint = None

    # ---------------------------------------------------------- transport hook
    def _invoke(self, operation, spec, params):
        raise NotImplementedError

    # ----------------------------------------------------------------- helpers
    def _result(self, operation, **params):
        spec = self.config.operation(operation)
        self.calls += 1
        payload = self._invoke(operation, spec, params)
        return mapping.extract(payload, spec.get("response"), params), payload

    # -------------------------------------------------------------- operations
    def search_table(self, name, db=""):
        data, payload = self._result("search_table", name=name, db=db)
        rows = mapping.as_list(data, ("records", "tables", "data", "items"))
        if not rows and isinstance(payload, dict) and data is payload:
            rows = mapping.as_list(_unwrap(payload, ("data", "result")), ("records", "tables", "items"))
        return [normalize_candidate(row) for row in rows if row is not None]

    def get_table(self, guid):
        data, _ = self._result("get_table", guid=guid)
        return normalize_table(data)

    def get_lineage(self, guid, direction="upstream"):
        data, payload = self._result("get_lineage", guid=guid, direction=direction)
        data = _unwrap(data, ("data", "result"))
        if isinstance(data, dict):
            keys = (("upstreamTables", "upstream", "upstream_tables", "parents", "inputs")
                    if direction == "upstream" else
                    ("downstreamTables", "downstream", "downstream_tables", "children", "outputs"))
            data = _first(data, keys)
        rows = mapping.as_list(data)
        return [normalize_lineage_item(row) for row in rows if row is not None]

    def get_task_detail(self, task_id):
        data, _ = self._result("get_task_detail", task_id=str(task_id))
        return normalize_task_detail(data)

    def get_task_dependencies(self, task_id, scope="DOWNSTREAM", step=1):
        data, payload = self._result(
            "get_task_dependencies", task_id=str(task_id), scope=str(scope).upper(), step=int(step))
        data = _unwrap(data, ("data", "result"))
        if isinstance(data, dict):
            prefix = "upstream" if str(scope).lower().startswith("up") else "downstream"
            data = _first(data, (prefix + "TaskIds", prefix + "_task_ids", "taskIds", "tasks"))
        return [str(item.get("taskId") if isinstance(item, dict) else item)
                for item in mapping.as_list(data)
                if (item.get("taskId") if isinstance(item, dict) else item) not in (None, "")]

    def get_task_sql(self, task_id):
        data, _ = self._result("get_task_sql", task_id=str(task_id))
        data = _unwrap(data, ("data", "result", "sqls", "statements"))
        if isinstance(data, str):
            return [data]
        out = []
        for item in mapping.as_list(data):
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict):
                sql = _first(item, ("sql", "content", "statement", "etlSql", "etl_sql"))
                if isinstance(sql, str):
                    out.append(sql)
        return out

    # ------------------------------------------------------------------ doctor
    def ping(self, table=None):
        info = self.config.describe()
        info["provider_name"] = self.provider_name
        info["calls"] = self.calls
        if table:
            hits = self.search_table(table)
            info["search_table"] = {"input": table, "matches": len(hits),
                                    "sample": [h.get("qualifiedName") or h.get("name") for h in hits[:5]]}
            info["calls"] = self.calls
        return info

    def close(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def required_operations():
    return REQUIRED_OPERATIONS
