# model-lineage-impact · 模型依赖血缘影响排查

输入一个**调度号**（调度任务 ID）或**模型表名**，查出下游依赖任务（默认 1 层），
逐条解析下游 ETL SQL 的表/字段引用与 `src_id` 分区，回答"改这张表/这个字段会波及谁"，
产出三份可直接粘贴远程表格的 TSV。

## 使用方法

```bash
# 1. 配置适配器与令牌（一次性）
cp .env.example .env        # DATADEV_API_TOKEN / DATADEV_ADAPTER_CONFIG

# 2. 排查（调度号）
python scripts/lineage_impact.py --task 1001 --depth 1 --field scr_cn_full_name \
    --adapter-config ../adapter-configs/example.mock.json

# 或按模型表名排查；同一 --out 目录重跑复用缓存，不再刷接口
python scripts/lineage_impact.py --table dm_demo_security --depth 1 \
    --field mkt_cd --src-id VENDOR_A --out output/dm_demo_security_demo \
    --adapter-config ../adapter-configs/example.mock.json
```

不接真实服务也能先跑通：`../adapter-configs/example.mock.json` 使用仓库内脱敏 fixtures，
端到端产出与真实链路一致。支持 `jsonrpc-mcp` / `rest` / `mock` 三种接口形态，
见 [`docs/adapter.md`](../docs/adapter.md)。

## 产出

```
output/<task-ID|表名>_<日期>/
├── 0_scope.tsv              影响范围小结（下游任务数 / 涉及字段数 / 涉及下游表(模型·主题表)数）
├── 1_downstream_tasks.tsv   下游任务清单（表级：是否引用目标表 / 命中的目标字段 / 分区 / 结论）
├── 2_field_impact.tsv       字段级影响矩阵（含"下游引用了但目标表没有"的字段）
├── 3_reference_detail.tsv   引用明细（证据 SQL 片段，一条一行）
├── impact.json              结构化全量结果
└── cache/                   适配器原始响应缓存（重跑不重复请求）
```

脚本 stdout 会先给一行**影响范围小结**（下游任务数 / 引用目标表数 / 命中本次字段数 / 疑似数 /
涉及字段数 / 涉及下游表(模型·主题表)数），再给字段级结论——细节看 TSV 即可。

## 置信度口径

| 标签 | 含义 |
|---|---|
| 限定 | 别名直接取自目标表（或 `select *` 透传子查询）的 `别名.列` 引用 —— 真读该字段 |
| 疑似 | 裸列名（该块内确实读了目标表）/ 子查询加工出来的列 / 别名冲突 —— 需人工确认 |
| 同名 | 下游自己产出了同名字段（如 `mkt_ch_name as mkt_cn_name`）—— 不是对目标表该字段的引用 |

## 参数

| 参数 | 说明 |
|---|---|
| `--task` / `--table` | 二选一：调度任务 ID 或模型表名 |
| `--depth` | 下游层数，默认 1（只查直接下游） |
| `--field` | 要排查的字段，可重复；缺省扫描目标表全部字段 |
| `--src-id` | 关注的分区，`auto`（默认）从锚点任务 `PARTITION(src_id='XX')` 推断 |
| `--max-tasks` | 下游任务数上限，默认 120，超过即停止（防大量调用） |
| `--names` | 补下游任务名（每任务 +1 次调用，默认关闭） |
| `--no-meta` | 不查元数据，字段清单退化为锚点任务 DDL |
| `--out` | 输出目录（重跑复用它以命中缓存） |
| `--adapter-config` | 适配器 JSON 配置（也可用 `DATADEV_ADAPTER_CONFIG`） |

## 工作原理

```
--task 1001 ──► adapter.get_task_detail（任务名）
             └─► adapter.get_task_sql（锚点 SQL：写出表 / src_id 分区 / DDL 字段）
--table T ──► adapter.search_table → get_table（字段清单 + 任务锚点）
              ▼
adapter.get_task_dependencies(DOWNSTREAM, step=N) ──► 下游任务 ID
              ▼
每个下游任务：adapter.get_task_sql ──► ETL SQL
              ▼
按 select 块解析：from/join 目标表 → 别名绑定；别名.列 → 限定；
裸列名（块内读了目标表）→ 疑似；输出别名同名 → 同名；src_id='XX' → 分区上下文
              ▼
1_downstream_tasks.tsv / 2_field_impact.tsv / 3_reference_detail.tsv
```

## 校验

```bash
python -m pytest tests/test_lineage_impact.py -q
```

覆盖：锚点写出表/分区推断、限定命中、裸列疑似、跨表同名列不误报、子查询别名不外泄与
加工列降级、where 片段不越界、TSV 落盘格式。
