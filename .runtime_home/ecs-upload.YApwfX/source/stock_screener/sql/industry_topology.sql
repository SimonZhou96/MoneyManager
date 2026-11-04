-- 产业拓扑关系缓存表（公司维度，TTL 7 天）
CREATE TABLE IF NOT EXISTS industry_relations (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    source_code VARCHAR(32) NOT NULL,
    source_market VARCHAR(8) NOT NULL,
    peer_code VARCHAR(32) NOT NULL,
    peer_market VARCHAR(8) NOT NULL,
    peer_name VARCHAR(128) NOT NULL DEFAULT '',
    relation VARCHAR(32) NOT NULL,
    direction VARCHAR(16) NOT NULL,
    evidence TEXT,
    is_empty TINYINT(1) NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at DATETIME NOT NULL,
    llm_provider VARCHAR(32) NOT NULL DEFAULT '',
    llm_model VARCHAR(64) NOT NULL DEFAULT '',
    peer_market_cap DOUBLE NULL,
    peer_market_cap_str VARCHAR(64) NOT NULL DEFAULT '',
    UNIQUE KEY uk_source_peer_relation (source_code, peer_code, relation),
    KEY idx_source_code (source_code),
    KEY idx_expires (expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
