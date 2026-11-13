# Task 2 · 字段映射（第 2 步）

> 前置：第 1 步的 `归一决策.tsv` 已由团队约定并 `--mark-confirmed` 盖章（否则脚本拒绝落盘）。
> 产出：四页交付物 + `roots_used.tsv` + `cache_input.tsv`。
> 门：**映射表交团队约定**后才进第 3 步（写缓存）。

```bash
python scripts/model_builder.py --fields fields.tsv --norm <输出目录>/归一决策.tsv \
    --tbl-ch <中文表名> --tbl-en <英文表名> --out <输出目录> [--domain <域>] [--no-sec-id]
```

引擎内置的默认归一（机制层，`归一决策.tsv` 可覆盖）：尾部单位括注换算（份→份额/元→金额，万/亿前缀
保留进备注，映射外单位上报团队约定）、是否X→X标志、×日→×日期、××标的→××标的证券代码、§4 标准字段
自动映射与注入、分区字段按 DDL 取。

**子类证券维度类型词剥离**：本表证券维度是指数/基金/债券/
股票时，其代码/名称/简称在 `归一决策.tsv` 里改写为证券级标准名——指数代码→证券代码 `scr_cd`、
指数名称→证券中文名称 `scr_cn_full_name`、简称→`scr_cn_abbr_name`，禁止 idx_cd/idx_name 等字面直译
（规则与边界见 `concept_mapping.md` §2.0）。

## 1. 词根翻译链路（`roots.translate_names`，进程内执行）

```
固定搭配（fixed_collocations.csv，最高优先级）
  → 金融缩写白名单（fin_abbr_whitelist.csv）
    → 翻译缓存（md_field_cache，按 cache_type 优先级）
      → 词根拼接（md_word_roots）
        → 中文 miss 时英文术语回查（en-match，如 永续→Perpetual→perp）
          → 仍无 = 规则缺口，交团队约定
```

来源列含义与处置：

- `cache(...)`：原样复用（"同义字段同名"的生命线，禁止重新拼装出第二个译名）
- `root-compose`：检查拼接是否达意；团队约定的译名在第 3 步 `cache-add` 入缓存
- `needs-approval(N段>5)`：**交用户审批**，通过才可用（或换主干重切）
- `miss`：列出未命中片段 → 换近义词 `lookup` → 英文术语回查 → 仍无则记规则缺口，字段名留占位
  `<待词根登记>` 并在交付物标注

字段 ≤5 个词根段、总长 ≤25 位；是否/判断类统一 `xx_flag`；字段名必须以**类词收尾**。
超段/超长属**用户审批项**：用户同意后在归一决策 TSV 的 note 写「已审批：<理由>」即可放行（留痕进交付物）。

## 2. 缓存优先级（md_field_cache 取用规则，用户可随时问）

> 缓存同时是**权威案例本体**（如 `cache-list --model demo_base_info`）；
> 开工前通读一遍对齐口味，比翻历史文档可靠。

```
scr_base（证券基础信息，scr_base_info 口径）
  > stk_base / bnd_base / idx_base / fund_base（子类基础信息，命名模式 *base_info）
    > common（普通模型字段）
      > other（未分类）
```

- 基础信息表通常形如 `stk_base_info / bnd_base_info / idx_base_info`，录入时按此归类 `cache_type`；
  尚未录入的子类，如实告知"该子类暂无基础信息缓存，当前以 common 条目取用"并建议补录。
- 更高优先级来源与已有条目译名不同**允许并存**，由优先级裁决；同优先级译名不同才是冲突，需人工裁决。
- `roots.py cache-hit <中文名…>` 列出取用条目与低优先级并存条目。

## 3. 码值对齐（枚举字段）

枚举字段逐个：`python scripts/code_value.py lookup <码域>`（多域批量）有域 → 逐源 `align` 建映射 +
`validate` 校验；无域 → 报告用户"该码域未建立，建议先定标准值"，把团队约定的标准值提案走第 3 步
知识沉淀入库，映射先落交付物的码值附表。

## 4. 迭代场景（新源接入已有主数据模型，Task 2B）

1. 新源字段逐个与主数据已有字段做**语义对齐**（同样先过 `concept_mapping.md` 的归一判断），三分类：
   - **复用**：语义等价且粒度一致 → 映射到已有字段（附等价性证据）
   - **新增**：主数据没有该特性 → 走第 1/2 步设计新字段
   - **不迁入**：源系统私有/过程性字段 → 列明理由
2. **多源整合沟通**：同义但各源定义/粒度/码值不同的字段，整理成
   `字段 | 源A口径 | 源B口径 | 差异 | 整合建议` 表格**交给用户拍板**，未确认字段标 `待确认`，不进最终映射。
3. 码值差异（A源 1=未上市 3=上市 vs B源 0=未上市 1=上市）必须经 `code_value.py` 对齐成统一标准值。

## 5. 字段设计规范摘要

全文见 `references/mpc-naming-rules.md`（中文备注三段式、后缀惯例 `_cd/_date/_time/_flag/_desc/_cnt/
_qty/_prop`、率类 `rate/intrt/coup_rate/intr_marg/yield`、价格面值余额 `pric/par_val/bal/ulmt`、
语言限定 `cn/en` 前置、拼音简称 `scr_abbr_pinyin_name`、序数前缀 `fir/main/r/ori/sh/sz`、比值字段
`a/b(%)`→`a_to_b`、标准字段清单、存量兼容 `yearsnum` 等）。**只读这一份，不要另找规范摘要。**
