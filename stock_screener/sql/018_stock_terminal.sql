CREATE TABLE IF NOT EXISTS stock_quote_cache (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    market VARCHAR(8) NOT NULL COMMENT '市场，例如 A/US/HK',
    code VARCHAR(32) NOT NULL COMMENT '证券代码',
    payload_json JSON NOT NULL COMMENT '报价快照 JSON',
    source VARCHAR(32) NOT NULL DEFAULT 'cache',
    fetched_at DATETIME(6) NOT NULL,
    expires_at DATETIME(6) NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uk_stock_quote_cache_symbol (market, code),
    KEY idx_stock_quote_cache_expires (expires_at),
    KEY idx_stock_quote_cache_updated (updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
COMMENT='Stock Terminal 报价 TTL 缓存';

CREATE TABLE IF NOT EXISTS stock_minute_cache (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    market VARCHAR(8) NOT NULL COMMENT '市场，例如 A/US/HK',
    code VARCHAR(32) NOT NULL COMMENT '证券代码',
    trade_date DATE NOT NULL COMMENT '交易日',
    payload_json JSON NOT NULL COMMENT '分时走势点位 JSON 数组',
    source VARCHAR(32) NOT NULL DEFAULT 'cache',
    fetched_at DATETIME(6) NOT NULL,
    expires_at DATETIME(6) NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uk_stock_minute_cache_day (market, code, trade_date),
    KEY idx_stock_minute_cache_expires (expires_at),
    KEY idx_stock_minute_cache_updated (updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
COMMENT='Stock Terminal 分时 TTL 缓存';

CREATE TABLE IF NOT EXISTS stock_fund_flow_cache (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    market VARCHAR(8) NOT NULL COMMENT '市场，例如 A/US/HK',
    code VARCHAR(32) NOT NULL COMMENT '证券代码',
    payload_json JSON NOT NULL COMMENT '资金流点位 JSON 数组',
    source VARCHAR(32) NOT NULL DEFAULT 'cache',
    fetched_at DATETIME(6) NOT NULL,
    expires_at DATETIME(6) NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uk_stock_fund_flow_cache_symbol (market, code),
    KEY idx_stock_fund_flow_cache_expires (expires_at),
    KEY idx_stock_fund_flow_cache_updated (updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
COMMENT='Stock Terminal 资金流 TTL 缓存';
