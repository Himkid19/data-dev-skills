# data-dev-skills

> 面向数据开发团队的通用 **[Agent Skills](https://github.com/anthropics/skills)** 技能集合：
> 主数据建模、主数据来源字段映射、模型依赖血缘影响排查。
>
> 所有技能通过一层 **六操作 adapter** 访问元数据平台，同一套工作流可对接 JSON-RPC MCP、
> 普通 REST，或用录制 fixture 离线演示。仓库内不含任何真实业务表、内部域名与凭据。

[English](./README.md) · [Adapter 说明](./docs/adapter.md) · [安装](./docs/install.md) · [各 harness 接入](./docs/harnesses.md)

## 技能列表

| Skill | 职能 | 详细文档 |
|---|---|---|
| [master-data-modeling](./master-data-modeling/) | 金融主数据建模：按 MPC 规范（ISO/IEC 11179）完成字段命名、术语根复用、码值对齐、多源整合与 review 交付 | [README](./master-data-modeling/README.md) |
| [master-data-source-mapping](./master-data-source-mapping/) | 沿 `dm_mast → 模型 → 来源` 链路采集元数据/血缘/调度 ETL SQL，输出接入源表清单 + 字段级映射宽表（TSV） | [README](./master-data-source-mapping/README.md) |
| [model-lineage-impact](./model-lineage-impact/) | 输入调度号或模型表名，查下游任务（默认 1 层），解析 ETL SQL，输出字段级影响矩阵 | [README](./model-lineage-impact/README.md) |
| [data-dev-skill-sync](./data-dev-skill-sync/) | 市场客户端元技能：在任意 harness 对话里说"更新 data-dev 技能"即可拉取并同步 | [install.md](./docs/install.md) |

版本号登记在 [`marketplace.json`](./marketplace.json)；技能内容变更必须递增版本并记 CHANGELOG。

## 为什么需要 adapter

不同团队的元数据平台形态不同：有的暴露 MCP 工具，有的是 REST API，有的只能导出元数据。
技能不绑定某一家接口，而是调用六个逻辑操作：

```
search_table · get_table · get_lineage
get_task_detail · get_task_dependencies · get_task_sql
```

一份 JSON 配置把每个操作映射到工具名/HTTP 请求，并声明如何读取响应。完整 schema 与示例见
**[docs/adapter.md](./docs/adapter.md)**。仓库自带三份参考配置：

- `example.mcp.json`：JSON-RPC MCP，按 `metadata` / `schedule` 两个服务路由
- `example.rest.json`：通用 REST
- `example.mock.json`：离线 fixture（合成数据 `VENDOR_A` / `VENDOR_B`）

## 快速开始（离线演示，无需凭据）

前置：Python 3.8+。

```bash
git clone https://github.com/<your-org>/data-dev-skills.git
cd data-dev-skills

# 来源映射：用 fixture 跑通
cd master-data-source-mapping
python scripts/collect_mapping.py dm_demo_security \
  --adapter-config ../adapter-configs/example.mock.json \
  --out ../.tmp/demo
python scripts/build_tsv.py ../.tmp/demo/mapping.json --out ../.tmp/demo

# 血缘影响：用 fixture 跑通
cd ../model-lineage-impact
python scripts/lineage_impact.py --task 1001 --depth 1 \
  --field scr_cn_full_name --names \
  --adapter-config ../adapter-configs/example.mock.json --out ../.tmp/lineage
```

接入真实平台时，复制一份配置并设置 `DATADEV_API_TOKEN`，先自检：

```bash
python -m datadev_adapters doctor --config my.json --table your_table
```

## 安装到 harness

订阅一次，之后靠 git 持续更新。把 `<your-org>` 换成实际托管仓库的 GitHub 账号。

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

| `--harness` | 技能目录 |
|---|---|
| `agents` | `~/.agents/skills`（Agent Skills 通用格式） |
| `claude` | `~/.claude/skills` |
| `codex` | `~/.codex/skills` |
| `cursor` | `~/.cursor/skills` |
| `workbuddy` | `~/.codebuddy/skills` |
| `zcode` | `~/.zcode/skills` |
| 其他 | `--dest <目录>` |

装好后**开新对话**。各 harness 细节见 [docs/harnesses.md](./docs/harnesses.md)。

### 更新 / 管理

```bash
python ~/.data-dev-skills/repo/scripts/subscribe.py --sync
python ~/.data-dev-skills/repo/scripts/subscribe.py --register-task   # 每日 09:30
python ~/.data-dev-skills/repo/scripts/subscribe.py --status
python ~/.data-dev-skills/repo/scripts/subscribe.py --uninstall
```

或直接在对话里说：**"更新 data-dev 技能"**。

更新会保留用户自己的 `.env` 与 `output/` 目录；缺配环境变量时按技能逐项提示，并给出
`setup_env.py --set` 写入命令（值不回显）。

### 一句话安装

把下面这句发给有终端能力的 agent（替换 `<your-org>`）：

```
Read https://raw.githubusercontent.com/<your-org>/data-dev-skills/main/docs/install.md and install data-dev skills for my harness.
```

## 环境变量

| 技能 | 必填 | 可选 |
|---|---|---|
| `master-data-modeling` | `MD_OPERATOR`、`MYSQL_HOST/USER/PASS/DB`、`PANEL_BASE_URL`、`TDAI_USER_KEY`、`KS_BASE_URL` | `MYSQL_PORT`、`TDAI_SERVICE_ID`、`WIKI_RULES_*`、`DDM_*` |
| `master-data-source-mapping` | `DATADEV_API_TOKEN` | `DATADEV_ADAPTER_CONFIG`、`DM_LAYER_MODEL_PREFIX`、`DM_LAYER_SOURCE_PREFIX`、`SOURCE_PRIORITY` |
| `model-lineage-impact` | `DATADEV_API_TOKEN` | `DATADEV_ADAPTER_CONFIG` |

"是否必填 / 谁提供 / 怎么拿"的权威来源是 `marketplace.json` 的 `skills[].env`。
完整说明见 [docs/install.md](./docs/install.md#environment-variables)。

## 使用方式

直接用自然语言提需求，无需记命令：

```
把来源表 ods_scr_base_info 按 MPC 规范重构，输出新的主数据模型
把 dm_demo_security 反查回来源表，输出字段映射 TSV
哪些下游任务读了 dm_demo_security.scr_cn_full_name？给出影响矩阵
```

每个技能的 `SKILL.md` 写明执行工作流、硬性门控与 review 清单；README 解释概念与脚本用法。

## 仓库结构

```
data-dev-skills/
├── README.md / README.zh-CN.md     # 中英双语说明
├── AGENTS.md / CLAUDE.md           # agent 根指令（开工流程 / 规则 / 收尾检查）
├── init.sh                         # 自举：环境检查 → 装依赖 → 验证
├── progress.md                     # 会话级进度日志
├── feature_list.json               # 机器可读功能清单
├── CHANGELOG.md                    # 版本化变更记录
├── marketplace.json                # 市场索引：版本 + 环境变量契约 + baseline
├── packages/datadev_adapters/      # adapter 单一源码（六操作）
├── adapter-configs/                # mcp / rest / mock 示例配置
├── scripts/
│   ├── subscribe.py                # 订阅 / 同步 / 状态 / 卸载 / 计划任务
│   ├── setup_env.py                # 按技能写 .env（值不回显）
│   ├── sync_adapters.py            # adapter 镜像到各技能；--check 防漂移
│   ├── lint_marketplace.py         # UNLISTED / DEAD / DRIFT 检测 + --fix
│   └── package_zips.py             # WorkBuddy ZIP 打包
├── docs/
│   ├── install.md                  # 安装、环境变量、故障排查
│   ├── adapter.md                  # adapter 契约与配置参考
│   ├── harnesses.md                # 各 harness 技能目录
│   └── workbuddy.md                # WorkBuddy URL 导入 / ZIP 上传
├── tests/                          # adapter 契约 / 端到端 / 订阅 / 市场一致性
└── master-data-*/ , model-lineage-impact/   # 每个技能一个自包含目录
```

## 维护者指南

本仓库沿用 [harness engineering 模板](https://walkinglabs.github.io/learn-harness-engineering/zh/resources/templates/)：

```bash
./init.sh        # 自举 + 验证
python scripts/lint_marketplace.py --check
python scripts/sync_adapters.py --check
python -m pytest tests/ -q
```

规则以 [AGENTS.md](./AGENTS.md) 为准，要点：

- 改技能内容必须 bump `marketplace.json` 版本 + 记 CHANGELOG；
- adapter 只在 `packages/datadev_adapters/` 改，改完跑 `scripts/sync_adapters.py`；
- `.env`、令牌、Cookie、`output/`、`dist/` 永不入库；
- 订阅演练必须用隔离的 `--prefix` / `--dest`，禁止写真实用户目录。

## 安全说明

- 仓库不含任何凭据、内部主机名与真实业务表；
- 演示 fixture 全部为合成数据（`VENDOR_A`、`VENDOR_B`、任务号 `1001`/`2001`）；
- 令牌只从环境变量/`.env` 读取，以 `${token}` 注入；仓库内配置只有占位符。

## License

MIT，见 [LICENSE](./LICENSE)。
