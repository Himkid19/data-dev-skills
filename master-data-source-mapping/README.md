# master-data-source-mapping · 主数据来源字段映射

输入一个主数据表名（dm_mast 层），自动梳理出 **主数据 ← 模型 ← 源表** 的字段级映射关系，
产出两份可直接粘贴远程表格的 TSV。元数据/血缘/调度接口通过 adapter 接入，可换成任意服务商。

## 使用方法

```bash
# 1. 配置适配器与令牌（一次性）
cp .env.example .env                     # DATADEV_API_TOKEN / DATADEV_ADAPTER_CONFIG
# 或直接指定配置（仓库根 adapter-configs/ 下的示例；安装后换成自己的配置绝对路径）
# adapter-configs/example.mcp.json / example.rest.json / example.mock.json

# 2. 采集（已知主数据装载任务 ID 时用 --dm-task 省去下游扫描）
python scripts/collect_mapping.py dm_demo_security --dm-task 1003 \
    --adapter-config ../adapter-configs/example.mock.json \
    --out output/dm_demo_security_demo

# 3. 生成 TSV
python scripts/build_tsv.py output/dm_demo_security_demo/mapping.json
```

不接真实服务也能先跑通：`../adapter-configs/example.mock.json` 使用仓库内的脱敏 fixtures
（`packages/datadev_adapters/fixtures/demo/`），端到端产出与真实链路一致。

agent 复核结论写在 `output/<表名>_<日期>/review.json` 后重跑第 3 步即可叠加进产物
（未匹配字段的"疑似映射/技术字段"裁决，详见 SKILL.md Step 3）。

## 产出示例（mock fixtures：4 字段 / 2 个来源）

**1_source_tables.tsv · 接入源表清单**

| 来源缩写 | 来源表英文名 | 来源表中文名 | 匹配情况 |
|---|---|---|---|
| A | security_master | 来源 A 证券主表（虚构数据） | 覆盖4个主数据字段 |
| B | security_basic | 来源 B 证券基础表（虚构数据） | 覆盖4个主数据字段 |

**2_field_mapping.tsv · 详细字段映射（大宽表，来源列全局对齐，来源1..N 各 5 列）**

| 字段英文名 | 匹配情况 | 来源1(A)表名称 | 来源1表字段英文名称 | 来源1表字段加工逻辑 |
|---|---|---|---|---|
| scr_cd | 单源直传 | odata_vendor_a.security_master | security_code | （直传留空） |
| scr_cn_full_name | 多源判断(仅加工逻辑) | odata_vendor_a.security_master | security_name | （直传留空） |
| mkt_cd | 单源直传 | odata_vendor_a.security_master | market_code | （直传留空） |
| list_date | 单源直传 | odata_vendor_a.security_master | list_date | （直传留空） |

## 工作原理

```
adapter.search_table ──► 主数据表 guid
adapter.get_table    ──► dm 字段清单（中文名/码值集）
adapter.get_lineage  ──► 上游模型表（模型层前缀）
  └─ 每个模型：get_table → 任务列表；get_lineage → 来源层源表注册
schedule（锚点）：
  主数据装载任务 <表>_grpNN（--dm-task 指定 / 下游任务名扫描自动发现）
  └─ get_task_dependencies(UPSTREAM) ──► 权威模型任务
  └─ get_task_sql ──► 权威任务 ETL SQL：模型字段 ← 来源字段
  └─ 装载任务 SQL ──► dm 层加工逻辑链（临时表下钻 ≤3 层）+ 多源判断/常量标记
```

关键口径与边界（务必了解）：

- **权威来源 = 主数据装载任务的一级上游**；模型表其它任务未接入装载，不计入来源，
  其覆盖的字段会标"未匹配(需人工)"；
- **来源列全局对齐**：来源1/2/3 在所有行固定为同一来源缩写（顺序由 `SOURCE_PRIORITY` 决定，
  未配置则按字母序）；每个字段 × 每个来源恰好一组，某来源无该字段映射时该组加工逻辑填"空值"；
  来源x表名称只放一张来源层源表；
- **来源x表字段只放真正来自源表的字段**：码值转换（join 码值维表）取源表关联键字段、
  加工逻辑填 join 子句；值来自模型表的字段留空、加工逻辑填 join 子句；纯直传加工逻辑留空；
- **一个来源一个值源**：来源字段优先取该来源模型任务里 `select 别名 = 主数据字段` 的项，
  加工逻辑填该项对源表字段的转换；无同名项时才回退 dm 表达式链推断的候选别名——
  dm 层 CASE 的判断输入列不算来源字段；
- **多源判断字段**（dm 层 src_id 分支/优先级合并）照常给各来源分组，
  匹配情况标"多源判断(仅加工逻辑)"；各来源全部空值判"未匹配(需人工)"；
- 模型表间 join、码值转换 join 不算来源表，只体现在加工逻辑里；
- Hive INSERT 按位置写入，dm 字段逻辑按列序与 select 项对齐；
- 子查询内层别名不外泄（作用域隔离），嵌套子查询的临时别名不会覆盖外层同名别名；
- 服务端有限流时脚本内置退避重试 + 磁盘缓存（`cache/`），重跑不重复请求。
