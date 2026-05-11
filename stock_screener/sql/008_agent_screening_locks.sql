-- Cloud Web screening jobs are queued for the local OpenD Agent.
-- One successful full-market screening run is allowed per market + timeframe + rule-chain + day.

CREATE TABLE IF NOT EXISTS screening_run_locks (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    lock_id VARCHAR(64) NOT NULL,
    run_date DATE NOT NULL,
    market VARCHAR(8) NOT NULL,
    timeframe VARCHAR(8) NOT NULL,
    chain_key VARCHAR(64) NOT NULL DEFAULT 'default_zuoyi_and_other',
    status VARCHAR(32) NOT NULL DEFAULT 'queued',
    job_id VARCHAR(64) NOT NULL,
    task_id VARCHAR(64) NULL,
    agent_id VARCHAR(128) NULL,
    claimed_at DATETIME(6) NULL,
    heartbeat_at DATETIME(6) NULL,
    completed_at DATETIME(6) NULL,
    error_message TEXT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uk_screening_run_lock_scope (run_date, market, timeframe, chain_key),
    UNIQUE KEY uk_screening_run_lock_id (lock_id),
    KEY idx_screening_run_lock_job (job_id),
    KEY idx_screening_run_lock_status (status, run_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
COMMENT='全市场筛选每日分布式锁';

ALTER TABLE web_screening_jobs
    ADD COLUMN IF NOT EXISTS execution_mode VARCHAR(32) NOT NULL DEFAULT 'local_agent' AFTER error_message,
    ADD COLUMN IF NOT EXISTS agent_id VARCHAR(128) NULL AFTER execution_mode,
    ADD COLUMN IF NOT EXISTS claimed_at DATETIME(6) NULL AFTER agent_id,
    ADD COLUMN IF NOT EXISTS heartbeat_at DATETIME(6) NULL AFTER claimed_at,
    ADD COLUMN IF NOT EXISTS options_json JSON NULL AFTER heartbeat_at,
    ADD COLUMN IF NOT EXISTS summary_json JSON NULL AFTER options_json;

ALTER TABLE single_stock_runs
    ADD COLUMN IF NOT EXISTS chain_key VARCHAR(64) NULL AFTER timeframe,
    ADD COLUMN IF NOT EXISTS result_json JSON NULL AFTER ai_analysis_json,
    ADD COLUMN IF NOT EXISTS agent_id VARCHAR(128) NULL AFTER ai_analysis_json,
    ADD COLUMN IF NOT EXISTS claimed_at DATETIME(6) NULL AFTER agent_id,
    ADD COLUMN IF NOT EXISTS heartbeat_at DATETIME(6) NULL AFTER claimed_at,
    ADD COLUMN IF NOT EXISTS error_message TEXT NULL AFTER heartbeat_at;

ALTER TABLE screening_tasks
    ADD COLUMN IF NOT EXISTS passed_count INT NULL AFTER completed_count,
    ADD COLUMN IF NOT EXISTS failed_count INT NULL AFTER passed_count,
    ADD COLUMN IF NOT EXISTS uploaded_result_scope VARCHAR(32) NULL AFTER failed_count;

ALTER TABLE screening_run_locks
    ADD COLUMN IF NOT EXISTS chain_key VARCHAR(64) NOT NULL DEFAULT 'default_zuoyi_and_other' AFTER timeframe;

-- Existing deployments may still have the old unique key on (run_date, market, timeframe).
-- If your MySQL version does not support conditional index DDL, run these two statements manually
-- after checking SHOW INDEX FROM screening_run_locks.
ALTER TABLE screening_run_locks DROP INDEX uk_screening_run_lock_scope;
ALTER TABLE screening_run_locks
    ADD UNIQUE KEY uk_screening_run_lock_scope (run_date, market, timeframe, chain_key);
