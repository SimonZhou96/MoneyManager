CREATE TABLE IF NOT EXISTS screening_reports (
    report_id VARCHAR(191) NOT NULL,
    task_id VARCHAR(191) NOT NULL,
    task_type VARCHAR(32) NOT NULL,
    title VARCHAR(512) NOT NULL,
    summary VARCHAR(1024) NOT NULL,
    markets_json JSON NOT NULL,
    codes_json JSON NOT NULL,
    names_json JSON NOT NULL,
    passed_count INT UNSIGNED NOT NULL DEFAULT 0,
    generated_at DATETIME(6) NOT NULL,
    task_date DATE NOT NULL,
    timeframe VARCHAR(32) NOT NULL DEFAULT '',
    chain_key VARCHAR(128) NOT NULL DEFAULT '',
    content_json JSON NOT NULL,
    content_text MEDIUMTEXT NOT NULL,
    search_tokens MEDIUMTEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (report_id),
    UNIQUE KEY uk_screening_reports_task (task_type, task_id),
    KEY idx_screening_reports_generated (generated_at, report_id),
    FULLTEXT KEY ft_screening_reports_search (search_tokens)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='任务筛选报告与全文检索';
