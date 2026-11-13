# MPC 主数据命名规范 · 执行手册

> 本手册是 skill 自带的**执行口径**，来源两份：① 公开调研结论（见文末来源与可信度）；② **权威案例 =
> `md_field_cache` 的已确认译名**。与知识库冲突时，以知识库中**更新的**治理决策为准
> （用 wiki_search 查"命名规范"），并按 kb-writeback.md 流程把分歧反馈回知识库。

## 0. MPC 是什么（引用纪律）

- **公开渠道查无以 "MPC" 命名的行业标准**。MPC 是企业内部规范名，其方法论内核来自
  **ISO/IEC 11179（GB/T 18391）数据元标准**与**证监会《证券期货业数据模型》（SDOM）**的词根规则。
- ⛔ 在对外文档/注释中**不要**把 MPC 写成"行业标准"；表述为"公司主数据建模规范（方法论对齐
  ISO/IEC 11179）"。

## 1. 数据元三段式命名（ISO/IEC 11179 核心）

每个字段 = **对象类(Object) + 特性(Property) + 表示类(Representation)**（可加限定词）：

```
证券(对象类) + 代码(特性) → scr_cd
债券(对象类) + 名称(特性→文字表示) → full_name
上市(特性) + 状态(表示类→码值) → list_stat
```

中文备注（字段中文名）必须能还原这个结构：先说"什么东西"，再说"什么属性"。
中文名三元组：**名词中文名 / 全称 / 简称**（对应 11179 的 名称/同义名称/定义），登记新数据元时三列都填。

### 1.1 MPC 命名结构

MPC 英文物理字段命名结构 = **Modifier 修饰词 + Part 基词 + Classifier 类词/量词**，与
JR/T 0304（金融行业标准）"**对象词 + 特性词 + 表示词**"一一对应：

| MPC | JR/T 0304 | 作用 |
|---|---|---|
| Modifier 修饰词 | 对象词（限定词） | 限定时间/来源/币种/维度/范围，可选、可多个 |
| Part 基词 | 特性词（主体词） | 核心业务实体/指标本体，必须存在 |
| Classifier 类词/量词 | 表示词 | 标识度量类型（日期/金额/数量/比率/代码……） |

- **类词表**：份额→**shr**、**率→rate**（替代 利率→Intrt 行，利率→Intrt
  保留为固定搭配）、日期→**date**；新增 **文本→text**（说明/备注/描述类统一文本类词）、
  **数值→val**（无专属金融量纲的金融参数：合约乘数/系数/因子等）、**次数→cnt**、
  **年份→year**；移除 价值/名义金额/敞口/份数。全表含**建议字段类型**（建表 DDL 参考）见
  `references/mpc_classifiers.csv`。已入 fixed_collocations 的类词以搭配表为准。
- **转义逐词提取**：字段先分解为 Modifier/Part/Classifier（允许 M+C、P+C、仅 P 等组合，
提取不到留白），产出转义提取表交团队约定（第 1 步，见 references/task1-mpc-escape.md §4）；映射阶段结合缓存推荐译名。
- **语言限定是 Modifier 前置**：证券中文名称 → scr_cn_full_name、
  证券英文简称 → scr_en_abbr_name。
- **拼音不进语言限定位**：拼音简称是 **P+C**——证券拼音简称 →
  `scr_abbr_pinyin_name`（M=scr / P=abbr_pinyin / C=name），禁用 `scr_abbr_pinyin` 后缀式；
  固定搭配 `拼音简称/简称拼音 → abbr_pinyin_name` 已入 fixed_collocations.csv。单独"拼音"仍作词根
  （拼音→pinyin）。存量缓存 `简称拼音→abbr_pinyin` 按新口径重指（`roots.py cache-add 简称拼音 abbr_pinyin_name --force`）。

## 2. 词根规则（对齐 SDOM，权威数据源 = MySQL md_word_roots，上游词根库维护）

| 规则 | 口径 |
|---|---|
| 词根来源 | **只允许**词根字典里的缩写（MySQL `md_word_roots` 是唯一数据源，`roots.py sync` 从 词根平台同步，不落本地文件）。严禁模型记忆或现场臆造缩写。中文直查 miss 时按**英文术语回查**：把中文词翻成英文术语，按词根库**全称列**匹配（如 永续→Perpetual→perp、递进→Progressive→进度 prog、浮息→Floating→浮动 flot、长→Longest→longst）；仍无才与团队约定 |
| 固定搭配 | `references/fixed_collocations.csv` 里的中文搭配**强制**用固定缩写，优先级最高、覆盖字典旧译：次数/计数→cnt、金额→amt、数量→qty、比例→prop、日期→date、时间→time、说明→desc……（主数据内部规范守则，单独维护，新增搭配随案例补充）。**高优先级词根批次**随批次入库：方法/方式→way、公司/机构/企业→co、证券→scr、货币→crrc、基期→base、国别地区→cty 等；与 旧译冲突时以批次为准（已覆盖 机构→Org、企业→Corp）。缓存整字段译名与批次口径冲突时不自动覆盖——按规则人工裁决后 `cache-add --force` 更新缓存 |
| 金融缩写白名单 | PE/TTM 等金融领域指定缩写**跳过词根处理直接使用**，但必须在 `references/fin_abbr_whitelist.csv` 内。**界定标准**（满足其一）：① 国际/行业标准编码（ISO 4217/6166/10383/17442/10962）；② 证券期货业无歧义通用术语（PE/PB/EPS/ROE/TTM/YTM）；③ 仓库现有模型高频使用（rg 可佐证）；④ 词根治理组登记认可。新缩写先与用户沟通确认后 `whitelist.py add` 入表 |
| 词根长度 | 主词根 ≤4 位（小写字母+下划线），如 `scr`、`bnd`、`cd`、`dt`；词根库已登记的复合/特例词根以词根库为准（如 crsmkt、yearsnum、longst、0229） |
| 存量兼容 | 下游已稳定使用的存量译名（如 年内序号→yearsnum）经团队约定可原样保留并在决策备注说明，不强制词根化 |
| 字段长度 | 字段名**默认不超过 5 个单词（词根段）**、总长 ≤25 位；超限产出标记 `needs-approval`，**必须用户审批**后才能采用 |
| 比值字段 | 源字段 a/b(%) 归一为「a占b比重」，英文名 = `abbr(a)_to_abbr(b)`（下划线分隔，to 计入段数；缺词根与团队约定后登记固定搭配）。详见 `concept_mapping.md` §5 |
| 表名 | 数据域前缀 + 业务词根串，≤25 位；主数据模型域前缀为 `dm_`（现有 dm_mast），业务域落地为 `bnd_base_info` 风格（内部层前缀由数据平台约定） |
| 临时/过程表 | `_tmp` / `_bak` 后缀，禁止混入正式主数据 |

## 3. 字段类型与后缀惯例

| 字段语义 | 后缀/前缀 | 示例 |
|---|---|---|
| 日期 | **统一 `_date` 后缀**（弃用 `_dt`/`_day`；起始→`begn`、截止→`end`） | busi_date, list_date, iss_begn_date |
| 时间 | **`_time` 后缀**；记录创建/进表时间统一 rec_down_time（§4） | rec_upd_time, rec_down_time |
| 率/利率 | 率→`rate`、利率→`intrt`、票面利率→`coup_rate`、利差→`intr_marg`、收益率→`yield`、频率→`freq` | iss_cms_rate, coup_rate, intr_marg, ref_yield |
| 名称/简称（中英并存） | **统一证券维度标准结构，写法见 §1.1**：语言限定 `cn`/`en` 作 Modifier 前置、证券英文简称 `scr_en_abbr_name`）；**拼音简称 = M=scr / P=abbr_pinyin / C=name**；长→`longst`。禁止 `en_name`/`en_abbr_name` 前缀式与 `scr_abbr_pinyin` 等 `_pinyin` 后缀式 | scr_cn_full_name, scr_en_abbr_name, scr_abbr_pinyin_name |
| 价格/面值/余额/上限 | `pric` / `par_val` / `bal` / `ulmt` | iss_pric, par_val, bnd_bal, iss_amt_ulmt |
| 序数/方位前缀 | 首→`fir`、主→`main`、再→`r`、原→`ori`、沪→`sh`、深→`sz`、并列同类字段数字后缀 `_1`/`_2` | fir_mkt_flag, main_undrw_name, rguar_name, ori_bnd_cd, sh_list_scal_amt, scr_det_type_1 |
| 代码/外部码 | `_cd` | scr_cd, mkt_cd |
| 号/号码/编号/序号类 | **必须以类词 `no` 收尾**：编号/流水号/单号/批号→`no`，序号/次序/顺序/序列→`seq_no`（`seq` 只是序列修饰，不是类词）。禁止 `…_seq` 这类缺类词的残缺名 | trd_seq_no, wk_trd_day_seq_no, doc_no, rec_no |
| 机构/公司类 | 机构、企业、公司**统一 co**，禁止 org/corp/comp_org（编制机构→comp_co、发布机构→iss_co） | comp_co_cd, iss_co_id, iss_co_name |
| 表示类消歧 | 源中文名不带表示类时按 `concept_mapping.md` §2.1 消歧：源英文可判断按源（date→`_date`、name→`_name`、cd/code→`_cd`、id→`_id`）；源中文带"代码"必须保留 `_cd` 收尾；都判断不了**默认按名称**加 `_name`；"××标的"**默认归一"××标的名称"**，映射明确给出证券代码的以映射为准（指数标的→idx_undrl_scr_cd）。**源英文字段名以 `_id` 结尾时目标也以 `_id` 收尾**：`corp_id`→`co_id`（Modifier 归一 corp→co，表示类按源）、`secu_id`→`scr_id`（§4 标准字段，脚本已内置） | 基期+src idx_basic_date→基期日期 base_date；指数特征代码→idx_char_cd；国别地区→cty_name；机构编号+src corp_id→co_id |
| **"××标识"语义分流** | **ID/身份/主键/编码语义 → `_id`；指示/判断语义 → `_flag`**（权威案例零 `_ind`：发行公司id `iss_co_id`、担保人id `guar_id`、次级债混合资本债标志 `subordinate_compd_cptl_debt_flag`）。**禁用 `_ind` 直译**：历史旧根 标识→Ind 仅存量兼容（`fnd_clas_ind`/`inr_ind`/`prd_perf_disc_ind`，不主动改名）；新模型产出 `_ind` 会被 `validate` 拦下，歧义字段交团队约定后写「类词豁免：<理由>」或在归一决策里改成 `_id`/`_flag` | 发行公司id `iss_co_id`、担保人id `guar_id`、次级债混合资本债标志 `subordinate_compd_cptl_debt_flag` |
| 主键/记录标识 | 参见 §4 标准字段 | rec_no, scr_id |
| 日期 | `_dt` / `_date` | busi_date |
| 金额 | 显式带币种词根，DECIMAL | ccy, amt |
| 布尔/是否/判断 | **统一 `_flag` 后缀**（=××标志）："是否城投债"→`ct_bnd_flag`；禁止 `whth_*`/`jdg_*` 直译，脚本已内置剥离"是否/判断"前缀的规则 | ct_bnd_flag |
| 数量/比例 | `_cnt` / `_qty` / `_prop` / `_pct` | hold_pct |
| 文本/描述 | `_desc` / `_text` / `_fpml` | bond_fpml |
| 码值/枚举 | `_stat` / `_cls` / `_type`，必须过码值对齐 | list_stat |

## 4. 主数据标准字段（每个主数据表必备，直接复用）

来自参考案例（dm_mast → 主数据 的固定映射，写映射时直接套）：

> 本清单是**同批标准字段词**：`remark` 与 `rec_upd_time`/`rec_down_time` 一样，
> 按既有名直接复用，**不参与类词收尾校验、不做类词化改写**（`remark` 不改成 `remark_text`）；
> 无证券维度的参照表（市场/日历/参数）经团队约定后可用 `--no-sec-id` 豁免 `scr_id`。

| 标准字段 | 中文名 | 说明 |
|---|---|---|
| busi_date | 业务日期 | 固定数据日期 |
| rec_no | 记录编号 | 注意：dm_mast 旧名是 rec_id，主数据统一 rec_no |
| scr_id | 统一证券编码 | 旧名 secu_id；经 scr_base_info 关联 in_code 转换 |
| grp_id | 并行标识 | **标准字段**：源表"并行标志/并行标识"等一律映射 grp_id，禁止 prll_flag 等按字面直译 |
| src_id | 源系统标识 | 分区字段，各源一份 |
| remark | 备注 | 表级通用备注（子类特有备注保留前缀，如 bnd_remark） |
| rec_upd_time | 数据更新时间 | 每表必备 |
| rec_down_time | 数据进表时间 | =旧口径"记录创建时间"，惯例名，每表必备 |

## 5. 主数据与普通模型的四条铁律

1. **先归一再翻译**：源表中文字段禁止直译进主数据——先按 `references/concept_mapping.md`
   定位真实业务场景（对象类+特性），用标准概念查缓存（例："债券名称"先归一为证券维度，
   再用"证券中文名称"查缓存）；**子类表的证券维度字段必须剥离类型词**：指数代码→证券代码 scr_cd、指数名称→证券中文名称 scr_cn_full_name，
   禁止 idx_cd/idx_name 直译；子类特有属性（债券期限、票面利率等）保留子类词根，不强归一。
2. **同义字段同名**：同一业务含义在所有主数据表里必须用同一个字段名——翻译前先查
   MySQL 表 `md_field_cache`（经 scripts/roots.py；缓存命中必须复用），确认后写回缓存。
3. **缓存按优先级取用**：md_field_cache 增加 `cache_type` 分层，同一中文名多源并存时按
   `scr_base（证券基础信息）> stk/bnd/idx/fund_base（子类基础信息）> common > other`
   自动取最优；基础信息表（`*_base_info`）录入缓存时必须归类对应 cache_type。
4. **码值标准化**：所有枚举字段的值必须落在 MySQL 表 `md_code_values` 的标准码域内，
   源系统的原始码值通过 `md_code_value_mappings` 映射对齐（scripts/code_value.py）——
   **禁止**把源码值直接搬进主数据。

## 6. 来源与可信度

- ISO/IEC 11179 / GB/T 18391 数据元三段式命名、SDOM 词根规则（≤4位、≤5词、25位）：公开资料，
  可信度高（多篇独立中文来源互证，含《证券期货业数据模型》引用文）。
- "MPC" 本体：公开检索无结果，判定为企业内部规范名，引用纪律见 §0。
- 主题域划分：SDOM 八大域（主体 pty、品种 var、账户 acc、交易 evt、资产 ast、营销 mkt、
  渠道 chn、合同 agt + 跨域 pub）；证券/债券/基金归"品种"域，机构/客户归"主体"域。
- 国际编码锚点（参考）：ISO 6166 (ISIN)、ISO 10383 (MIC)、ISO 17442 (LEI)、ISO 10962 (CFI)。
