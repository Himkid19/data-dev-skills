"""Adapter contract tests: config, mapping, normalisation and transports.

The JSON-RPC and REST tests run against a local HTTP server, so they are true
contract tests of the transport layer (no network, no credentials).
"""

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "packages"))

from datadev_adapters import mapping  # noqa: E402
from datadev_adapters.base import (  # noqa: E402
    normalize_column,
    normalize_lineage_item,
    normalize_table,
    normalize_task_detail,
)
from datadev_adapters.config import load_config  # noqa: E402
from datadev_adapters.errors import AdapterConfigError, AdapterRequestError  # noqa: E402
from datadev_adapters.registry import adapter_from_env  # noqa: E402


def write_config(tmp_path, doc, name="adapter.json"):
    path = tmp_path / name
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return path


class _Server:
    """Minimal HTTP server fixture with a per-test handler factory."""

    def __init__(self, handler_cls):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        host, port = self.server.server_address[:2]
        return "http://%s:%d" % (host, port)

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def _send_json(handler, payload, status=200):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


JSONRPC_RESPONSES = {
    "search_table": {"records": [
        {"guid": "g-master", "name": "dm_demo_security",
         "qualifiedName": "dm_master.dm_demo_security"},
    ]},
    "get_table": {"data": {
        "guid": "g-master", "name": "dm_demo_security", "db": "dm_master",
        "columns": [{"name": "SCR_CD", "comment": "security code"}],
        "technicalMetadata": {"horaeTasks": [{"taskId": 1003, "taskName": "load"}]},
    }},
    "get_lineage": {"data": {"upstreamTables": [
        {"guid": "g-model", "qualifiedName": "pdata_demo.pdata_demo_security"},
    ]}},
    "get_task_detail": {"data": {"taskId": "1001", "taskName": "load_a"}},
    "get_task_dependencies": {"data": {"downstreamTaskIds": ["1003"]}},
    "get_task_sql": {"data": [{"sql": "insert overwrite table t select 1"}]},
}


@pytest.fixture()
def jsonrpc_server():
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", 0))
            request = json.loads(self.rfile.read(length or 0) or b"{}")
            tool = request.get("params", {}).get("name")
            received.append({
                "tool": tool,
                "arguments": request.get("params", {}).get("arguments"),
                "authorization": self.headers.get("Authorization"),
            })
            if tool == "missing_tool":
                _send_json(self, {"jsonrpc": "2.0", "id": request.get("id"),
                                  "error": {"message": "tool not found"}})
                return
            _send_json(self, {"jsonrpc": "2.0", "id": request.get("id"),
                              "result": {"structuredContent": JSONRPC_RESPONSES[tool]}})

        def log_message(self, *args):
            pass

    server = _Server(Handler)
    try:
        yield server.url, received
    finally:
        server.close()


@pytest.fixture()
def rest_server():
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            parsed = urlparse(self.path)
            query = {k: v[0] for k, v in parse_qs(parsed.query, keep_blank_values=True).items()}
            received.append({
                "path": parsed.path,
                "query": query,
                "authorization": self.headers.get("Authorization"),
            })
            if parsed.path == "/tables":
                _send_json(self, {"data": {"items": [
                    {"id": "g1", "name": query.get("name", "t"),
                     "qualified_name": "db.t"},
                ]}})
            elif parsed.path == "/tables/g1":
                _send_json(self, {"data": {
                    "guid": "g1", "name": "t", "db": "db",
                    "columns": [{"name": "COL", "comment": "c"}],
                    "technicalMetadata": {"horaeTasks": [{"taskId": 9, "taskName": "load"}]},
                }})
            elif parsed.path == "/tables/g1/lineage":
                _send_json(self, {"data": {"upstream": [
                    {"id": "g0", "qualified_name": "db.up"},
                ]}})
            elif parsed.path == "/tasks/1001":
                _send_json(self, {"data": {"taskId": "1001", "taskName": "load"}})
            elif parsed.path == "/tasks/1001/dependencies":
                _send_json(self, {"data": {"DOWNSTREAM": [{"taskId": "1003"}]}})
            elif parsed.path == "/tasks/1001/sql":
                _send_json(self, {"data": {"statements": [{"sql": "insert into t select 1"}]}})
            else:
                _send_json(self, {"error": "not found"}, status=404)

        def log_message(self, *args):
            pass

    server = _Server(Handler)
    try:
        yield server.url, received
    finally:
        server.close()


REST_OPERATIONS = {
    "search_table": {
        "method": "GET", "path": "/tables",
        "query": {"name": "{name}", "db": "{db}"},
        "response": {"path": "data.items",
                     "item": {"guid": "id", "name": "name", "qualifiedName": "qualified_name"}},
    },
    "get_table": {"method": "GET", "path": "/tables/{guid}", "response": {"path": "data"}},
    "get_lineage": {
        "method": "GET", "path": "/tables/{guid}/lineage",
        "query": {"direction": "{direction}"},
        "response": {"path": "data.{direction}"},
    },
    "get_task_detail": {
        "method": "GET", "path": "/tasks/{task_id}", "response": {"path": "data"},
    },
    "get_task_dependencies": {
        "method": "GET", "path": "/tasks/{task_id}/dependencies",
        "query": {"scope": "{scope}", "depth": "{step}"},
        "response": {"path": "data.{scope}"},
    },
    "get_task_sql": {
        "method": "GET", "path": "/tasks/{task_id}/sql",
        "response": {"path": "data.statements", "item": "sql"},
    },
}


def test_config_expands_token_and_resolves_fixtures(tmp_path):
    (tmp_path / "fixtures").mkdir()
    doc = {
        "provider": "rest",
        "endpoint": "https://metadata.example.com/api",
        "token_env": "MY_TOKEN",
        "headers": {"X-Api-Key": "${token}"},
        "fixtures": "fixtures",
    }
    config = load_config(path=write_config(tmp_path, doc),
                         env={"MY_TOKEN": "abc"}, skill_root=tmp_path)
    assert config.provider == "rest"
    assert config.headers["X-Api-Key"] == "abc"
    assert config.token == "abc"
    assert config.fixture_path("x") == str(tmp_path / "fixtures")


def test_config_rejects_unknown_provider(tmp_path):
    config = load_config(path=write_config(tmp_path, {"provider": "grpc"}),
                         env={}, skill_root=tmp_path)
    with pytest.raises(AdapterConfigError):
        adapter_from_env(path=write_config(tmp_path, {"provider": "grpc"}),
                         env={}, skill_root=tmp_path)


def test_mapping_extract_pick_and_item():
    payload = {"data": {"DOWNSTREAM": [{"id": 1}, {"id": 2}], "UPSTREAM": [{"id": 0}]}}
    spec = {"path": "data", "pick": {"DOWNSTREAM": "DOWNSTREAM", "UPSTREAM": "UPSTREAM"},
            "item": {"guid": "id"}}
    assert mapping.extract(payload, spec, {"scope": "DOWNSTREAM"}) == [{"guid": 1}, {"guid": 2}]
    assert mapping.extract(payload, spec, {"scope": "UPSTREAM"}) == [{"guid": 0}]
    assert mapping.get_path(payload, "data.UPSTREAM.0.id") == 0
    assert mapping.render({"taskId": "{task_id}", "step": "{step}"},
                          {"task_id": 7, "step": "1"}) == {"taskId": 7, "step": "1"}


def test_normalisation_handles_common_envelopes():
    table = normalize_table({"data": {
        "name": "t", "dbName": "db",
        "columns": {"COL_A": "a", "col_b": "b"},
        "technicalMetadata": {"horaeTasks": [{"id": 9, "name": "load"}]},
    }})
    assert table["db"] == "db"
    assert [c["name"] for c in table["columns"]] == ["col_a", "col_b"]
    assert table["tasks"] == [{"taskId": "9", "taskName": "load"}]

    assert normalize_column("MKT_CD") == {
        "name": "mkt_cd", "comment": "", "codedsetCode": None, "codedsetChnName": None}
    assert normalize_task_detail({"data": [{"id": 1, "name": "n"}]}) == {
        "taskId": "1", "taskName": "n", "taskDesc": ""}
    assert normalize_lineage_item({"qualifiedName": "db.Table"})["table"] == "table"


def test_decode_sse_body():
    from datadev_adapters.jsonrpc_mcp import _decode_body
    raw = b'event: message\ndata: {"result": {"structuredContent": {"ok": true}}}\n\n'
    assert _decode_body(raw, "text/event-stream") == {
        "result": {"structuredContent": {"ok": True}}}
    with pytest.raises(AdapterRequestError):
        _decode_body(b"not-json", "application/json")


def test_jsonrpc_contract(tmp_path, jsonrpc_server):
    endpoint, received = jsonrpc_server
    doc = {"provider": "jsonrpc-mcp", "endpoint": endpoint, "token": "secret-token",
           "timeout": 5, "retries": 0}
    adapter = adapter_from_env(path=write_config(tmp_path, doc), env={})

    assert adapter.search_table("dm_demo_security")[0]["guid"] == "g-master"
    assert received[0]["tool"] == "search_table"
    assert received[0]["arguments"]["name"] == "dm_demo_security"
    assert received[0]["authorization"] == "Bearer secret-token"

    table = adapter.get_table("g-master")
    assert table["name"] == "dm_demo_security"
    assert table["columns"][0]["name"] == "scr_cd"
    assert table["tasks"][0]["taskId"] == "1003"

    assert adapter.get_lineage("g-master")[0]["table"] == "pdata_demo_security"
    assert adapter.get_task_detail("1001")["taskName"] == "load_a"
    assert adapter.get_task_dependencies("1001") == ["1003"]
    assert adapter.get_task_sql("1001") == ["insert overwrite table t select 1"]
    assert adapter.calls == 6


def test_rest_contract(tmp_path, rest_server):
    endpoint, received = rest_server
    doc = {"provider": "rest", "endpoint": endpoint, "token": "rest-token",
           "timeout": 5, "retries": 0, "operations": REST_OPERATIONS}
    adapter = adapter_from_env(path=write_config(tmp_path, doc), env={})

    assert adapter.search_table("t", "db")[0]["guid"] == "g1"
    assert received[0]["query"] == {"name": "t", "db": "db"}
    assert received[0]["authorization"] == "Bearer rest-token"

    table = adapter.get_table("g1")
    assert table["columns"][0]["name"] == "col"
    assert table["tasks"][0]["taskId"] == "9"
    assert adapter.get_lineage("g1", "upstream")[0]["table"] == "up"
    assert adapter.get_task_detail("1001")["taskName"] == "load"
    assert adapter.get_task_dependencies("1001", "DOWNSTREAM", 1) == ["1003"]
    assert adapter.get_task_sql("1001") == ["insert into t select 1"]


def test_mock_example_config_is_consistent():
    adapter = adapter_from_env(path=REPO / "adapter-configs" / "example.mock.json", env={})
    assert adapter.search_table("dm_demo_security")[0]["guid"] == "g-master"
    assert adapter.get_table("g-master")["columns"][0]["name"] == "scr_cd"
    assert adapter.get_lineage("g-master")[0]["table"] == "pdata_demo_security"
    assert adapter.get_task_dependencies("1001") == ["1003"]
    assert adapter.get_task_sql("1001")[0].startswith("create table")
