-- 选股结果搜索与模型辅助分析表
-- 该表只存储辅助判断，不参与筛选通过/失败计算。

CREATE TABLE IF NOT EXISTS screening_signal_analysis (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    task_id VARCHAR(36) NOT NULL COMMENT '筛选任务 ID',
    market VARCHAR(8) NOT NULL COMMENT '市场: HK/US/A',
    code VARCHAR(32) NOT NULL COMMENT '股票代码',
    name VARCHAR(255) NULL COMMENT '股票名称',
    check_date DATE NOT NULL COMMENT '筛选日期',
    csv_path VARCHAR(1024) NULL COMMENT '来源 CSV 路径',
    analysis_status VARCHAR(32) NOT NULL DEFAULT 'success' COMMENT 'success/error/skipped',
    reliability_score DECIMAL(6,2) NULL COMMENT '信号可靠性评分 0-100',
    confidence_score DECIMAL(6,2) NULL COMMENT '模型置信度 0-100',
    signal_bias VARCHAR(32) NULL COMMENT 'bullish/bearish/neutral/avoid/unknown',
    summary TEXT NULL COMMENT '模型摘要',
    positive_factors JSON NULL COMMENT '利好因素',
    risk_factors JSON NULL COMMENT '风险因素',
    macro_factors JSON NULL COMMENT '宏观/政策因素',
    company_events JSON NULL COMMENT '公司事件',
    hot_sectors JSON NULL COMMENT '识别到的热点板块',
    hot_sector_mark VARCHAR(32) NULL COMMENT '重点/相关/观察/无明确关联/未知',
    matched_hot_sectors JSON NULL COMMENT '匹配到的热点板块',
    hot_sector_relevance VARCHAR(64) NULL COMMENT '热点板块关联度',
    hot_sector_reason TEXT NULL COMMENT '热点板块匹配理由',
    hot_sector_sources JSON NULL COMMENT '热点板块来源',
    source_urls JSON NULL COMMENT '信息来源 URL',
    model VARCHAR(128) NULL COMMENT '模型名',
    raw_response JSON NULL COMMENT '模型原始结构化响应',
    error_message TEXT NULL COMMENT '错误信息',
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uk_signal_analysis_task_market_code (task_id, market, code),
    KEY idx_signal_analysis_market_date (market, check_date),
    KEY idx_signal_analysis_score (reliability_score),
    KEY idx_signal_analysis_status (analysis_status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
COMMENT='选股结果搜索与模型辅助分析';
