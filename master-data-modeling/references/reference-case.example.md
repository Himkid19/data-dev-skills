# 参考案例（示例）· 证券基础信息 demo_base_info（权威数据源 = md_field_cache）

> 这是**脱敏示例**，用于说明"案例即缓存"的用法。真实案例来自你们团队的
> MySQL `md_field_cache`：`cache_type=<子类>_base / source_model=<模型名>` 的已确认译名。
>
> 一条命令读案例，不要翻离线表格：
>
> ```bash
> python scripts/roots.py cache-list --model demo_base_info # 全部条目
> python scripts/roots.py cache-list --model demo_base_info --match 标志 # 看某类口径
> python scripts/roots.py cache-hit <中文字段名> # 单字段取用（含优先级并存条目）
> ```

## 1. 为什么以缓存为准

- 缓存是**多人确认过的最新口径**（`confirmed_by`/`confirmed_at` 留痕，多用户共享）；
 离线表格是一次性评审物料，之后的命名裁决不会回写进去。
- 做同型模型前，先 `cache-list` 通读一遍，比读案例文档更快、更准、也更省轮次。

## 2. 从示例案例提炼的口径

| 语义 | 示例写法 |
|---|---|
| ID/身份语义 | `_id` 收尾：发行机构id `iss_co_id`、统一证券编码 `scr_id` |
| 指示/判断语义 | `_flag` 收尾：免税标志 `tax_free_flag`、跨市场标志 `crsmkt_flag` |
| 代码 | `_cd`：市场代码 `mkt_cd`、证券代码 `scr_cd` |
| 号/序号 | `_no`：记录编号 `rec_no`（无 `_seq` 残缺名） |
| 名称（中英并存） | 语言限定前置：证券中文全称 `scr_cn_full_name`、证券中文简称 `scr_cn_abbr_name` |
| 说明/备注文本 | `_desc_text`：说明文本 `xxx_desc_text` |
| 率类 | `iss_coup_rate`（发行票面利率）、`intr_marg_rate`（利差率） |
| 方式 vs 方法 | 明确区分：计息方式名称 `inta_way_name` / 计息方法名称 `inta_method_name` |
| 存量特例 | 经团队约定的历史字段名原样保留，不重新推导 |

## 3. 用法

- **开工扫案例**：新模型开工前 `cache-list --model <同型模型>`（或 `--cache-type <子类>_base`）通读，
 把命名口径对齐，而不是逐字段猜。
- **逐字段取用**：`cache-hit` 命中必复用（按 cache_type 优先级：scr_base > 子类 base > common > other）。
- **交付前反查**：`cache-check --file cache_input.tsv` 防英文名撞车/重复造名。
- **案例自我更新**：新确认的译名按第 3 步 `cache-add-batch` 回写缓存 —— **案例就是缓存本身**，
 没有第二份需要同步维护的文档。

> 交付物形态仍沿用四页（目录 / 基础词根圈定 / 字段词根表 / 字段映射表），该形态已固化在
> `scripts/model_builder.py`（01~04 MD），不需要再从历史物料里对照。
