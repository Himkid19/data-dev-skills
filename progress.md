# progress.md · 项目进度日志

> 每轮会话结束**追加**一条记录；新会话开工先读「当前已验证状态」。
> 会话记录模板：本轮目标 / 已完成 / 运行过的验证 / 提交记录 / 已知风险 / 下一步最佳动作。
>
> 本文件自公开版本起记录；更早的内部迭代已按开源要求移除。

## 当前已验证状态（唯一真相）

- **仓库**：`data-dev-skills`（分支 `oss/data-dev-skills`；公开地址 `https://github.com/<your-org>/data-dev-skills`）
- **标准启动路径**：`./init.sh`（环境检查 → 装 pytest → lint + adapter 镜像校验 + 测试）
- **标准验证路径**：
  `python scripts/lint_marketplace.py --check && python scripts/sync_adapters.py --check && python -m pytest tests/ -q`
- **用户安装入口**：把 `docs/install.md` 的 raw 链接发给 agent（一句话安装）→ clone →
  `subscribe.py --harness ...` → 按技能引导 `scripts/setup_env.py` 写 `.env`（值不回显）；
  变量口径来源 = `marketplace.json` 的 `skills[].env`
- **平台接入入口**：`docs/adapter.md`；离线演示用 `adapter-configs/example.mock.json`，
  真实服务用 `python -m datadev_adapters doctor --config <file> --table <name>` 先自检
- **当前最高优先级未完成功能**：`feature_list.json` 的 `f-013`（真实平台 adapter smoke）
- **当前 blocker**：本机无真实元数据服务凭据/内网访问；以本地 JSON-RPC/REST 契约测试 +
  合成 fixture 端到端替代，真实 token 的 smoke 需在目标环境执行

## 会话记录

### 2026-11-13（五）· 开源改造为 data-dev-skills + 可插拔 adapter

- **本轮目标**：把内部主数据技能仓库改造为可公开的 data-dev skill 集合：业务脱敏、
  元数据接口抽象成 adapter（兼容不同服务），补齐中英 README、各 harness 安装文档与 CI，
  用合成数据验证端到端链路
- **已完成**：
  - adapter 包 `packages/datadev_adapters/`（六操作 + jsonrpc-mcp/rest/mock 三 provider +
    JSON 配置与 `${token}` 注入）与三份示例配置；`scripts/sync_adapters.py` 镜像与漂移守卫
  - 两个元数据技能切换到 adapter（`--adapter-config`），删除各自 `mcp_client.py`；
  `mapping.extract` 支持 `response.path` 占位符；`build_tsv.py` 自动创建输出目录
  - `master-data-modeling` 去硬编码地址、资产转 `.example`、词表/案例/文档脱敏
  - 市场改名 `data-dev-skills`、元技能改名 `data-dev-skill-sync`、订阅目录
    `~/.data-dev-skills`、计划任务 `data-dev-skills-sync`、凭据提示改 GitHub token
  - 中英 `README`、`docs/adapter.md`、`docs/harnesses.md`、重写 `docs/install.md` /
    `docs/workbuddy.md` / `AGENTS.md` / `CLAUDE.md` / `init.sh`；`.github/workflows/ci.yml`；
    MIT `LICENSE`；`.gitignore` 补 `.tmp/` / `dist/` / 本地 adapter 配置
  - 新增 `tests/test_adapter.py`（配置 + 归一 + 映射 + 本地 JSON-RPC/REST 契约）与
    `tests/test_adapter_demo.py`（两技能 mock 端到端）；脱敏 `test_lineage_impact.py`
    与 `test_build_tsv.py`
- **运行过的验证**：
  - `python -m pytest tests/ -q` → **34 passed**
  - mock 端到端：`collect_mapping.py dm_demo_security --adapter-config ... --out ...` →
    4 列 / 1 模型 / 2 来源 / 0 errors；`build_tsv.py` 产出两份 TSV；
    `lineage_impact.py --task 1001 --depth 1 --field scr_cn_full_name --names` → 5 份文件
  - `python scripts/lint_marketplace.py --check` → 4 个技能目录 / 4 条登记通过
  - `python scripts/sync_adapters.py --check` → 镜像一致
  - 隔离目录订阅演练：`subscribe.py --repo . --dest <tmp> --prefix <tmp>` 通过
- **提交记录**：公开分支采用**无内部历史的孤立提交**——根提交包含全部公开文件
  （不再携带内部历史），随后一条 baseline 刷新提交；推送时只推 `oss/data-dev-skills`
- **已知风险**：
  - 真实元数据平台未在线验证：不同平台的响应结构可能超出示例配置的映射能力，
    需要用 `doctor` + 实际表名逐项校准 `response.path` / `item`
  - `master-data-modeling` 的 MySQL / Panel / KS 仍依赖部署方提供；本机无法回归
  - 名称/路径重命名后，老订阅用户需要重新 clone 到 `~/.data-dev-skills`
- **下一步最佳动作**：在目标环境用真实 token 跑一次 `python -m datadev_adapters doctor
  --config <真实配置> --table <已知表>` 与两条技能链路；把差异补进 `docs/adapter.md`
  的“常见响应形态”小节
