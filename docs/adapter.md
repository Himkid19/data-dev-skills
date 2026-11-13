# Adapter layer · connect the skills to your own metadata platform

The three skills never call one vendor's API directly. They talk to a small
**six-operation adapter** (`packages/datadev_adapters/`). Point the adapter at
your metadata service with a JSON file, and the same skills run against a
JSON-RPC MCP server, a plain REST API, or recorded fixtures.

```
skill scripts (collect_mapping / lineage_impact)
        │  search_table · get_table · get_lineage
        │  get_task_detail · get_task_dependencies · get_task_sql
        ▼
datadev_adapters  ── jsonrpc-mcp │ rest │ mock
        │
        ▼
your metadata / scheduling platform
```

## The six operations

Every provider returns the same canonical shapes; response unfolding is done by
the adapter, not by the skills.

| Operation | Arguments | Canonical result |
|---|---|---|
| `search_table` | `name`, `db` | `[{"guid", "name", "qualifiedName", "db"}]` |
| `get_table` | `guid` | `{"guid", "name", "db", "qualifiedName", "comment", "columns": [{"name", "comment", "codedsetCode", "codedsetChnName"}], "tasks": [{"taskId", "taskName"}]}` |
| `get_lineage` | `guid`, `direction` (`upstream`/`downstream`) | `[{"guid", "qualifiedName", "table"}]` |
| `get_task_detail` | `task_id` | `{"taskId", "taskName", "taskDesc"}` |
| `get_task_dependencies` | `task_id`, `scope` (`DOWNSTREAM`/`UPSTREAM`), `step` | `["task-id", ...]` |
| `get_task_sql` | `task_id` | `["insert ...", ...]` |

A provider only implements raw transport (`_invoke`). Normalisation lives in
`base.py`, so a new backend behaves the same for skills and tests.

## Configuration discovery

Resolution order:

1. `--adapter-config FILE` (skill CLI flag)
2. `DATADEV_ADAPTER_CONFIG` — a file path, or inline JSON
3. `DATADEV_ADAPTER_JSON` — inline JSON

`.env` in the skill directory is read automatically; real environment variables
win over `.env`.

Secrets are **never committed**. Put the token in `DATADEV_API_TOKEN` (or set
`token_env` to another variable name) and reference it as `${token}` inside
`headers`.

## Config schema

```jsonc
{
  "provider": "jsonrpc-mcp",          // jsonrpc-mcp | rest | mock
  "endpoint": "https://host/api",     // single endpoint …
  "endpoints": {                      // … or route by service
    "metadata": "https://host/metadata/mcp",
    "schedule": "https://host/schedule/mcp"
  },
  "token_env": "DATADEV_API_TOKEN",
  "headers": { "Authorization": "Bearer ${token}" },
  "timeout": 60,
  "retries": 3,
  "verify_ssl": true,
  "operations": {
    "search_table": {
      "service": "metadata",          // optional: pick endpoints.<service>
      "tool": "metadata.search_table",// jsonrpc-mcp: MCP tool name
      "method": "GET",                // rest: HTTP method
      "path": "/tables",              // rest: URL path (templates allowed)
      "query": { "name": "{name}" },  // rest: query string
      "body": { "name": "{name}" },   // rest: JSON body
      "arguments": {                  // jsonrpc-mcp: tool arguments
        "dbName": "{db}",
        "tableName": "{name}"
      },
      "drop_empty": true,             // jsonrpc-mcp: drop empty arguments
      "response": {
        "path": "data.records",       // dotted path into the payload
        "item": {                     // map each row to canonical names
          "guid": "guid",
          "name": "name",
          "qualifiedName": "qualifiedName"
        }
      }
    }
  }
}
```

### Response mapping

| Key | Meaning |
|---|---|
| `response.path` | dotted path to the useful subtree (`""` / `$` = payload itself); may contain `{scope}` / `{direction}` placeholders |
| `response.item` | map each list element; a dict maps field names, a string picks one field, `"."` keeps scalars |
| `response.pick` | choose a subtree by request parameter, e.g. `{"DOWNSTREAM": "downstreamTaskIds", "UPSTREAM": "upstreamTaskIds"}` |

Request templates use `{name}`, `{guid}`, `{task_id}`, `{scope}`, `{step}`,
`{direction}`. When a template is exactly one placeholder, the original type is
preserved.

## Examples

- `adapter-configs/example.mcp.json` — JSON-RPC MCP with two services.
- `adapter-configs/example.rest.json` — plain REST API.
- `adapter-configs/example.mock.json` — offline fixtures (`packages/datadev_adapters/fixtures/demo`).

Copy the closest one, edit endpoints/tool names/paths, and point
`DATADEV_ADAPTER_CONFIG` at it.

### JSON-RPC MCP

Works with any MCP server exposing `tools/call` (stateless Streamable HTTP).
Responses may be plain JSON or SSE (`data:` lines). Both `structuredContent` and
text content blocks are supported.

### REST

One operation = one HTTP request. Use `service` to route operations to different
`endpoints`. Non-2xx responses raise a readable error; 429/5xx are retried.

### Mock

Fixture filename = `<operation>.json`. A fixture is either the payload itself,
or `{"default": <payload>, "by": {"<key>": <payload>}}`, where `<key>` is the
table name, guid or task id (or the JSON encoding of all parameters).

## Verify a config

```bash
# resolved config, no network call, token masked
python -m datadev_adapters config --config adapter-configs/example.mcp.json

# one real search_table call
python -m datadev_adapters doctor --config my.json --table dm_demo_security

# list MCP tools (jsonrpc-mcp only)
python -m datadev_adapters tools --config my.json
```

Offline end-to-end demo:

```bash
cd master-data-source-mapping
python scripts/collect_mapping.py dm_demo_security \
  --adapter-config ../adapter-configs/example.mock.json --out ../.tmp/demo
```

## Add a new platform

1. Copy `adapter-configs/example.mcp.json` or `example.rest.json`.
2. Fill in endpoints, credentials env name, and the six operations.
3. Run `python -m datadev_adapters doctor --config <file> --table <known-table>`.
4. Run the skill with `--adapter-config <file>`; no Python code changes needed.

Only if the platform has a genuinely new transport (e.g. gRPC) do you implement
a provider: subclass `MetadataAdapter`, implement `_invoke`, register it in
`registry.py`, and add a contract test in `tests/test_adapter.py`.

## Source layout and mirroring

`packages/datadev_adapters/` is the single source of truth. Each skill needs a
copy because harnesses distribute one directory at a time:

```
packages/datadev_adapters/            <- edit here
master-data-source-mapping/scripts/_shared/datadev_adapters/
model-lineage-impact/scripts/_shared/datadev_adapters/
```

After editing the package run:

```bash
python scripts/sync_adapters.py          # mirror
python scripts/sync_adapters.py --check  # CI guard: fail on drift
```

## Security notes

- Tokens, cookies and internal endpoints stay in `.env` / local JSON files, never in git.
- The config only holds `${token}` placeholders and public endpoint examples.
- `verify_ssl` defaults to `true`; disable it only for a self-signed test host.
- Fixtures in this repository are synthetic (`VENDOR_A` / `VENDOR_B`, `1001`, `dm_demo_security`).
