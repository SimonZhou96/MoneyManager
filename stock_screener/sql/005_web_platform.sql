-- Web stock screener, external Agent ingestion, K-line cache, artifacts, and single-stock analysis.
-- Safe to run multiple times.

CREATE TABLE IF NOT EXISTS web_users (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    username VARCHAR(128) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(32) NOT NULL DEFAULT 'admin',
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uk_web_users_username (username),
    KEY idx_web_users_active (is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Web fixed login users';

CREATE TABLE IF NOT EXISTS web_sessions (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    session_hash CHAR(64) NOT NULL,
    user_id BIGINT UNSIGNED NOT NULL,
    expires_at DATETIME(6) NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    last_seen_at DATETIME(6) NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_web_sessions_hash (session_hash),
    KEY idx_web_sessions_user (user_id),
    KEY idx_web_sessions_expires (expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Web server sessions';

CREATE TABLE IF NOT EXISTS web_login_attempts (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    username VARCHAR(128) NOT NULL,
    ip_address VARCHAR(64) NOT NULL,
    success TINYINT(1) NOT NULL DEFAULT 0,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    KEY idx_login_attempts_lookup (username, ip_address, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Web login attempts';

CREATE TABLE IF NOT EXISTS web_screening_jobs (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    job_id VARCHAR(64) NOT NULL,
    user_id BIGINT UNSIGNED NULL,
    markets JSON NULL,
    timeframe VARCHAR(8) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'queued',
    task_ids JSON NULL,
    error_message TEXT NULL,
    execution_mode VARCHAR(32) NOT NULL DEFAULT 'web_backend',
    agent_id VARCHAR(128) NULL,
    claimed_at DATETIME(6) NULL,
    heartbeat_at DATETIME(6) NULL,
    options_json JSON NULL,
    summary_json JSON NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    finished_at DATETIME(6) NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_web_screening_job_id (job_id),
    KEY idx_web_screening_jobs_user (user_id, created_at),
    KEY idx_web_screening_jobs_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Web-created screening job groups';

CREATE TABLE IF NOT EXISTS data_sync_runs (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    sync_run_id VARCHAR(64) NOT NULL,
    agent_id VARCHAR(128) NULL,
    markets JSON NULL,
    timeframes JSON NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'running',
    started_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    finished_at DATETIME(6) NULL,
    stock_pool_rows INT NOT NULL DEFAULT 0,
    sector_rows INT NOT NULL DEFAULT 0,
    kline_rows INT NOT NULL DEFAULT 0,
    error_message TEXT NULL,
    metadata_json JSON NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uk_data_sync_runs_id (sync_run_id),
    KEY idx_data_sync_status (status),
    KEY idx_data_sync_started (started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='External Agent sync runs';

CREATE TABLE IF NOT EXISTS stock_kline_cache (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    market VARCHAR(8) NOT NULL,
    code VARCHAR(32) NOT NULL,
    timeframe VARCHAR(8) NOT NULL,
    bar_time DATETIME(6) NOT NULL,
    open DECIMAL(20,6) NULL,
    high DECIMAL(20,6) NULL,
    low DECIMAL(20,6) NULL,
    close DECIMAL(20,6) NULL,
    volume DECIMAL(28,6) NULL,
    turnover DECIMAL(28,6) NULL,
    source VARCHAR(32) NOT NULL DEFAULT 'opend_cache',
    sync_run_id VARCHAR(64) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uk_kline_cache_bar (market, code, timeframe, bar_time),
    KEY idx_kline_cache_lookup (market, code, timeframe, bar_time),
    KEY idx_kline_cache_source (source),
    KEY idx_kline_cache_sync_run (sync_run_id),
    KEY idx_kline_cache_updated (updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Cloud K-line cache';

CREATE TABLE IF NOT EXISTS screening_artifacts (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    artifact_id VARCHAR(64) NOT NULL,
    task_id VARCHAR(64) NOT NULL,
    market VARCHAR(8) NULL,
    artifact_type VARCHAR(32) NOT NULL,
    file_name VARCHAR(255) NOT NULL,
    file_path VARCHAR(1024) NOT NULL,
    content_type VARCHAR(128) NULL,
    file_size BIGINT NULL,
    checksum VARCHAR(128) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uk_screening_artifact_id (artifact_id),
    KEY idx_screening_artifacts_task (task_id),
    KEY idx_screening_artifacts_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Screening artifact metadata';

CREATE TABLE IF NOT EXISTS single_stock_runs (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    run_id VARCHAR(64) NOT NULL,
    user_id BIGINT UNSIGNED NULL,
    market VARCHAR(8) NOT NULL,
    code VARCHAR(32) NOT NULL,
    normalized_code VARCHAR(32) NOT NULL,
    timeframe VARCHAR(8) NOT NULL,
    chain_key VARCHAR(64) NULL,
    passed TINYINT(1) NOT NULL DEFAULT 0,
    data_source VARCHAR(32) NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'running',
    warnings_json JSON NULL,
    ai_analysis_json JSON NULL,
    result_json JSON NULL,
    agent_id VARCHAR(128) NULL,
    claimed_at DATETIME(6) NULL,
    heartbeat_at DATETIME(6) NULL,
    error_message TEXT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    finished_at DATETIME(6) NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_single_stock_run_id (run_id),
    KEY idx_single_stock_user (user_id, created_at),
    KEY idx_single_stock_code (market, normalized_code, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Single-stock screening runs';

CREATE TABLE IF NOT EXISTS single_stock_rule_details (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    run_id VARCHAR(64) NOT NULL,
    rule_key VARCHAR(64) NULL,
    rule_name VARCHAR(128) NOT NULL,
    rule_type VARCHAR(16) NULL,
    result VARCHAR(16) NOT NULL,
    reason TEXT NULL,
    details_json JSON NULL,
    display_order INT NOT NULL DEFAULT 0,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    KEY idx_single_rule_run (run_id, display_order)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Single-stock rule details';

CREATE TABLE IF NOT EXISTS screening_run_locks (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    lock_id VARCHAR(64) NOT NULL,
    run_date DATE NOT NULL,
    market VARCHAR(8) NOT NULL,
    timeframe VARCHAR(8) NOT NULL,
    chain_key VARCHAR(64) NOT NULL DEFAULT 'default_zuoyi_and_other',
    pool_scope VARCHAR(255) NOT NULL DEFAULT 'best,major_index,industry_top5,recent_ipo_2y,all_etf',
    status VARCHAR(32) NOT NULL DEFAULT 'queued',
    job_id VARCHAR(64) NOT NULL,
    task_id VARCHAR(64) NULL,
    agent_id VARCHAR(128) NULL,
    claimed_at DATETIME(6) NULL,
    heartbeat_at DATETIME(6) NULL,
    completed_at DATETIME(6) NULL,
    error_message TEXT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uk_screening_run_lock_scope (run_date, market, timeframe, chain_key, pool_scope),
    UNIQUE KEY uk_screening_run_lock_id (lock_id),
    KEY idx_screening_run_lock_job (job_id),
    KEY idx_screening_run_lock_status (status, run_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Full-market screening daily distributed locks';
