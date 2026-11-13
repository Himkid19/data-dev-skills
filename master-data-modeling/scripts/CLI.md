# 脚本命令契约（agent 只查这里，禁止读脚本源码）

> 每个命令 = 一次进程启动 + 一次 MySQL 连接。**批量调用**：一条命令里用空格传多个词/域，
> 禁止一个词起一次进程。所有命令都在 skill 根目录下执行：`python scripts/xxx.py ...`

## model_builder.py · 通用闭环生成器（第 1/2 步主力，禁止再手写一次性脚本）

| 场景 | 命令 |
|---|---|
| 第 1 步 · MPC 转义预览（必跑，产出 `转义预览.tsv` + `归一决策.tsv` 种子） | `python scripts/model_builder.py --fields <字段清单.tsv> --tbl-ch <中文表名> --tbl-en <英文表名> --out <输出目录> --preview` |
| 团队约定后盖章（闸门） | `python scripts/model_builder.py --mark-confirmed <输出目录>/归一决策.tsv --by "<用户姓名/原话>"` |
| 第 2 步 · 映射落盘（`confirmed` 全盖章才准跑） | `python scripts/model_builder.py --fields <字段清单.tsv> --norm <输出目录>/归一决策.tsv --tbl-ch <中文表名> --tbl-en <英文表名> --out <输出目录> [--domain <域>] [--no-sec-id]` |
| 有 DDL 文件时 | 把 `--fields <tsv>` 换成 `--ddl <表.sql>`，并补 `--src-ch/--src-en` |
| 交付物 | `<输出目录>/`：`<tbl_en>_mapping.tsv`(13列) / `roots_used.tsv` / `cache_input.tsv` / `01~04 *.md` / `code_value_appendix.md` / `转义预览.tsv` / `归一决策.tsv` |

- `--fields` 字段清单 TSV：4 列（源表中文/源表英文/字段中文/源字段英文，源表名自动取）或 2 列（字段中文/源字段英文）。
- `--norm` 归一决策 TSV：`src_fld_en / mdl_fld_ch / mdl_fld_en / note / confirmed`，只填「判断」不填机制；
  `note` 里写「类词豁免：<理由>」放行规范特例（存量 `_ind` 等）；写「已审批：<理由>」/「超长审批：<理由>」
  放行超 5 词根段 / 超 25 位（**须用户同意后再写**，交付物留痕）。
- `--no-sec-id`：市场/日历等**无证券维度**的参照表，豁免 §4 必备的 `scr_id`。
- 自检：`python scripts/test_model_builder.py`（改 model_builder 后必跑，不连库）。

## roots.py · 词根 / 翻译 / 缓存

| 用途 | 命令 |
|---|---|
| 批量查词根（多词一次查完） | `python scripts/roots.py lookup 交易日 序号 市场 代码` |
| 批量翻译（stdin 或 `--file`） | `python scripts/roots.py translate --file <中文名.txt>` |
| 批量查缓存 | `python scripts/roots.py cache-hit 交易日历 交易市场代码` |
| **读权威案例**（按模型/类型列缓存条目） | `python scripts/roots.py cache-list --model bnd_base_info`（可加 `--cache-type bnd_base`、`--match 标志`） |
| 拟用字段反向撞名检查（第 3 步前必跑） | `python scripts/roots.py cache-check --file <cache_input.tsv>` |
| 确认后写缓存（第 3 步，自动写词根使用明细） | `python scripts/roots.py cache-add-batch --file <cache_input.tsv> --domain <域> --source-model <模型名> [--cache-type scr_base\|common\|...]` |
| 单条写缓存 | `python scripts/roots.py cache-add <中文名> <英文名> [--cache-type T] [--force]` |
| 同步词根 | `python scripts/roots.py sync`（dry-run）→ `sync --apply` |
| 存量反解词根明细 | `python scripts/roots.py usage-sync-cache [--model M1,M2]` |

## code_value.py / whitelist.py · 码值 / 缩写白名单

| 用途 | 命令 |
|---|---|
| 批量查码域（多域一次查完） | `python scripts/code_value.py lookup 上市状态 交易市场` |
| 建源映射 / 校验 | `python scripts/code_value.py align ...` ／ `validate ...` |
| 缩写白名单 | `python scripts/whitelist.py list` ／ `add <缩写> <全称> <中文> --basis <1-4>` |

## 环境

- Python 解释器与 `pymysql` 属**部署期依赖**：`pip install -r requirements.txt`；运行期禁止现场建 venv / pip install。
- `db.py` 读 skill 根目录 `.env`（MySQL/DDM/KS）。连不上直接报错终止，禁止回退本地 CSV。
