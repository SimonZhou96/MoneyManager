CREATE TABLE IF NOT EXISTS quant_backtest_runs (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    run_id VARCHAR(64) NOT NULL,
    user_id BIGINT UNSIGNED NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'running',
    request_json JSON NULL,
    rule_chain_snapshot_json JSON NULL,
    metrics_json JSON NULL,
    warnings_json JSON NULL,
    error_message TEXT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    finished_at DATETIME(6) NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_quant_backtest_run_id (run_id),
    KEY idx_quant_backtest_user (user_id, created_at),
    KEY idx_quant_backtest_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='量化实验室回测任务';

CREATE TABLE IF NOT EXISTS quant_signal_events (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    run_id VARCHAR(64) NOT NULL,
    signal_id VARCHAR(64) NOT NULL,
    market VARCHAR(8) NOT NULL,
    symbol VARCHAR(32) NOT NULL,
    signal_time DATETIME(6) NOT NULL,
    direction VARCHAR(16) NOT NULL,
    reason TEXT NULL,
    strength DECIMAL(10,4) NULL,
    rule_chain_key VARCHAR(128) NULL,
    rule_result_json JSON NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_quant_signal_id (signal_id),
    KEY idx_quant_signal_run (run_id),
    KEY idx_quant_signal_symbol (market, symbol, signal_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='量化信号事件';

CREATE TABLE IF NOT EXISTS quant_backtest_orders (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    run_id VARCHAR(64) NOT NULL,
    order_id VARCHAR(64) NOT NULL,
    signal_id VARCHAR(64) NULL,
    symbol VARCHAR(32) NOT NULL,
    side VARCHAR(16) NOT NULL,
    quantity INT NOT NULL,
    order_type VARCHAR(16) NOT NULL,
    limit_price DECIMAL(20,6) NULL,
    status VARCHAR(32) NOT NULL,
    rejected_reason VARCHAR(255) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uk_quant_order_id (order_id),
    KEY idx_quant_order_run (run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='量化回测模拟订单';

CREATE TABLE IF NOT EXISTS quant_backtest_trades (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    run_id VARCHAR(64) NOT NULL,
    trade_id VARCHAR(64) NOT NULL,
    order_id VARCHAR(64) NOT NULL,
    symbol VARCHAR(32) NOT NULL,
    side VARCHAR(16) NOT NULL,
    quantity INT NOT NULL,
    price DECIMAL(20,6) NOT NULL,
    fee DECIMAL(20,6) NOT NULL DEFAULT 0,
    slippage DECIMAL(20,6) NOT NULL DEFAULT 0,
    traded_at DATETIME(6) NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_quant_trade_id (trade_id),
    KEY idx_quant_trade_run (run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='量化回测模拟成交';

CREATE TABLE IF NOT EXISTS quant_equity_curve (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    run_id VARCHAR(64) NOT NULL,
    point_time DATETIME(6) NOT NULL,
    equity DECIMAL(20,6) NOT NULL,
    cash DECIMAL(20,6) NOT NULL,
    drawdown DECIMAL(20,8) NOT NULL DEFAULT 0,
    benchmark_value DECIMAL(20,6) NULL,
    PRIMARY KEY (id),
    KEY idx_quant_equity_run (run_id, point_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='量化回测净值曲线';

CREATE TABLE IF NOT EXISTS quant_paper_accounts (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    account_id VARCHAR(64) NOT NULL,
    user_id BIGINT UNSIGNED NULL,
    name VARCHAR(128) NOT NULL,
    cash DECIMAL(20,6) NOT NULL,
    equity DECIMAL(20,6) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    config_json JSON NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uk_quant_paper_account_id (account_id),
    KEY idx_quant_paper_user (user_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='量化纸面交易账户';
