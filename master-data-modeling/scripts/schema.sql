-- master-data-modeling skill · 共享数据表结构
-- 目标库：任意 MySQL 库（连接配置见 skill 根目录 .env 的 MYSQL_*，示例库名 datadev_mast）
-- 首次部署：执行本文件建表，再灌初始数据：
--   python roots.py sync --apply         # 从词根平台首次引导 md_word_roots（词根唯一数据源，需 .env 的 DDM_*）
--   python roots.py cache-import-seed    # field_cache.csv（或 .example）→ md_field_cache（翻译缓存首次引导）
--   python code_value.py 的种子导入（见 code_value.py 帮助）
-- 表归 MySQL 管，skill 内同名 CSV 仅作首次引导种子；词根不落本地文件。

-- v3 变更
-- md_root_usage 重构为词根使用明细表：一行一个词根段（seg_seq 还原字段名），
--   root_id 关联 md_word_roots.id 支撑按词根聚合审计；非词根成分（固定搭配/白名单/
--   规则衍生 flag、cn、to）root_id 为 NULL、seg_source 标注来源。
-- 写入时机改为"确认入缓存时"（cache-add / cache-add-batch / usage-sync-cache 回填），
--   translate 不再写记录。
-- 存量库执行文末【v3 存量迁移】段落。
-- ─────────────────────────────────────────────
-- v2 变更
-- 1) 新增 md_word_roots：词根字典迁入 MySQL（上游词根库维护，roots.py sync 同步）
-- 2) 新增 md_root_usage：词根使用记录表（translate 自动落，按模型留痕）
-- 3) md_field_cache 增加 cache_type 优先级列，并放开 fld_ch 唯一键（同名多源按优先级取用）
--    存量库执行文末【v2 存量迁移】段落。
-- ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS md_word_roots (
  id INT AUTO_INCREMENT PRIMARY KEY,
  root_zh VARCHAR(64) NOT NULL COMMENT '词根中文名',
  root_en VARCHAR(128) NOT NULL DEFAULT '' COMMENT '词根英文全称',
  abbr VARCHAR(32) NOT NULL COMMENT '标准缩写（词根库口径）',
  category VARCHAR(64) NOT NULL DEFAULT '' COMMENT '分类',
  synced_at DATE NOT NULL COMMENT '最近同步日期',
  UNIQUE KEY uk_root (root_zh, abbr)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='主数据建模·词根字典（上游词根库维护，roots.py sync 同步）';

CREATE TABLE IF NOT EXISTS md_root_usage (
  id INT AUTO_INCREMENT PRIMARY KEY,
  model_name VARCHAR(128) NOT NULL COMMENT '使用词根的模型/表名（如 bnd_base_info）',
  fld_ch VARCHAR(128) NOT NULL COMMENT '字段中文名（归一后）',
  fld_en VARCHAR(64) NOT NULL COMMENT '最终英文字段名',
  root_id INT NULL COMMENT '关联 md_word_roots.id；非词根成分（搭配/白名单/规则衍生）为 NULL',
  abbr VARCHAR(32) NOT NULL COMMENT '该段实际使用的缩写（快照，不 JOIN 也能看）',
  seg_seq TINYINT NOT NULL COMMENT '段序号 1..n，可按序还原字段名',
  seg_source VARCHAR(16) NOT NULL DEFAULT 'root' COMMENT 'root=词根/collocation=固定搭配/whitelist=白名单/rule=规则衍生/unknown',
  operator VARCHAR(64) NOT NULL DEFAULT '' COMMENT '操作人（MD_OPERATOR）',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_usage (model_name, fld_ch, fld_en, seg_seq),
  KEY idx_root (root_id),
  KEY idx_model (model_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='主数据建模·词根使用明细（一行一个词根段，确认入缓存时写入，幂等可重跑）';

CREATE TABLE IF NOT EXISTS md_field_cache (
  id INT AUTO_INCREMENT PRIMARY KEY,
  fld_ch VARCHAR(128) NOT NULL COMMENT '字段中文名（标准名）',
  fld_en VARCHAR(128) NOT NULL COMMENT '确认的英文字段名',
  domain VARCHAR(64) NOT NULL DEFAULT '' COMMENT '业务域',
  cache_type VARCHAR(32) NOT NULL DEFAULT 'common' COMMENT '缓存优先级类型：scr_base(证券基础)>stk/bnd/idx_base(子类基础)>common(普通)>other',
  source_model VARCHAR(128) NOT NULL DEFAULT '' COMMENT '首次确认来源模型',
  confirmed_by VARCHAR(64) NOT NULL DEFAULT '' COMMENT '确认人',
  confirmed_at DATE NOT NULL COMMENT '确认日期',
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_fld_ch (fld_ch)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='主数据建模·已确认字段翻译缓存（多用户共享，同中文名多源并存按 cache_type 优先级取用）';

CREATE TABLE IF NOT EXISTS md_code_values (
  id INT AUTO_INCREMENT PRIMARY KEY,
  code_domain VARCHAR(64) NOT NULL COMMENT '码域',
  std_value VARCHAR(64) NOT NULL COMMENT '标准值',
  std_label VARCHAR(128) NOT NULL COMMENT '标准含义(中文)',
  std_label_en VARCHAR(128) NOT NULL DEFAULT '' COMMENT '标准含义(英文)',
  source_of_truth VARCHAR(128) NOT NULL DEFAULT '' COMMENT '口径归属',
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_dom_val (code_domain, std_value)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='主数据建模·标准码值表';

CREATE TABLE IF NOT EXISTS md_code_value_mappings (
  id INT AUTO_INCREMENT PRIMARY KEY,
  code_domain VARCHAR(64) NOT NULL,
  src_sys VARCHAR(32) NOT NULL COMMENT '源系统',
  src_field VARCHAR(128) NOT NULL,
  src_value VARCHAR(64) NOT NULL,
  src_label VARCHAR(128) NOT NULL DEFAULT '',
  std_value VARCHAR(64) NOT NULL,
  note VARCHAR(255) NOT NULL DEFAULT '',
  updated_by VARCHAR(64) NOT NULL DEFAULT '' COMMENT '对齐操作人',
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_map (code_domain, src_sys, src_field, src_value)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='主数据建模·源码值→标准值映射';

-- ─────────────────────────────────────────────
-- v2 存量迁移（已建过 v1 表的库执行；新库直接跑上面建表语句即可）
-- ─────────────────────────────────────────────
-- ALTER TABLE md_field_cache
--   ADD COLUMN cache_type VARCHAR(32) NOT NULL DEFAULT 'common' COMMENT '缓存优先级类型：scr_base>子类base>common>other' AFTER domain;
-- ALTER TABLE md_field_cache DROP INDEX uk_fld_ch;
-- ALTER TABLE md_field_cache ADD INDEX idx_fld_ch (fld_ch);
-- 已有基础信息类缓存可回填优先级，例如：
-- UPDATE md_field_cache SET cache_type='scr_base' WHERE source_model LIKE '%scr_base%';
-- UPDATE md_field_cache SET cache_type='bnd_base' WHERE source_model LIKE 'bnd_base%';

-- ─────────────────────────────────────────────
-- v3 存量迁移（已建过 v2 md_root_usage 的库按序执行；新库直接跑上面建表语句即可）
-- 1) 旧记录（拼接串格式、translate 时点的未确认数据）留档：
-- RENAME TABLE md_root_usage TO md_root_usage_legacy;
-- 2) 重新执行本文件建出新结构 md_root_usage；
-- 3) 回填已确认字段的词根明细（全部或按 model 过滤）：
--   python roots.py usage-sync-cache
--   python roots.py usage-sync-cache --model bnd_base_info,scr_base_info
-- 4) 确认无误后清理留档表：
-- DROP TABLE IF EXISTS md_root_usage_legacy;
-- ─────────────────────────────────────────────
