# data-dev-skills

> Vendor-neutral **[Agent Skills](https://github.com/anthropics/skills)** for data
> engineering teams: master-data modeling, source-to-master field mapping, and
> model lineage impact analysis.
>
> Every skill talks to a small **six-operation adapter**, so the same workflows run
> against different internal metadata platforms — JSON-RPC MCP, plain REST, or
> recorded fixtures for an offline demo. No vendor URLs, credentials or business
> tables live in this repository.

[中文说明](./README.zh-CN.md) · [Adapter guide](./docs/adapter.md) · [Install](./docs/install.md) · [Harnesses](./docs/harnesses.md)

## Skills

| Skill | What it does | Docs |
|---|---|---|
| [master-data-modeling](./master-data-modeling/) | Builds a governed master-data model: MPC naming (ISO/IEC 11179), term-root reuse, code-value alignment, multi-source merge and review-ready deliverables | [README](./master-data-modeling/README.md) |
| [master-data-source-mapping](./master-data-source-mapping/) | Walks `dm_mast → model → source` via the adapter, collects metadata/lineage/ETL SQL, and writes a source-table list plus a field-level mapping spreadsheet (TSV) | [README](./master-data-source-mapping/README.md) |
| [model-lineage-impact](./model-lineage-impact/) | Takes a scheduler task id or model table, finds downstream tasks (default 1 hop), parses their ETL SQL, and produces a field-level impact matrix | [README](./model-lineage-impact/README.md) |
| [data-dev-skill-sync](./data-dev-skill-sync/) | Meta skill: say “update data-dev skills” in any harness to pull and redistribute the market | [install.md](./docs/install.md) |

Versions live in [`marketplace.json`](./marketplace.json); changing a skill
requires a version bump and a CHANGELOG entry.

## Why an adapter?

Internal platforms differ: one team exposes MCP tools, another a REST API, a
third only has exported metadata. Instead of hard-coding one vendor's JSON, the
skills call six logical operations:

```
search_table · get_table · get_lineage
get_task_detail · get_task_dependencies · get_task_sql
```

A JSON config maps each operation to a tool/HTTP request and describes how to
read the response. See **[docs/adapter.md](./docs/adapter.md)** for the schema
and examples. Three reference configs ship in [`adapter-configs/`](./adapter-configs/):

- `example.mcp.json` — JSON-RPC MCP with `metadata` / `schedule` services
- `example.rest.json` — generic REST
- `example.mock.json` — offline fixtures (synthetic `VENDOR_A` / `VENDOR_B` data)

## Quick start (offline demo, no credentials)

Requirements: Python 3.8+.

```bash
git clone https://github.com/<your-org>/data-dev-skills.git
cd data-dev-skills

# run the source-mapping skill against recorded fixtures
cd master-data-source-mapping
python scripts/collect_mapping.py dm_demo_security \
  --adapter-config ../adapter-configs/example.mock.json \
  --out ../.tmp/demo

# build the TSVs
python scripts/build_tsv.py --input ../.tmp/demo/mapping.json --out ../.tmp/demo

# run the lineage skill
cd ../model-lineage-impact
python scripts/lineage_impact.py --task 1001 --depth 1 \
  --field scr_cn_full_name --names \
  --adapter-config ../adapter-configs/example.mock.json --out ../.tmp/lineage
```

Connect a real platform by copying one config and setting `DATADEV_API_TOKEN`
(see [docs/adapter.md](./docs/adapter.md)). Verify it first:

```bash
python -m datadev_adapters doctor --config my.json --table your_table
```

## Install into a harness

One subscription, then updates come from git. Replace `<your-org>` with the
GitHub account hosting this repository.

```bash
# macOS / Linux
git clone https://github.com/<your-org>/data-dev-skills.git ~/.data-dev-skills/repo
python ~/.data-dev-skills/repo/scripts/subscribe.py \
  --harness agents --harness claude --harness codex
```

```powershell
# Windows PowerShell
git clone https://github.com/<your-org>/data-dev-skills.git "$env:USERPROFILE\.data-dev-skills\repo"
python "$env:USERPROFILE\.data-dev-skills\repo\scripts\subscribe.py" `
  --harness agents --harness claude --harness codex
```

| `--harness` | Directory |
|---|---|
| `agents` | `~/.agents/skills` (Agent Skills open format) |
| `claude` | `~/.claude/skills` |
| `codex` | `~/.codex/skills` |
| `cursor` | `~/.cursor/skills` |
| `workbuddy` | `~/.codebuddy/skills` |
| `zcode` | `~/.zcode/skills` |
| custom | `--dest <dir>` |

Open a **new chat** afterwards. Details and per-harness notes:
[docs/harnesses.md](./docs/harnesses.md).

### Update / manage

```bash
python ~/.data-dev-skills/repo/scripts/subscribe.py --sync
python ~/.data-dev-skills/repo/scripts/subscribe.py --register-task   # daily 09:30
python ~/.data-dev-skills/repo/scripts/subscribe.py --status
python ~/.data-dev-skills/repo/scripts/subscribe.py --uninstall
```

Or just tell the agent: **“update data-dev skills”**.

User-created `.env` and `output/` folders are preserved across updates. Missing
environment variables are reported per skill, with a `setup_env.py --set`
command to write them without echoing values.

### One-line agent install

Send this to an agent with terminal access (replace `<your-org>`):

```
Read https://raw.githubusercontent.com/<your-org>/data-dev-skills/main/docs/install.md and install data-dev skills for my harness.
```

## Environment variables

| Skill | Required | Optional |
|---|---|---|
| `master-data-modeling` | `MD_OPERATOR`, `MYSQL_HOST/USER/PASS/DB`, `PANEL_BASE_URL`, `TDAI_USER_KEY`, `KS_BASE_URL` | `MYSQL_PORT`, `TDAI_SERVICE_ID`, `WIKI_RULES_*`, `DDM_*` |
| `master-data-source-mapping` | `DATADEV_API_TOKEN` | `DATADEV_ADAPTER_CONFIG`, `DM_LAYER_MODEL_PREFIX`, `DM_LAYER_SOURCE_PREFIX`, `SOURCE_PRIORITY` |
| `model-lineage-impact` | `DATADEV_API_TOKEN` | `DATADEV_ADAPTER_CONFIG` |

`marketplace.json` is the authoritative source for “required / who provides it /
how to obtain it”. Full walkthrough: [docs/install.md](./docs/install.md#environment-variables).

## Using the skills

Talk to the agent in natural language; no command memorization required.

```
Refactor source table ods_scr_base_info into an MPC-compliant master-data model.
Map dm_demo_security back to its source tables and output the field mapping TSV.
Which downstream tasks read dm_demo_security.scr_cn_full_name? Show the impact matrix.
```

Each skill's `SKILL.md` contains the execution workflow, hard stops and review
gates; the README explains the concepts and scripts.

## Repository layout

```
data-dev-skills/
├── README.md / README.zh-CN.md     # English / Chinese overview
├── AGENTS.md / CLAUDE.md           # agent root instructions (workflow, rules, checks)
├── init.sh                         # bootstrap: checks → deps → verification
├── progress.md                     # session-level progress log
├── feature_list.json               # machine-readable feature list
├── CHANGELOG.md                    # versioned changes
├── marketplace.json                # market index: versions + env contract + baselines
├── packages/datadev_adapters/      # adapter source of truth (six operations)
├── adapter-configs/                # example mcp / rest / mock configs
├── scripts/
│   ├── subscribe.py                # subscribe / sync / status / uninstall / scheduled task
│   ├── setup_env.py                # write .env per skill (values not echoed)
│   ├── sync_adapters.py            # mirror adapter package into skills; --check guards drift
│   ├── lint_marketplace.py         # UNLISTED / DEAD / DRIFT detection + --fix
│   └── package_zips.py             # build WorkBuddy ZIPs
├── docs/
│   ├── install.md                  # install, env vars, troubleshooting
│   ├── adapter.md                  # adapter contract and config reference
│   ├── harnesses.md                # per-harness skill directories
│   └── workbuddy.md                # WorkBuddy URL import / ZIP upload
├── tests/
│   ├── test_adapter.py             # config + normalisation + JSON-RPC/REST contract tests
│   ├── test_adapter_demo.py        # offline end-to-end for both metadata skills
│   ├── test_subscribe.py           # subscribe / upgrade / uninstall / preserve
│   ├── test_setup_env.py
│   ├── test_lint_marketplace.py
│   └── test_lineage_impact.py
└── master-data-*/ , model-lineage-impact/   # one self-contained skill per directory
```

## Maintenance

This repository follows the [harness engineering templates](https://walkinglabs.github.io/learn-harness-engineering/zh/resources/templates/):

```bash
./init.sh        # bootstrap + verify
python scripts/lint_marketplace.py --check
python scripts/sync_adapters.py --check
python -m pytest tests/ -q
```

Rules for agents and maintainers are in [AGENTS.md](./AGENTS.md). Highlights:

- bump `marketplace.json` version + add a CHANGELOG entry when skill content changes;
- edit the adapter only in `packages/datadev_adapters/`, then run `scripts/sync_adapters.py`;
- never commit `.env`, tokens, cookies, `output/` or `dist/`;
- run subscription drills with isolated `--prefix` / `--dest`, never against a real home directory.

## Security

- No credentials, internal hostnames or business tables are tracked.
- Demo fixtures are synthetic (`VENDOR_A`, `VENDOR_B`, task ids `1001`/`2001`).
- Tokens are read from environment/`.env` and injected as `${token}`; config files in the repo contain placeholders only.

## License

MIT — see [LICENSE](./LICENSE).
