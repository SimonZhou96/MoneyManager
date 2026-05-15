-- 主力流出风险分析：主结果摘要字段 + 明细表

ALTER TABLE screening_results
  ADD COLUMN main_force_risk_level VARCHAR(16) NULL COMMENT 'low/medium/high/unknown',
  ADD COLUMN main_force_risk_score DECIMAL(6,2) NULL COMMENT '主力流出风险分 0-100，越高风险越大',
  ADD COLUMN main_force_risk_summary VARCHAR(512) NULL COMMENT '主力流出风险摘要',
  ADD COLUMN main_force_risk_signals JSON NULL COMMENT '触发的主要风险信号摘要',
  ADD COLUMN main_force_data_status JSON NULL COMMENT '资金/盘口/龙虎榜/筹码数据状态',
  ADD COLUMN main_force_risk_updated_at DATETIME(6) NULL COMMENT '主力流出风险更新时间',
  ADD KEY idx_screening_main_force_risk (main_force_risk_level, main_force_risk_score);

CREATE TABLE IF NOT EXISTS screening_main_force_risks (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  task_id VARCHAR(36) NOT NULL,
  market VARCHAR(8) NOT NULL,
  code VARCHAR(32) NOT NULL,
  name VARCHAR(255) NULL,
  check_date DATE NOT NULL,
  csv_path VARCHAR(1024) NULL,
  analysis_status VARCHAR(32) NOT NULL DEFAULT 'success',
  risk_level VARCHAR(16) NOT NULL DEFAULT 'unknown',
  risk_score DECIMAL(6,2) NULL,
  risk_summary TEXT NULL,
  triggered_signals JSON NULL COMMENT '触发信号列表',
  missing_data JSON NULL COMMENT '缺失/不适用/无权限数据项',
  provider_status JSON NULL COMMENT '各数据源状态',
  metrics_json JSON NULL COMMENT '归一化后的关键指标，不存完整盘口大对象',
  error_message TEXT NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uk_main_force_task_market_code (task_id, market, code),
  KEY idx_main_force_market_date (market, check_date),
  KEY idx_main_force_risk (risk_level, risk_score),
  KEY idx_main_force_status (analysis_status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
COMMENT='主力流出风险分析明细';
