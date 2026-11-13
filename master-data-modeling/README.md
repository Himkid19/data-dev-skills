# master-data-modeling · 金融主数据自动建模 Skill

> 让 agent 扮演**主数据建模工程师**：输入源表结构，按 MPC 规范（ISO/IEC 11179 方法论 + 词根平台）自动完成
> 字段命名、码值对齐、多源整合与 review 交付。词根翻译、缓存复用、码值对齐等**确定性工作全部交给脚本**；
> 概念归一、复用/新增裁决、多源口径沟通等**语义判断由 agent 完成并说明理由**。

| | |
|---|---|
| 触发场景 | "主数据建模、主数据改造、dm_mast 层、主数据字段设计、MPC 规范、码值对齐、多源字段整合、新源接入主数据、字段词根翻译" |
| 运行依赖 | Python 3.8+、MySQL（共享资产库）、词根平台（同步用）、知识库 KS/Panel（查证与沉淀用） |
| 上游更新 | 词根字典由治理团队维护；**用户主动提同步时才同步**（不主动询问打断流程） |

## 1. 职能

| 职能 | 说明 | 入口 |
|---|---|---|
| **新增主数据模型** | 源表 → MPC 逐词转义（团队约定）→ 概念归一 → 词根翻译 → 映射（团队约定）→ 四页交付物 | 三步流程 |
| **新源接入已有模型** | 新源字段逐个与主数据对齐：复用 / 新增 / 不迁入 三分类，口径差异交用户拍板 | 三步流程 + [task2 §4](./references/task2-mapping.md) |
| **词根维护** | 词根平台同步、英文术语回查、缺口确认与固定搭配登记 | 第 1 步 |
| **码值管理** | 标准码域查询、多源码值对齐与校验、缺口报告 | 第 2 步 |
| **质量闸门** | review 清单全绿才准交付，交付前缓存反查防撞名 | 第 3 步 |
| **知识沉淀** | 规则缺口提案入库、文档积累、词根使用审计 | 第 3 步 |

## 2. 使用方法

### 部署准备（首次安装必做）

```bash
cd <skills目录>/master-data-modeling

# 1. 安装依赖（pymysql 必需；参考案例已改为读 md_field_cache，不再需要 openpyxl）
pip install -r requirements.txt

# 2. 配置环境变量：MySQL 连接、词根平台 Cookie、知识库端点与 key
cp .env.example .env   # 按注释填写；.env 含 key 禁止提交

# 3. 建表 + 引导（scripts/schema.sql 含全部共享表与存量迁移说明）
python scripts/roots.py sync --apply       # 从词根平台首次引导词根（唯一数据源，需 DDM_*）
python scripts/roots.py cache-import-seed  # 翻译缓存种子（缺失时自动用 .example 示例）
#    码值种子导入见 code_value.py 帮助
```

### 典型指令（装好后直接说人话）

| 你说 | skill 做 |
|---|---|
| `把 VENDOR_A 源的 demo_security_src 表按 MPC 规范重构，输出新的主数据模型` | 三步流程：先出 MPC 转义表等你确认 → 再出映射表等你确认 → 最后写缓存与 review |
| `把 VENDOR_B 的 xxx 表接入已有的 dm_demo_security 主数据模型` | 同上三步 + 迭代三分类：复用/新增/不迁入 + 多源整合表 |
| `同步一下词根` | `roots.py sync` dry-run 看差异 → `--apply` 入库 |
| `"上市状态"枚举各源不一致，做码值对齐` | `code_value.py align` 建映射 + validate 校验 |
| `所得税/利润总额(%) 这种占比字段帮我翻译` | 按比值字段规则归一 → `a_to_b`（见 references/mpc-naming-rules.md） |
| `PE 不在白名单，帮我加` | `whitelist.py add` 登记后可用 |

### 交付物

第 1 步：`转义预览.tsv`（MPC 逐词转义）+ `归一决策.tsv`（含团队约定留痕列）；第 2 步：四页交付物
（目录 / 基础词根圈定 / 字段词根表含决策备注 / 逐字段映射表）+ `roots_used.tsv` + `cache_input.tsv`；
第 3 步：`review_report.md` + 缓存写回（含词根使用明细）。全部落盘 `output/<模型名>_<日期>/`。

## 3. 工作原理（自动建模怎么实现）

**核心思想：agent 做语义判断，脚本做确定性执行，MySQL 共享资产保证多用户"同义字段同名"。**

```
用户需求（自然语言）
   │ 第 1 步 · 输入收集（三件套）+ 规范查证 + MPC 逐词转义预览  ⛔ 停下交团队约定
   ▼
┌─ Agent（语义判断）────────────────┐    ┌─ 脚本（确定性执行）─────────────────┐
│ · 概念归一：源字段 → 标准对象类+特性 │    │ roots.py     词根翻译 / 缓存 / 使用明细 │
│   （债券名称 → 证券的"名称"特性）    │◄──►│ code_value.py 码域查询 / 多源对齐 / 校验 │
│ · 复用 / 新增 / 不迁入 三分类裁决    │    │ whitelist.py 金融缩写白名单维护          │
│ · 多源口径冲突整理成表，交用户拍板   │    │ model_builder.py 预览/映射/自校验/交付物 │
└──────────────────────────────┘    └──────────────┬──────────────────────┘
   │ 第 2 步 · 映射落盘（归一→缓存命中→词根拼接）   ⛔ 停下交团队约定映射表
   │   闸门在代码里：confirmed 未盖章 / 无 --norm → 脚本拒绝落盘
   │ 第 3 步 · 团队约定后写缓存 + review 清单全绿才准交付
   ▼
四页交付物 + review_report        MySQL 共享资产（词根/缓存/码值/使用明细）
```

**翻译优先级链**（`roots.py translate` 自动执行，同义字段复用同一译名）：

```
固定搭配（fixed_collocations.csv，最高）→ 金融缩写白名单 → 翻译缓存（md_field_cache，
按 cache_type 优先级：scr_base 证券基础 > stk/bnd/idx/fund_base 子类基础 > common > other）
→ 词根拼接（md_word_roots）→ 中文 miss 时英文术语回查 → 仍无则团队约定
```

**MySQL 共享资产**（多用户共用，仓库内同名 CSV 仅为首次引导种子）：

| 表 | 作用 |
|---|---|
| `md_word_roots` | 词根字典，**唯一数据源**（上游词根平台，`sync` 同步；不落本地文件） |
| `md_field_cache` | 已确认字段翻译缓存，含 cache_type 优先级；命中必复用 |
| `md_root_usage` | 词根使用明细：一行一个词根段，`root_id` 关联词根字典 id，支撑按词根聚合审计 |
| `md_code_values` / `md_code_value_mappings` | 标准码值 / 源码值→标准值映射 |

**硬性门控**（违反即重做，清单见 [SKILL.md](./SKILL.md)）：词根不臆造、缓存命中必复用、先归一再翻译、
是否类统一 `xx_flag`、**字段名必须以类词收尾**、字段 ≤5 词根段
（超限用户审批）、缩写白名单制、码值不过脑、review 是闸门、知识库不直写。

## 4. 字段设计规范

**规范全文唯一出处 = [references/mpc-naming-rules.md](./references/mpc-naming-rules.md)**：中文备注三段式、
后缀惯例（`_cd/_date/_time/_flag/_desc/_cnt/_qty/_prop`、率类 `rate/intrt/yield`、`pric/par_val/bal/ulmt`）、
语言限定 `cn/en` 前置、拼音简称 `scr_abbr_pinyin_name`、序数前缀 `fir/main/r/ori/sh/sz`、比值字段
`a_to_b`、标准字段清单、存量兼容（yearsnum）等。README 不再复制规范正文，避免口径漂移。

两条最易错：

- **类词收尾**：英文名必须以 MPC 类词收尾——序号→`seq_no`、编号/流水号→`no`、是否→`flag`、代码→`cd`、
  日期→`date`、名称→`name`…类词表 = [references/mpc_classifiers.csv](./references/mpc_classifiers.csv)；
  `model_builder.py` 已强制校验，规范特例在归一决策 TSV 的 note 写「类词豁免：<理由>」放行。
- **标准字段**：`busi_date / rec_no / scr_id / src_id / grp_id / remark / rec_upd_time / rec_down_time`；
  无证券维度的参照表（市场/日历/参数）用 `--no-sec-id` 豁免 `scr_id`。

## 5. 词根维护

- **权威数据源**：MySQL `md_word_roots`（由词根平台维护）；用户主动提同步时跑 `python scripts/roots.py sync` dry-run 看差异 → `--apply` 入库
- **查询**：`roots.py lookup <中文词>`，匹配级别 精确 → 包含 → 模糊 → **英文术语回查**（en-match：中文 miss 时翻成英文术语按词根库全称匹配，如 永续→Perpetual→perp、递进→Progressive→进度 prog）；中文候选不达意时优先看英文回查候选区
- **缺口处理**：近义词 lookup → 英文回查 → 仍无则与团队约定口径 → 登记进 `fixed_collocations.csv`（固定搭配，最高优先级）或 `fin_abbr_whitelist.csv`（缩写白名单），或作为词根平台的新词根提案
- **使用审计**：字段确认入缓存时自动反解写入 `md_root_usage`（一行一个词根段），可按词根聚合统计：

  ```sql
  -- 某张表用了哪些词根
  SELECT w.abbr, w.root_zh, COUNT(*) FROM md_root_usage u
  JOIN md_word_roots w ON w.id = u.root_id
  WHERE u.model_name = 'demo_base_info' GROUP BY u.root_id ORDER BY 3 DESC;

  -- 从未被使用的词根（治理清理依据）
  SELECT w.* FROM md_word_roots w LEFT JOIN md_root_usage u ON u.root_id = w.id WHERE u.id IS NULL;
  ```

- 存量回填：`python scripts/roots.py usage-sync-cache [--model M1,M2]`

## 6. 码值管理

- 枚举字段一律走 `scripts/code_value.py`，禁止现场编造标准值或硬映射（"码值不过脑"门控）
- **有码域**：`lookup <码域>` 查标准值 → 逐源 `align` 建映射 → `validate` 校验，映射进交付物
- **无码域**：报告用户"该码域未建立，建议先定标准值"→ 团队约定的标准值提案走第 3 步知识沉淀入库 `md_code_values`，源映射落 `md_code_value_mappings`（多源冲突如 `A源 1=未上市 3=上市` vs `B源 0=未上市 1=上市` 必须对齐成统一标准值）
- 码值差异表进迭代场景的多源整合沟通（task2 §4），未确认字段标"待确认"不进最终映射

## 7. 运作流程（三步，每步一个 STOP 门）

闸门做在代码里（`model_builder.py`）：第 2 步不给 `--norm`、或 `归一决策.tsv` 的 `confirmed` 列没有用户
确认盖章 → 直接拒绝落盘。用户没确认前不写缓存。

| 步 | 做什么 | 命令 | 产出 | 门 |
|---|---|---|---|---|
| 1 转义 | 输入三件套 + 规范查证 + MPC 逐词转义（标准字段除外）+ agent 语义复核 | `model_builder.py --fields … --preview` → `--mark-confirmed` | `转义预览.tsv`、`归一决策.tsv` | 中文字段名交**团队约定** |
| 2 映射 | 归一 → 缓存命中 → 词根拼接 → 码值对齐 | `model_builder.py --fields … --norm …` | 四页交付物 + `roots_used.tsv` + `cache_input.tsv` | 映射表交**团队约定** |
| 3 写回 | 团队约定后写缓存（含词根使用明细）+ review 清单 + 规则缺口提案 | `roots.py cache-add-batch` + `review-checklist.md` | `review_report.md`、缓存/码值/词根明细 | review 全绿才交付 |

迭代场景（新源接入已有模型）在第 1 步做三分类（复用/新增/不迁入）、第 2 步做多源整合表交用户拍板，
细节见 [references/task2-mapping.md](./references/task2-mapping.md) §4。

## 8. 目录结构

```
master-data-modeling/
├── SKILL.md                   # agent 工作流定义（三步流程 + STOP 门 + 硬性门控 + 资源路由，≤6KB）
├── README.md                  # 本文件
├── .env.example / .env        # 环境变量模板（MySQL / 词根平台 / 知识库；.env 禁止提交）
├── requirements.txt           # pymysql 必需，openpyxl 可选
├── references/
│   ├── task1-mpc-escape.md    # 第 1 步细则：输入收集/查证/概念归一/MPC 逐词转义
│   ├── task2-mapping.md       # 第 2 步细则：翻译链路/缓存优先级/码值/迭代三分类
│   ├── task3-writeback.md     # 第 3 步细则：写缓存/review 闸门/知识沉淀
│   ├── mpc-naming-rules.md    # MPC/11179 命名规则执行手册（字段设计规范全文）
│   ├── mpc_classifiers.csv    # MPC 类词表（中文类词→英文+建议字段类型；类词收尾校验的数据源）
│   ├── concept_mapping.md     # 概念归一对照表（含比值字段 a_to_b 规则 §5）
│   ├── fixed_collocations.csv # 固定搭配表（最高优先级，脚本自动加载；团队本地维护）
│   ├── fin_abbr_whitelist.csv # 金融缩写白名单（whitelist.py 维护）
│   ├── review-checklist.md    # review 全清单（第 3 步闸门）
│   ├── kb-writeback.md        # 知识分层、提案、防脏数据
│   ├── reference-case.example.md # 案例口径解读（示例；权威案例 = md_field_cache）
│   └── code_values*.example.csv  # 码值/映射首次引导种子示例（正式数据源 = MySQL）
├── scripts/
│   ├── CLI.md                 # ⭐ 命令契约（agent 只查这里，禁止读脚本源码）
│   ├── model_builder.py       # 闭环生成器：--preview 转义闸门 / --norm 映射 / --mark-confirmed 盖章
│   ├── test_model_builder.py  # 纯逻辑自检（不连库）：python scripts/test_model_builder.py
│   ├── roots.py               # 词根 sync/lookup/translate/缓存/使用明细全链路
│   ├── code_value.py          # 码域查询/多源对齐/校验
│   ├── whitelist.py           # 缩写白名单 CRUD
│   ├── ddm_client.py          # 词根平台客户端（DDM_* 配置）
│   ├── field_cache.example.csv # 翻译缓存首次引导种子示例（正式数据源 = MySQL）
│   ├── schema.sql             # 共享表建表 + 存量迁移（v3 词根使用明细）
│   └── db.py                  # MySQL 连接（读 .env）
└── assets/                    # 固定物料（MPC 术语解释）
```

## ⚠️ 注意事项

- `.env` 含用户 key 与词根平台 Cookie，禁止提交；多人共享同一套 MySQL 资产，缓存命中必复用
- 词根/缓存/码值的正式数据源都在 MySQL，仓库内 CSV 仅为首次引导种子；运行期产物（output/、.cache/）不入库
- 对共享表的写入（cache-add、码值入库）都带操作人留痕（`MD_OPERATOR`），冲突需人工裁决
