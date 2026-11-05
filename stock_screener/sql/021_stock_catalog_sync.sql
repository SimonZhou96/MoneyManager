-- Catalog metadata lives in stock_pools; no new security master table.
-- The same columns are initialized by STOCK_POOL_CATALOG_COLUMNS in db.py.
SET @ddl = IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='stock_pools' AND COLUMN_NAME='name_zh'), 'SELECT 1', 'ALTER TABLE stock_pools ADD COLUMN name_zh VARCHAR(255) NULL');
PREPARE catalog_ddl FROM @ddl;
EXECUTE catalog_ddl;
DEALLOCATE PREPARE catalog_ddl;
SET @ddl = IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='stock_pools' AND COLUMN_NAME='name_en'), 'SELECT 1', 'ALTER TABLE stock_pools ADD COLUMN name_en VARCHAR(255) NULL');
PREPARE catalog_ddl FROM @ddl;
EXECUTE catalog_ddl;
DEALLOCATE PREPARE catalog_ddl;
SET @ddl = IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='stock_pools' AND COLUMN_NAME='instrument_type'), 'SELECT 1', 'ALTER TABLE stock_pools ADD COLUMN instrument_type VARCHAR(16) NULL');
PREPARE catalog_ddl FROM @ddl;
EXECUTE catalog_ddl;
DEALLOCATE PREPARE catalog_ddl;
SET @ddl = IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='stock_pools' AND COLUMN_NAME='catalog_seen_at'), 'SELECT 1', 'ALTER TABLE stock_pools ADD COLUMN catalog_seen_at DATETIME(6) NULL');
PREPARE catalog_ddl FROM @ddl;
EXECUTE catalog_ddl;
DEALLOCATE PREPARE catalog_ddl;
CREATE TABLE IF NOT EXISTS stock_catalog_sync_runs (
    run_date DATE NOT NULL PRIMARY KEY,
    status VARCHAR(16) NOT NULL DEFAULT 'running',
    request_count INT NOT NULL DEFAULT 0,
    sources_json JSON NOT NULL,
    started_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    finished_at DATETIME(6) NULL,
    error_message TEXT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
