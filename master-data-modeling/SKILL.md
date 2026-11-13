---
name: master-data-modeling
description: 金融主数据建模 skill：按 MPC 规范（ISO/IEC 11179 方法论 + 词根平台）对主数据模型做结构/字段命名/码值的规范设计与多源迭代。当用户提到"主数据建模、主数据改造、dm_mast 层、主数据字段设计、MPC 规范、码值对齐、多源字段整合、新源接入主数据、字段词根翻译"时使用；也适用于"把新源表接入已有主数据模型"和"输出新的主数据模型"请求。
---

# Master Data Modeling · 主数据建模工作流

> 你是**主数据建模工程师**。词根翻译、缓存复用、码值对齐、落盘与校验这些**确定性工作一律交给
> `scripts/`**；字段语义判断（归一/复用/新增/不迁入）、口径裁决、与用户的确认**你自己做并给理由**。
> 命令不会用就查 [`scripts/CLI.md`](scripts/CLI.md)，**禁止读脚本源码**（读一次 `roots.py` 就是 3 万字符上下文）。

## ⛔ 三步流程（每步一个产物 + 一个 STOP 门，禁止跳步）

| 步 | 做什么 | 命令 | 产物 | 门 |
|---|---|---|---|---|
| **1** | MPC 逐词转义（全字段，标准字段除外）→ agent 语义复核 | `model_builder.py --fields … --preview` | `转义预览.tsv` + `归一决策.tsv`（种子） | ⛔ **停下**，把转义表/中文字段名交团队约定 |
| **2** | 字段映射：归一 → 缓存命中 → 词根拼接 | `model_builder.py --fields … --norm …` | 四页交付物 + `roots_used.tsv` + `cache_input.tsv` | ⛔ **停下**，映射表交团队约定 |
| **3** | 写缓存 + review + 沉淀 | `roots.py cache-add-batch` + review 清单 | `review_report.md` + 缓存/词根明细 | review 全绿才交付 |

**闸门在代码里，不在本文档里**：第 2 步不给 `--norm`、或 `归一决策.tsv` 的 `confirmed` 列没盖章，
脚本直接拒绝落盘；团队约定前**禁止**任何 `cache-add`（写缓存是第 3 步）。
用户说"直接按你建议的做"也要先跑第 1 步把转义表贴出来，再盖章继续。

细节按需读（不要一次全读）：

- 第 1 步 → [`references/task1-mpc-escape.md`](references/task1-mpc-escape.md)
- 第 2 步 → [`references/task2-mapping.md`](references/task2-mapping.md)
- 第 3 步 → [`references/task3-writeback.md`](references/task3-writeback.md)

## ⛔ 硬性门控（违反即重做）

1. **词根不臆造**：英文名必须出自 `md_word_roots`（MySQL，`roots.py`）；来源 `miss` 禁止交付——换词
   lookup / 英文术语回查 / 作为规则缺口提案。
2. **缓存命中必复用**：`md_field_cache` 命中的译名原样用，禁止拼出第二个译名（优先级见 task2 §2）。
3. **先归一再翻译**：源中文禁止直译（"债券名称"先归一为证券的"名称"特性）；**子类证券维度的
   代码/名称/简称剥离类型词**：指数代码→
   证券代码 `scr_cd`、指数名称→证券中文名称 `scr_cn_full_name`、简称→`scr_cn_abbr_name`，
   禁止 idx_cd/idx_name 等字面直译。
4. **是否类统一 `xx_flag`**：禁止 `whth_*`/`jdg_*` 直译。
5. **字段名必须以类词收尾**：序号→`seq_no`、编号/流水号→`no`、
   是否→`flag`、代码→`cd`、日期→`date`；`validate` 已强制，特例在 `归一决策.tsv` 的 note 写
   「类词豁免：<理由>」。
6. **≤5 个词根段、总长 ≤25 位**：超限**必须用户审批**（`validate` 会拦：审批后在本模型归一决策 TSV 的
   note 写「已审批：<理由>」/「超长审批：<理由>」放行并在交付物留痕；历史权威案例里存在更长的字段）。
7. **缩写白名单制**：PE/TTM 等只在 `references/fin_abbr_whitelist.csv` 内的可直接用，新增先确认再 `whitelist.py add`。
8. **码值不过脑**：枚举一律 `code_value.py` 查标准码域/建映射，缺口交团队约定，禁止现场编造标准值。
9. **review 是闸门**：`references/review-checklist.md` 全绿才准交付（脚本已强制的项见 task3 §3）。
10. **知识库直写是禁区**：规则页只走提案 → 审核（`references/kb-writeback.md`），提案前先三查防脏数据。

## ⚡ 效率守则（每轮工具调用 = 一次 Python 冷启动 + MySQL 连接，别再逐条试）

1. **批量调用**：`roots.py lookup 词1 词2 词3…` / `cache-hit 名1 名2…` / `code_value.py lookup 域1 域2…`，
   禁止一个词起一次进程。
2. **translate 一次全量**：禁止先小批量试跑再全量；个别字段用 `归一决策.tsv` 覆盖即可，不重跑全量。
3. **交付物只走 `model_builder.py`**：逐模型只写「归一决策 TSV」，禁止手写一次性 build/生成脚本。
4. **物料定位先 `glob`/`ls`**：禁止猜路径连试多次。
5. **依赖是部署期的事**：`pip install -r requirements.txt` 由部署完成，运行期禁止现场建 venv / pip install。
6. **参考文件并行读**：规范/案例/清单在一条消息里并行 Read。

## 📁 资源路由（按需读，别预读）

| 何时 | 读什么 |
|---|---|
| 全程 | `scripts/CLI.md`（命令契约，替代读源码） |
| 第 1 步 · 归一/命名 | `references/mpc-naming-rules.md`（规范全文，唯一一份）、`references/concept_mapping.md`、`references/mpc_classifiers.csv`（类词表+建议类型）、`assets/mpc-terms.md` |
| 遇专业缩写 | `references/fin_abbr_whitelist.csv` |
| 做同型模型 | `roots.py cache-list --model demo_base_info`（**权威案例 = md_field_cache**，离线物料不再参考）+ `references/reference-case.example.md`（案例口径解读） |
| 第 3 步 · 评审/沉淀 | `references/review-checklist.md`、`references/kb-writeback.md` |
| 环境/建表 | `.env.example`、`scripts/schema.sql`、`requirements.txt` |
