# WorkBuddy guide · subscribe to data-dev-skills

Goal: subscribe **once** to the repository, then receive skill updates through
the subscription script or a single sentence in chat — instead of re-uploading
ZIP files by hand.

> WorkBuddy skills documentation: <https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Skills-Market>

## Options

| Path | Prerequisite | Auto-update | Best for |
|---|---|---|---|
| **A. Script subscription (recommended)** | git + Python 3.8+, GitHub reachable | scheduled task / chat trigger | daily users |
| **B. URL import** | WorkBuddy can reach the repository with git credentials | depends on client refresh | users without Python |
| **C. ZIP upload** | a Release artifact or local packaging | ❌ manual each time | machines without git |

WorkBuddy's user skill directory is **`~/.codebuddy/skills`** (Windows:
`%USERPROFILE%\.codebuddy\skills`; WorkBuddy is built on Tencent CodeBuddy). It
also understands the Agent Skills open directory **`~/.agents/skills`**. Path A
mirrors the skills into those directories.

---

## Path A: script subscription (recommended)

### 1) First subscription

```bash
# Windows
git clone https://github.com/<your-org>/data-dev-skills.git "%USERPROFILE%\.data-dev-skills\repo"
python "%USERPROFILE%\.data-dev-skills\repo\scripts\subscribe.py" --harness workbuddy --harness agents

# macOS / Linux
git clone https://github.com/<your-org>/data-dev-skills.git ~/.data-dev-skills/repo
python ~/.data-dev-skills/repo/scripts/subscribe.py --harness workbuddy --harness agents
```

The script reads `marketplace.json`, mirrors each skill directory into
`~/.codebuddy/skills/` and `~/.agents/skills/` (excluding `.env`, `output/`,
`__pycache__`), and records the subscription in
`~/.data-dev-skills/subscription.json`.

Open WorkBuddy → 专家·技能·连接器 → 技能, confirm the skills are listed, then use
`/` in a **new chat**.

> If the sync output shows `⚠️ 以下技能需要配置环境变量`, follow
> [install.md § Environment variables](./install.md#environment-variables).
> Configure once; updates preserve `.env`.

### 2) Updates

**Chat trigger** — the subscription also installs the `data-dev-skill-sync` meta
skill:

```
update data-dev skills
```

**Command line:**

```bash
python "%USERPROFILE%\.data-dev-skills\repo\scripts\subscribe.py" --sync
```

**Unattended** (daily 09:30):

```bash
python "%USERPROFILE%\.data-dev-skills\repo\scripts\subscribe.py" --register-task
```

The Windows task is named `data-dev-skills-sync`; macOS/Linux use crontab.
Remove with `--unregister-task`.

### 3) Manage

```bash
python .../subscribe.py --status
python .../subscribe.py --uninstall
python .../subscribe.py --dest D:\proj\.workbuddy\skills --sync
```

---

## Path B: URL import inside WorkBuddy

1. WorkBuddy → 专家·技能·连接器 → 技能 → **添加技能** → **通过 URL 导入**
2. Paste `https://github.com/<your-org>/data-dev-skills.git`
3. The client scans directories containing `*/SKILL.md`

The machine needs working git credentials for a private fork. If the client
handles private repositories poorly, use Path A — both end up in local skill
directories.

## Path C: ZIP upload (no git)

1. Download a skill ZIP from the GitHub Release, or build locally:
   `python scripts/package_zips.py`
2. WorkBuddy → 技能 → 添加技能 → **上传技能** → choose the ZIP
3. ZIP layout: the first level is the skill directory, e.g.
   `master-data-modeling/SKILL.md`

---

## Version and update mechanics

- `marketplace.json` is the single source of versions. Content changes require a
  version bump (patch = rule fix, minor = new capability, major = breaking).
- `subscribe.py --sync` compares against `subscription.json` and prints
  `new install / upgraded a -> b / up to date`, plus the pulled commit subjects.
- Distribution is a **full mirror**: files deleted upstream disappear locally.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `git pull` failed | The script uses the local copy; check network or `git stash` local edits |
| New skill not in WorkBuddy | Confirm `~/.codebuddy/skills` or `~/.agents/skills`; open a **new chat**; refresh the skill page |
| Skill does not trigger | `SKILL.md` must be at the first level of the skill directory; select it explicitly with `/` |
| Scheduled task does not run | Windows Task Scheduler → `data-dev-skills-sync` → last run result; verify the Python path |
| Install for one project only | `subscribe.py --dest <project>/.workbuddy/skills --sync` |
