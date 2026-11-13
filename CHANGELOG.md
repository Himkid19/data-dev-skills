# CHANGELOG

> 版本化变更记录，与 `marketplace.json` 的版本号一一对应；分发层/工程化变化按日期记录。
> 维护规则见 [AGENTS.md](./AGENTS.md)：改技能必须 bump 版本 + 在此登记。
>
> 本文件自公开版本起记录；更早的内部迭代已按开源要求移除（含业务表名、内部域名与人员信息）。

## 2026-11-13

### 开源改造：通用 data-dev skill 集合 + 可插拔 adapter

- **仓库定位**：从内部新闻主数据技能仓库改为 `data-dev-skills`——面向数据开发团队的通用技能集合；
  `README.md` 英文、`README.zh-CN.md` 中文，GitHub Actions 取代旧 CI，
  新增 MIT `LICENSE`，移除与内部业务/域名/人员相关的历史台账
- **adapter 层**（新）：`packages/datadev_adapters/` 提供六个逻辑操作
  （`search_table` / `get_table` / `get_lineage` / `get_task_detail` /
  `get_task_dependencies` / `get_task_sql`），传输层与响应归一分离；支持
  `jsonrpc-mcp` / `rest` / `mock` 三种 provider；配置为 JSON（`adapter-configs/example.*.json`），
  密钥只经 `token_env`（默认 `DATADEV_API_TOKEN`）与 `${token}` 注入
- **adapter 镜像**：`scripts/sync_adapters.py` 把单一源码镜像到各技能的
  `scripts/_shared/datadev_adapters/`，`--check` 防漂移（CI 必跑）
- **响应映射增强**：`response.path` 支持 `{scope}` / `{direction}` 占位符，
  REST 配置可按请求参数选择子树
- **配置示例与离线数据**：新增 `example.mcp.json` / `example.rest.json` / `example.mock.json`
  与合成 fixture（`dm_demo_security`、`pdata_demo_security`、`odata_vendor_a/b`、
  任务 `1001`/`1002`/`1003`/`2001`/`2002`）
- **分发层**：`subscribe.py` 默认状态目录改为 `~/.data-dev-skills`，计划任务名
  `data-dev-skills-sync`，凭据提示改为 GitHub Personal access token；
  `data-dev-skill-sync`（v2.0.0，由旧市场客户端元技能改名）支持"更新 data-dev 技能"触发
- **文档**：新增 `docs/adapter.md`（adapter 契约与配置参考）、`docs/harnesses.md`
  （各 harness 技能目录）；重写 `docs/install.md`、`docs/workbuddy.md`、
  `README.md`、`README.zh-CN.md`、`AGENTS.md`、`CLAUDE.md`、`init.sh`
- **测试**：新增 `tests/test_adapter.py`（配置/归一/映射 + 本地 JSON-RPC/REST 契约）、
  `tests/test_adapter_demo.py`（两个元数据技能 mock 端到端）；
  `tests/test_subscribe.py` 锁定新市场名与 GitHub 凭据提示；
  `tests/test_lineage_impact.py` 去内部表名

### 技能版本

- `master-data-modeling` **2.0.2 → 3.0.0**：去除硬编码的数据库/知识库/术语平台地址，
  改为 `.env` 全量配置；`DDM_API_URL` / `DDM_PORTAL_URL` 必填、`DDM_VERIFY_SSL` 可选；
  资产转 `.example` 模板（`reference-case.example.md`、`field_cache.example.csv`、
  `code_values.example.csv`、`code_value_mappings.example.csv`）；删除 xlsx / 业务案例 CSV；
  文档与词表去除内部业务标识（不兼容调整）
- `master-data-source-mapping` **1.0.1 → 2.0.0**：旧令牌变量改为 `DATADEV_API_TOKEN`，
  `mcp_client.py` 删除并切换到 adapter；分层前缀 `DM_LAYER_MODEL_PREFIX` /
  `DM_LAYER_SOURCE_PREFIX` 可配，来源顺序 `SOURCE_PRIORITY` 可配；
  `build_tsv.py` 不再硬编码来源库前缀，并自动创建输出目录；测试数据脱敏
- `model-lineage-impact` **0.1.4 → 0.2.0**：切换到 adapter（`--adapter-config`），
  旧令牌变量改为 `DATADEV_API_TOKEN`，示例任务号改为 `1001` / `dm_demo_security`
- `data-dev-skill-sync` **1.2.0 → 2.0.0**：元技能改名与重写，订阅目录
  `~/.data-dev-skills`，凭据流程改为 GitHub token，环境变量引导改为 adapter 口径
