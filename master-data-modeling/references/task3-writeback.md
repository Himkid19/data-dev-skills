# Task 3 · 写缓存 + Review + 知识沉淀（第 3 步）

> 前置：用户已确认第 2 步的映射表。
> 产出：`review_report.md` + 缓存写回（含词根使用明细）+ 规则缺口提案。

## 1. 交付前反查（防撞名）

```bash
python scripts/roots.py cache-check --file <输出目录>/cache_input.tsv
```

英文名撞车、缓存已有同中文名译名的 → 回炉改用缓存译名（缓存命中必复用）。

## 2. 写回缓存（团队约定映射之后，不是之前）

```bash
python scripts/roots.py cache-add-batch --file <输出目录>/cache_input.tsv \
    --domain <域> --source-model <模型名> [--cache-type scr_base|stk_base|bnd_base|idx_base|fund_base|common]
```

- 与缓存既有译名冲突的行会报错，人工裁决后再入（`--force` 覆盖需用户拍板）。
- 同时自动反解写入词根使用明细 `md_root_usage`（一行一个词根段，`root_id` 关联 `md_word_roots.id`）。
- 写入带操作人留痕（`.env` 的 `MD_OPERATOR`）。

## 3. Review 闸门

逐项执行 `references/review-checklist.md`（A 命名合规 / B 完整性 / C 码值 / D 多源整合 / E 交付物），
每项给结论；⚠️ 问题项回炉修正后**重跑全清单**。全绿才交付，`review_report.md` 随产物落盘。

脚本已强制的项（不必人工复核，命中即拒跑）：词根 miss、超 5 段/超 25 位（用户审批并在 note 留痕后放行）、是否类未落 `_flag`、
源中文带"代码"未落 `_cd`、日期类未落 `_date`、时间类未落 `_time`、**字段名缺类词（MPC Classifier）**、
目标英文名重复、源字段映射不完整、§4 标准字段缺失（`--no-sec-id` 可豁免 scr_id）。

## 4. 知识沉淀（Task 4）

1. **规则缺口**：本次建模发现的规范缺口/修正（词根未命中清单、白名单新增记录、命名裁决）→ 按
   `references/kb-writeback.md` §2 对比已有知识后**提案**入库（**不直写正式页**，防脏数据三查）。
2. **用户文档积累**：用户要求文档入知识库时，按 `kb-writeback.md` §3 走"对比 → 判断增量/冲突 → ingest"，
   冲突必须团队约定。
3. 缓存、码值表、词根使用明细属程序资产，不进知识库。

> ⚠️ 规则回写提醒：团队约定出的新命名规则（如 序号→`seq_no`）必须同时落到 **skill 仓库**的
> `references/mpc_classifiers.csv` / `references/fixed_collocations.csv` / `model_builder.py` 校验里，
> 只写知识库页面不改 skill，下个用户会再犯同一处错。
