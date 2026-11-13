# Task 1 · 输入收集 + 查证 + MPC 逐词转义（第 1 步）

> 产出：`转义预览.tsv`（全字段逐词转义） + `归一决策.tsv`（种子）
> 门：**把转义表贴给团队约定**，未确认不许进第 2 步（第 2 步脚本会因 `confirmed` 空而拒绝落盘）。

## 1. 输入收集（缺则向用户追问）

必备三件套：
1. **数据结构**：dm_mast 现有模型字段（或新源表结构）。用户 99% 直接粘贴
   「源表中文名 / 源表英文名 / 字段中文名 / 源字段英文名」TSV——存成 `fields.tsv` 直接用
   `model_builder.py --fields` 喂进去，**不要**先转成 DDL、也不要手写解析脚本。
2. **源系统**：VENDOR_A/VENDOR_B… 及表用途说明
3. **意图**：新增主数据模型 or 向已有主数据模型迭代接入新源

**词根同步**：词根由上游治理团队维护，但**不主动询问、不打断建模流程**
——用户主动提"同步词根"时才跑 `python scripts/roots.py sync`（dry-run 看差异）
→ `sync --apply` 入库，并反馈新增/变更/删除条数与影响。建模中遇到词根 miss 按 §2/第 2 步链路处理。

## 2. 查证与分叉（产出：查证记录）

1. **词根库就绪检查**：`python scripts/roots.py lookup <任一常用词>` 抽查 MySQL `md_word_roots`
   可达且非空（词根唯一数据源）。空表/不可达 → 让用户跑 `roots.py sync --apply`（需 .env 的 DDM_*）。
2. **表存在性判断**：按项目仓库/用户提供的模型清单 + 知识库 wiki_search，判断目标主数据表是否已存在
   → 走 **新增（Task 2 全流程）** 还是 **迭代（Task 2B）**；两者兼有先迭代后新增。
3. **规范查证**：wiki_search 知识库（命名规范/词根口径/码值规范/目标主题域），命中规范页读全文并引用页名
   ——知识库口径**更新于** skill 内置口径时以知识库为准。
4. **同型案例比对**：同类模型先 `python scripts/roots.py cache-list --model demo_base_info` 扫权威案例
   （案例 = md_field_cache，`references/reference-case.example.md` 是它的口径解读）；其他类型先 `cache-list --cache-type`
   找同类模型，再搜知识库有无已重构案例。历史离线物料不再参考。
5. 输出查证记录：表存在性结论 / 引用的规范页 / 案例清单。

## 3. 概念归一 + 中文备注重设计（A1，agent 语义判断）

对每个源字段：
1. **先归一**：按 `references/concept_mapping.md` 识别标准对象类与特性（"债券名称"→证券的"名称"特性），
   用归一后的标准概念去查缓存；**子类证券维度的代码/名称/简称剥离类型词**；
   **表示类消歧**（代码/名称/日期/标识）：源英文可判断按源、源中文带"代码"
   必须保留 `_cd`、都判断不了**默认 `_name`**、"××标的"归一"××标的证券代码"、机构/企业/公司统一 `co`
   ——规则见 `concept_mapping.md` §2.1。
2. 再按数据元三段式重写中文备注：**对象类 + 特性（+ 限定词）**，见 `references/mpc-naming-rules.md` §1/§3。
   禁止照抄旧备注；旧备注里的口径信息（"利率(%)是万分之一"）要保留进新备注。
3. 备注含专业缩写（PE/TTM/EBITDA…）：查 `references/fin_abbr_whitelist.csv`；**不在白名单**的先与用户
   沟通确认，`python scripts/whitelist.py add <缩写> <全称> <中文> --basis <1-4>` 入表后再继续设计。

## 4. MPC 逐词转义（A1.5，交互闸门）

```bash
python scripts/model_builder.py --fields fields.tsv --tbl-ch <中文表名> --tbl-en <英文表名> \
    --out output/<表名>_<YYYYMMDD> --preview
```

输出 `转义预览.tsv` 5 列（**全字段覆盖**）：源字段中文名 / Modifier 修饰词提取 / Part 基词提取 /
Classifier 类词提取 / 转义后字段中文名。结构 = Modifier（对象词）+ Part（特性词）+ Classifier（表示词），
对齐 JR/T 0304；类词→英文与**建议字段类型**见 `references/mpc_classifiers.csv`。

- **并非所有字段都能三段提取全**：允许 M+C、P+C、仅 P 等组合，提取不到的列留白。
- 引擎只出机械草稿（类词尾匹配 + Modifier 提示词 + 单位换算量词），**agent 必须逐字段语义复核**，
  典型复核（会话实测踩过的坑）：
  - `该日期所在周第几个交易日` → 引擎给"该日期所在周第几个交易日期"，应复核为 **周二交易日序号** `wk_trd_day_seq_no`
    （"第几个"是序号，序号类词 = `no`，`seq` 只是序列修饰）；
  - `上一个交易日` → "上一个交易日期" → 复核 **上一个交易日** `prev_trd_date`；
  - 语言限定/拼音简称等按 `mpc-naming-rules.md` §4 前置 Modifier。
- 复核结论写进 `归一决策.tsv` 的 `mdl_fld_ch`（只改这一个列 + `note` 写理由）。
- ⛔ **STOP**：把 `转义预览.tsv`（或复核后的中文名清单）贴给团队约定/批注。团队约定后才
  `model_builder.py --mark-confirmed <输出目录>/归一决策.tsv --by "<用户>"` 盖章，进第 2 步。
- 标准字段（§4：busi_date / rec_no / scr_id / src_id / grp_id / remark / rec_upd_time / rec_down_time）
  由引擎自动映射，不参与转义复核；市场/日历等无证券维度表加 `--no-sec-id`。
