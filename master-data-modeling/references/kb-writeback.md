# 知识库协同 · 查询、回写与防脏数据

> agent memory 组件里与本 skill 相关的是 **MemoryKnowledge（wiki 知识库）** 和
> **MemoryCore（L1 记忆）**。本文件定义 skill 与它们的交互口径。
> **所有端点与凭据统一从 skill 根目录 `.env` 读取**（模板见 `.env.example`）：
> `PANEL_BASE_URL / TDAI_SERVICE_ID / TDAI_USER_KEY / KS_BASE_URL /
> WIKI_RULES_OFFICIAL / WIKI_RULES_DRAFT`。
> 执行 curl 前 `source .env`（或由 harness 注入）；`.env` 含 user key，禁止提交 git。
> 检索用注入的 `<knowledge_tools>` 自发现即可；写操作走 Panel API（带
> `X-Tdai-Service-Id` + `X-Tdai-User-Key` 头，user key 是权限与审计的依据）。

## 0. 知识分层原则（什么放哪）

| 内容 | 放哪 | 为什么 |
|---|---|---|
| 词根字典 | MySQL 表 `md_word_roots`（唯一数据源；上游词根库维护，`roots.py sync` 同步） | 精确查找型，要确定性；多用户共享 + 可自动迭代，LLM 读 5 万 token 的 md 又贵又易幻觉 |
| 词根使用明细 | MySQL 表 `md_root_usage`（一行一个词根段，root_id 关联 md_word_roots.id；确认入缓存时写入，translate 不落记录） | 按词根粒度可 JOIN 聚合，支撑"最终主数据用了哪些词根"的审计与治理 |
| 已确认字段翻译 | MySQL 表 `md_field_cache`（scripts/db.py 连接；CSV 仅首次引导种子），含 `cache_type` 优先级 | 复用型缓存，同义字段同名的保障，多用户共享 |
| 标准码值 + 源映射 | references/code_values*.csv（程序管理） | 结构化对齐，脚本校验 |
| MPC 规范解读、建模方法论 | skill references/（稳定）+ 知识库 wiki（演进解读） | 稳定规则内嵌随 skill 分发；解读与修正进知识库可迭代 |
| 建模案例、决策记录、踩坑 | **知识库 wiki**（活水） | 需要 search 召回、需要积累演进 |
| 个人建模偏好、会话经验 | MemoryCore L1（自动沉淀） | 无需 skill 干预 |

## 1. 查询（建模前查证，强制）

用注入的 knowledge tools（KS tools/call）：

```
wiki_search  query="<主题> 规范"      # 词根补充口径、命名规范修正、同类案例
read_page    page="字段设计/码值规范"  # 命中规范页后读全文
```

搜索结论要**引用页名**写进建模过程记录；没搜到规范≠没规范，按 skill 内置口径执行并记缺口。

## 2. 回写提案（发现规范缺口/修正时）

⛔ **skill 禁止直写正式规则页**。一律走提案 → 审核（rule-reviewer skill / 治理组）：

1. **写提案前先对比**（防脏数据三查）：
   - wiki_search 已有规则：是否已覆盖？覆盖了是"修正"还是"新增"？
   - 与本 skill 的 references/ 口径是否冲突？冲突点写明；
   - 证据链：来自哪次建模、哪个模型、什么问题（无证据不提案）。
2. 提案文件（frontmatter + 可执行条文 + 证据），提交到草稿区（wiki_id 用 `$WIKI_RULES_DRAFT`）：

```bash
source <skill根目录>/.env
curl -s -X POST "$PANEL_BASE_URL/api/v1/knowledge/wiki/raw/write" \
  -H "X-Tdai-Service-Id: $TDAI_SERVICE_ID" -H "X-Tdai-User-Key: $TDAI_USER_KEY" \
  -H "content-type: application/json" \
  -d '{"team_id":"<teamId>","wiki_id":"'"$WIKI_RULES_DRAFT"'","files":[{"filename":"rule-<YYYYMMDD>-<slug>.md","content":"<提案全文>"}]}'
```

3. 告知用户提案 ID 与"审核通过后生效"，并把提案摘要写进当次交付物。

## 3. 用户文档积累（多源文档入库）

用户说"把这份文档加入知识库"时：

1. 读文档 → 提炼主题与关键信息；
2. wiki_search 对比：**已有知识没覆盖** → 整文档 ingest；**部分覆盖** → 整理成增量
   （说明新增了什么、和已有条目关系），不要整篇倒进去；**冲突** → 停下向用户呈现
   两边内容，确认以哪边为准，再以修正形式入库；
3. ingest 走 Panel（端点与凭据同上，全部取自 .env）：`POST $PANEL_BASE_URL/api/v1/knowledge/wiki/raw/write`
   （目标：知识库 wiki）+ 触发 `POST $PANEL_BASE_URL/api/v1/knowledge/wiki/ingest`；ingest 有幂等增量机制（sha256 判重、同主题页
   自动合并、冲突保留双方标注），重复提交安全。

## 4. 与 MemoryCore 记忆的分工

- L1 召回是**自动**的（每轮注入），skill 不需要也没必要把建模结论塞进记忆——照常对话即可。
- 缓存（MySQL 表 md_field_cache / md_code_values / md_code_value_mappings）的更新是 skill 的责任：
  每次团队约定翻译后 `roots.py cache-add`（写 MD_OPERATOR 审计人）。缓存/码值表在 MySQL 中
  **多用户共享**，不进知识库也不随 skill 分发；skill 里的同名 CSV 只是首次引导种子。
