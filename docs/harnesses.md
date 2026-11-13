# Harness guide · install data-dev skills where your agent looks

`scripts/subscribe.py` copies every skill registered in `marketplace.json` into
one or more skill directories. The default targets are user-level, so the skills
are available in every project.

## Pick a target

| Harness | `--harness` | User-level directory |
|---|---|---|
| Generic Agent Skills | `agents` | `~/.agents/skills` |
| Claude Code | `claude` | `~/.claude/skills` |
| Codex | `codex` | `~/.codex/skills` |
| Cursor | `cursor` | `~/.cursor/skills` |
| WorkBuddy (CodeBuddy core) | `workbuddy` | `~/.codebuddy/skills` |
| ZCode | `zcode` | `~/.zcode/skills` |
| Anything else | `--dest DIR` | the directory that harness scans |

```bash
# subscribe to several targets at once
python scripts/subscribe.py --harness agents --harness claude --harness codex

# project-local install (checked into that project, optional)
python scripts/subscribe.py --dest .claude/skills
```

Each skill lands as `<target>/<skill-name>/SKILL.md`. Start a **new chat** after
installing; most harnesses scan skill directories only at session start.

## Harness notes

### Claude Code

- Reads `~/.claude/skills` (user) and `.claude/skills` (project).
- Skills are invoked automatically by `description`, or explicitly with `/`.
- Manual install: `cp -r master-data-modeling ~/.claude/skills/`.

### Codex

- Reads `~/.codex/skills`.
- Same `SKILL.md` + resources layout; no extra manifest needed.

### Cursor

- Reads `~/.cursor/skills` and project `.cursor/skills`.

### WorkBuddy

- Reads `~/.codebuddy/skills` and the generic `~/.agents/skills`.
- Also supports URL import and ZIP upload; see [workbuddy.md](./workbuddy.md).

### ZCode

- Reads `~/.zcode/skills`.

### Generic `.agents/skills`

The Agent Skills open format is understood by a growing number of harnesses.
If your tool has no documented directory, try `--harness agents` first, then
fall back to `--dest` with the path from its documentation.

### Custom harness

```bash
python scripts/subscribe.py --dest /path/to/harness/skills
```

The script mirrors the whole skill directory (excluding `.env`, `output/`,
caches) and preserves a user-created `.env` / `output/` on updates.

## Manual copy (no subscription)

```bash
git clone https://github.com/<your-org>/data-dev-skills.git
cp -r data-dev-skills/master-data-modeling ~/.claude/skills/
```

You lose update reports, mirror deletion and `.env` preservation. Prefer
`subscribe.py` when Python 3.8+ is available.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Skill not listed | Start a new chat; confirm `--status` shows the target directory |
| Skill not triggered | Check `SKILL.md` has YAML frontmatter with `name` and `description` |
| Nested directory (`skill/skill/SKILL.md`) | Re-run `subscribe.py`; do not unzip/copy by hand |
| Updates did not remove a deleted file | The script mirrors (delete + copy); re-run `--sync` |
| `.env` disappeared after update | Should not happen; restore from the preserved copy and report a bug |
