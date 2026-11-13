---
name: master-data-source-mapping
description: 主数据来源字段映射梳理 skill：输入主数据表名（dm_mast 层），沿 主数据→模型→来源 链路自动采集元数据/血缘/调度 ETL SQL，解析出 主数据←模型←源表 的字段级映射，输出两份 TSV（接入源表清单 + 详细字段映射大宽表）。当用户提到"主数据来源映射、字段来源梳理、来源表清单、接入源表、dm_mast 字段从哪来、主数据血缘、模型字段映射、元数据 MCP"等请求时使用。
---

# Master Data Source Mapping · 主数据来源字段映射

> 你正在扮演**主数据来源梳理工程师**。采集、SQL 解析、TSV 生成这些确定性工作**必须交给
> scripts/ 下的脚本**；只有机器搞不定的地方才由你介入：未匹配字段的语义裁决（疑似映射/
> 技术字段）、SQL 解析异常的补救、匹配情况的最终把关。

## 分层与链路（领域背景）

数据分层：**主数据 `dm_mast*` → 模型 `pdata_*` → 源表 `odata_*_<源缩写>`**。
三层前缀都能由环境变量调整（`DM_LAYER_MODEL_PREFIX` 默认 `pdata`、
`DM_LAYER_SOURCE_PREFIX` 默认 `odata`）。

- 源缩写来自 odata 库名后缀或调度任务名后缀：`VENDOR_A`、`VENDOR_B`、`VENDOR_C` 等；
  一个主数据表通常由多个模型表（如 `pdata_order_base` 主表 + `pdata_order_base_ext` 扩展表）拼合。
- 每个模型表按来源系统拆多个调度任务（任务名 `<模型>_<SRC>`），每任务
  `INSERT OVERWRITE ... partition(src_id='<SRC>')` 写入自己来源的分区。
- **权威链路以主数据装载任务为锚点**：任务名形如 `<主数据表>_grpNN`（grp_id 并行分区），
  它的**一级上游调度任务**才是主数据真正依赖的模型任务。模型表里其余任务虽也写模型表分区，
  但未接入该主数据装载，**不得计入来源**。
- 主数据装载 SQL（临时表 + 最终 INSERT）承载 dm 层加工：拓展表融合（ext join）、按 src_id 的
  业务统一转换、按来源优先级的取值插补。表达式链命中这些特征的字段 = **多源判断字段**。

## 数据接口（adapter，可替换成你的元数据服务）

本 skill 不绑定任何具体厂商接口，全部元数据/血缘/调度/SQL 请求走
`scripts/_shared/datadev_adapters`，用一份 JSON 配置声明端点与字段映射：

```bash
export DATADEV_API_TOKEN=<你的令牌>
python scripts/collect_mapping.py dm_demo_security --adapter-config ../adapter-configs/example.mcp.json
```

- 支持 `jsonrpc-mcp`（默认，JSON-RPC over HTTP/SSE）、`rest`（普通 HTTP + JSON 路径提取）、
  `mock`（离线 fixtures，用于演练与测试）三种 provider。
- 六个逻辑操作：`search_table` / `get_table` / `get_lineage` / `get_task_detail` /
  `get_task_dependencies` / `get_task_sql`，见 [`docs/adapter.md`](../docs/adapter.md)。
- 令牌只经 `token_env`（默认 `DATADEV_API_TOKEN`）注入，**禁止把 token 写进代码或文档**。
- 客户端已内置限流退避 + 磁盘缓存，重跑不重复请求。

## 工作流

### Step 1 采集（脚本）

```bash
python scripts/collect_mapping.py <主数据表名> [--dm-task 装载任务ID] [--out DIR] [--adapter-config FILE]
```

脚本自动完成：表搜索 → 主数据字段/码值集 → 血缘找上游模型表 → 每个模型的任务列表
+ 上游源表（中文名/字段注释）→ **主数据装载任务发现**
（`--dm-task` 可直接指定；缺省扫描模型任务下游按表名匹配，调用较多但走缓存）
→ 装载任务一级上游 = 权威模型任务 → 仅对权威任务拉 ETL SQL 并解析
（`表达式 as 字段`，按括号深度切分、任意嵌套子查询别名解析且内层别名不外泄）
→ 解析装载任务 SQL 提取 dm 层加工逻辑链 + 多源判断标记（Hive INSERT 按位置对齐字段）。
产出 `<out>/mapping.json`（结构化映射 + cache/ 原始响应）。
缺表/限流/解析失败会打印在 stderr 与 mapping.json 的 errors 中——**有 errors 必须处理再继续**。
已知主数据装载任务 ID 时优先 `--dm-task`，省去下游扫描。
**注意默认 out 目录带当天日期，重跑建议显式 `--out` 指向同一目录复用缓存。**

### Step 2 生成 TSV（脚本）

```bash
python scripts/build_tsv.py <out>/mapping.json
```

- `1_source_tables.tsv`：来源缩写 / 来源表英文名 / 来源表中文名 / 匹配情况（覆盖字段数）
- `2_field_mapping.tsv`：主数据字段大宽表，来源1..N 各 5 列
  （来源x / 来源x表名称 / 来源x表字段中文名称 / 来源x表字段英文名称 / 来源x表字段加工逻辑）

TSV 均 UTF-8 BOM，可直接复制粘贴到远程表格。口径：

- **来源列全局对齐**：来源1/2/3 在所有行固定为同一来源缩写，顺序由环境变量
  `SOURCE_PRIORITY`（逗号分隔）决定，未列出的按字母序；每个主数据字段 × 每个来源缩写
  恰好一组，某来源没有该字段映射时该组表名称填该来源的主源表、加工逻辑填「空值」。
- 来源x表名称只放**一张 odata 源表**——表达式引用的源表，
  无引用时（常量/码值转换）用该任务主表。
- **来源x表字段只放真正来自源表的字段**：直传/表达式取表达式引用的源表字段；
  码值转换（值来自码值维表）取与码值维表 left join 的**源表关联键字段**，
  加工逻辑填该 join 子句；值来自模型表同样只填 join 子句、字段留空——非源表字段不进来源列。
- **一个来源一个值源**：来源字段优先取该来源模型任务里 `select 别名 = 该主数据字段` 的项，
  加工逻辑填该项对源表字段的转换；只有该来源无同名项时才回退用 dm 表达式链推断的候选别名。
- **dm 层 CASE 的判断输入列不得混入来源分组**：判断条件用到的列不是该字段的来源字段。
- **加工逻辑只写非平凡情形**：纯直传留空；`常量` / `空值` / 表达式原文 / join 子句。
- **多源判断字段**（dm 层 src_id 分支/优先级合并）照常给出各来源分组，
  匹配情况标「多源判断(仅加工逻辑)」。
- 匹配情况：`多源整合 / 单源直传 / 单源表达式 / 多源判断(仅加工逻辑) / 常量填充 /
  未匹配(需人工)`；各来源全部空值的字段判「未匹配」；码值字段列：
  `是(<码值集编号>)` 来自元数据 codedset，`疑似` 仅为 `_cd/_flag` 后缀启发式。

### Step 3 你的复核（agent 介入点）

1. **未匹配字段逐一裁决**：先查 mapping.json 的 dmFieldLogic（dm 层可能已加工），
   再查模型层 items 里相似字段名与中文注释，判定"疑似映射 xxx"还是"技术字段"；
   结论写进 `<out>/review.json` 并重跑 build_tsv.py。**禁止替用户拍板改名**——疑似就是疑似。
2. **抽查解析质量**：随机抽 2~3 个字段，对照 mapping.json 里 `statements[].sql` 原文
   核对来源表/字段/加工逻辑；解析错的（如子查询别名没解析到）修 collect_mapping.py 的
   解析规则而不是手改 TSV。
3. **向用户说明边界**：权威来源 = 装载任务上游；未匹配字段多为未接入装载的分区来源所填，
   已在匹配情况标注；模型表间 join 在加工逻辑中以 join 子句体现，非直连 odata。

## 已知边界

- 主数据表查不到时不猜库名，先用精确匹配的表搜索。
- 极端嵌套子查询/多段 UNION 的 ETL SQL 解析可能不完整：以 errors 和抽查为准，
  解析不了的字段标"未匹配(需人工)"并附任务号，不要静默丢弃。
- 子查询内层别名不外泄（作用域隔离）：内层别名不会覆盖外层同名别名。
- 码值转换类 join 不作为来源表，只体现在加工逻辑里。
- Hive INSERT 按位置写入：dm 字段逻辑按主数据列序与 select 项对齐，select 别名可能
  与真实列名不同，以位置对齐结果为准。

## 交付物清单

```
output/<表名>_<日期>/
├── mapping.json      # 结构化全量映射（中间产物，供核对与重算）
├── review.json       # agent 复核结论（可选）
├── 1_source_tables.tsv
└── 2_field_mapping.tsv
```
