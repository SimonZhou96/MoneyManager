-- 核心指数成分股快照表
-- 用于在线指数成分股来源失败时，从数据库复用最近一次成功快照。

CREATE TABLE IF NOT EXISTS stock_index_constituents (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    market VARCHAR(8) NOT NULL COMMENT '市场: HK/US/A',
    index_code VARCHAR(32) NOT NULL COMMENT '指数代码',
    index_name VARCHAR(255) NULL COMMENT '指数名称',
    code VARCHAR(32) NOT NULL COMMENT '股票代码',
    name VARCHAR(255) NULL COMMENT '股票名称',
    source VARCHAR(64) NOT NULL DEFAULT 'online' COMMENT 'online/db_fallback/manual_seed',
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uk_stock_index_constituent (market, index_code, code),
    KEY idx_stock_index_constituents_market (market),
    KEY idx_stock_index_constituents_code (code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
COMMENT='核心指数成分股快照表';
