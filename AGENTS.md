# AGENTS.md · 根指令（agent 开工必读）

> 本仓库是 **data-dev-skills**：面向数据开发团队的通用技能仓库 + 订阅分发层 + 市场索引。
> 任何 agent（Codex / ZCode / Claude Code / WorkBuddy / OpenCode 会话内开发等）在本仓库
> 工作前先读完本文件。
> 模板来源：[harness engineering templates](https://walkinglabs.github.io/learn-harness-engineering/zh/resources/templates/)。

## 开工流程

1. 读 [progress.md](./progress.md) 的「当前已验证状态」——项目进展的唯一真相
2. 读 [feature_list.json](./feature_list.json)，找 `priority` 最小且 `status != passing` 的条目作为本轮目标
3. 跑**标准验证命令**确认基线是绿的。**失败即停**：先修复基线，不在坏基础上叠新功能

## 标准命令（唯一真相）

```bash
# 验证（提交前 / 收尾必绿）
python scripts/lint_marketplace.py --check \
  && python scripts/sync_adapters.py --check \
  && python -m pytest tests/ -q

# 自举（谁 clone 都能直接用：装依赖 → 验证 → 打印下一步）
./init.sh

# 订阅演练（必须用隔离目录，禁止写真实用户目录）
TMP=$(mktemp -d) && python scripts/subscribe.py --repo . --dest "$TMP/dest" --prefix "$TMP/prefix"
```

## 工作规则：每次改动都有记录和入册

**改技能内容**（`master-data-*/`、`model-lineage-impact/`、`data-dev-skill-sync/`
目录下任何文件）：

1. bump `marketplace.json` 对应条目的 `version`（semver：规则修补 +0.0.1、
   新能力 +0.1.0、不兼容调整 +1.0.0）
2. [CHANGELOG.md](./CHANGELOG.md) 加一条（日期 + 技能 + 版本 + 一句话改动）
3. `python scripts/lint_marketplace.py --fix` 刷新 baseline 并回写
   —— `--check` 会拦截"内容改了但版本没动 / 版本动了但基线没刷新"

**改 adapter**（`packages/datadev_adapters/`）：

1. 只在 `packages/datadev_adapters/` 改，**不要直接改**各技能下的 `scripts/_shared/datadev_adapters/`
2. 跑 `python scripts/sync_adapters.py` 镜像到技能目录
3. `python scripts/sync_adapters.py --check` 与 `tests/test_adapter.py` 必须绿
4. 配置 schema 或契约有变化时同步更新 `docs/adapter.md` 与三份 `adapter-configs/*.json`

**新增技能**：建目录 + `SKILL.md`（frontmatter 含 name/description）→
`lint --fix` 自动入册（0.1.0 起步，摘要取自 description）→ 手动精修
`marketplace.json` 里的 `summary` 和 `version`。需要凭据的技能附 `.env.example`
（订阅端会据此提醒用户配置）。

**删除技能**：删目录 → `lint --fix` 自动移除条目。

**改分发层**（`scripts/subscribe.py`、`scripts/package_zips.py`、`.github/workflows/`）：
pytest 全绿 + 跑一遍上面的订阅演练（隔离目录）。

## 开源卫生（硬性）

- 不提交真实业务表名、内部域名、人员姓名、工号、任务号；演示数据统一用
  `VENDOR_A` / `VENDOR_B`、`dm_demo_*`、`ods_*`、任务号 `1001` / `2001`
- 不提交 `.env`、令牌、Cookie、连接串；示例配置只放 `${token}` 占位符
- 业务案例只保留 `.example` 模板（`reference-case.example.md`、`*.example.csv`），
  真实资产不入库
- 新加文档/测试前先 grep 一遍内部标识：`git grep -n -i "<内部关键字>"` 应为空

## 硬性约束

- `.env`、令牌、Cookie **永不入库**；`output/`、`dist/`、`.tmp/` 运行期产物不入库（.gitignore 已配）
- 演练一律用隔离的 `--prefix` / `--dest`，禁止写 `~/.claude`、`~/.agents`、`~/.codebuddy` 等真实用户目录
- `lint --check`、`sync_adapters --check` 与 pytest 不绿：不提交、不收工、不汇报"完成"
- `marketplace.json` 的 `baseline` 字段由 lint 维护，不要手改

## 收尾检查（clean state）

1. 标准验证命令全绿
2. 本轮改动已记入 CHANGELOG.md（版本变化）和 progress.md（会话记录，模板见文件内）
3. 没有假 passing：验证命令真实跑过、输出看过
4. 下一轮会话**不需要人工修复就能继续**
