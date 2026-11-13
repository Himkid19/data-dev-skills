# Installation guide · data-dev-skills

This repository is a git-subscription skill marketplace. One subscription keeps
the skills in sync with your harness; you do not copy directories by hand.

> 中文安装说明见 [README.zh-CN.md](../README.zh-CN.md) 与各技能 README；命令完全一致。

## One-line install (send this to your agent)

Replace `<your-org>` with the GitHub account that hosts the repository:

```
Read https://raw.githubusercontent.com/<your-org>/data-dev-skills/main/docs/install.md and install data-dev skills for my harness.
```

Any harness with terminal access (Claude Code / Codex / Cursor / WorkBuddy /
ZCode / other) can follow the checklist below. The `data-dev-skill-sync` meta
skill is installed with the market, so afterwards you can just say
**“update data-dev skills”**.

## Agent checklist

### 1. Clone the repository

Public repository:

```bash
git clone https://github.com/<your-org>/data-dev-skills.git ~/.data-dev-skills/repo
```

Private fork — ask the user for a GitHub **personal access token** with read
access to the repository, then:

```bash
git clone "https://<user>:<token>@github.com/<your-org>/data-dev-skills.git" ~/.data-dev-skills/repo
```

Rules:

- Never echo the token into the chat or write it to a tracked file.
- If the directory already exists, run `git pull --ff-only` inside it instead.
- URL-encode tokens containing `@` or `:`.
- Windows path: `%USERPROFILE%\.data-dev-skills\repo`.

### 2. Subscribe

```bash
python <repo>/scripts/subscribe.py --harness <user's harness...>
```

- Not sure which harness? Use `--harness agents` plus the one the user names.
- Known values: `agents`, `claude`, `codex`, `cursor`, `workbuddy`, `zcode`.
- Custom directory: `--dest <path>`.
- Tell the user to open a **new chat** afterwards.

### 3. Configure environment variables

If the sync output ends with `⚠️ 以下技能需要配置环境变量`, walk through the
[environment section](#environment-variables) skill by skill, then write the
values with:

```bash
python <repo>/scripts/setup_env.py --skill <skill> --set KEY=VALUE [--set KEY=VALUE ...]
```

List requirements without showing values:

```bash
python <repo>/scripts/setup_env.py --skill <skill> --list
```

### 4. Smoke-test the metadata adapter

The two metadata skills need a working adapter. With the offline demo:

```bash
cd <repo>/master-data-source-mapping
python scripts/collect_mapping.py dm_demo_security \
  --adapter-config ../adapter-configs/example.mock.json --out ../.tmp/demo
```

With a real service:

```bash
python -m datadev_adapters doctor --config <user-config>.json --table <known-table>
```

### 5. Report

Tell the user which skills were installed, which directories received them, and
that **“update data-dev skills”** updates everything later.

## Environment variables

Principle: the user supplies what they can obtain themselves; platform
credentials come from the data platform administrator. Never echo secret values.

### master-data-modeling

| Variable group | Who fills it | How to get it |
|---|---|---|
| `MD_OPERATOR` | **the user** | Your own account or handle, recorded as the operator when writing shared assets |
| `MYSQL_HOST` / `MYSQL_USER` / `MYSQL_PASS` / `MYSQL_DB` | platform admin | Shared MySQL holding term roots, field cache and code values |
| `MYSQL_PORT` | optional | Default `3306` |
| `PANEL_BASE_URL` / `KS_BASE_URL` | platform admin | Knowledge base Panel / retrieval (KS) endpoints |
| `TDAI_USER_KEY` | platform admin | Panel user key (usually `sk-mem-…`) |
| `TDAI_SERVICE_ID` / `WIKI_RULES_*` | optional | Defaults in `.env.example` |
| `DDM_*` | **optional** | Only for “sync term roots”; skip for normal modeling |

### master-data-source-mapping / model-lineage-impact

| Variable | Who fills it | How to get it |
|---|---|---|
| `DATADEV_API_TOKEN` | **the user** | Personal access token of the metadata/scheduling platform |
| `DATADEV_ADAPTER_CONFIG` | optional | Path to the adapter JSON; otherwise pass `--adapter-config` |
| `DM_LAYER_MODEL_PREFIX` / `DM_LAYER_SOURCE_PREFIX` | optional | Layer prefixes, default `pdata` / `odata` |
| `SOURCE_PRIORITY` | optional | Comma-separated source priority such as `VENDOR_A,VENDOR_B` |

Adapter configuration is described in [adapter.md](./adapter.md). The quickest
start is to copy `adapter-configs/example.mcp.json` (or `example.rest.json`,
`example.mock.json`) and edit it.

**Two guarantees:**

- An existing `.env` and `output/` in the installed skill directory are preserved
  on every `--sync` (staged before mirroring, restored afterwards).
- `.env` is local only: never committed, never packaged, never distributed.

## Prerequisites

| Dependency | Purpose | Check |
|---|---|---|
| git | clone / pull the market | `git --version` |
| Python 3.8+ | run `subscribe.py` and skill scripts | `python --version` |
| Network access | GitHub + your metadata service | `git ls-remote <repo-url>` |
| Adapter access | metadata skills only | `python -m datadev_adapters doctor --config <file>` |

`master-data-modeling` also has Python dependencies in its `requirements.txt`.

## Command-line subscription

```bash
# Windows
git clone https://github.com/<your-org>/data-dev-skills.git "%USERPROFILE%\.data-dev-skills\repo"
python "%USERPROFILE%\.data-dev-skills\repo\scripts\subscribe.py" --harness agents --harness claude

# macOS / Linux
git clone https://github.com/<your-org>/data-dev-skills.git ~/.data-dev-skills/repo
python ~/.data-dev-skills/repo/scripts/subscribe.py --harness agents --harness claude
```

Targets are listed in [harnesses.md](./harnesses.md).

## Update

```bash
# ① on demand
python ~/.data-dev-skills/repo/scripts/subscribe.py --sync

# ② unattended: daily 09:30 (Windows scheduled task / crontab)
python ~/.data-dev-skills/repo/scripts/subscribe.py --register-task
python ~/.data-dev-skills/repo/scripts/subscribe.py --unregister-task

# ③ in chat
update data-dev skills
# (data-dev-skill-sync runs --sync and reports versions + env hints)
```

The report shows `new install / upgraded a -> b / up to date`, the commits pulled,
and any missing environment variables.

## Manage the subscription

```bash
python .../subscribe.py --status      # targets and installed versions
python .../subscribe.py --uninstall   # remove market skills and forget the subscription
python .../subscribe.py --dest <project>/.claude/skills --sync
python .../subscribe.py --harness cursor --sync   # add a target, keep existing ones
```

## Manual install

Copy a skill directory so that `SKILL.md` sits at `<skill>/SKILL.md`. For
WorkBuddy, upload a ZIP from a GitHub Release (or `python scripts/package_zips.py`).
Downside: no update report, no deletion mirroring.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `git pull` failed (offline or local edits) | The script continues with the local copy; restore network or `git stash` first |
| 401 / 403 / repeated password prompts | Regenerate the GitHub token, then `git remote set-url origin https://<user>:<token>@github.com/<your-org>/data-dev-skills.git` |
| Skills missing after sync | Open a **new chat**; check `--status` shows the right target |
| Skill runs but cannot reach metadata | Run `python -m datadev_adapters doctor --config <file> --table <name>` |
| Wrong column names / no source tables | Check `DM_LAYER_MODEL_PREFIX`, `DM_LAYER_SOURCE_PREFIX`, `SOURCE_PRIORITY` |
| Unsure which variables are missing | `python <repo>/scripts/setup_env.py --skill <skill> --list` |
| `.env` asks to be filled again | Should not happen — updates preserve it; report a bug with the sync log |
| Scheduled task does not run | Check the `data-dev-skills-sync` task (Windows Task Scheduler) or crontab |
