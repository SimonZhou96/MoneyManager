-- 股票板块成分关系表
-- 用于补齐筛选结果 CSV 的“所属板块”，并为热点板块标注提供基础数据。

CREATE TABLE IF NOT EXISTS stock_sector_memberships (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    market VARCHAR(8) NOT NULL,
    code VARCHAR(32) NOT NULL,
    sector_type VARCHAR(32) NOT NULL,
    sector_code VARCHAR(64) NULL,
    sector_name VARCHAR(128) NOT NULL,
    source VARCHAR(32) NOT NULL,
    as_of_date DATE NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uk_sector_member (market, code, sector_type, sector_name, source),
    KEY idx_sector_member_code (market, code),
    KEY idx_sector_member_sector (market, sector_type, sector_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
