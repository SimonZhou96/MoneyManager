-- Rule-chain selection support for Web tasks and external Agent execution.
-- The full-market daily lock scope must include chain_key, otherwise a test
-- chain would be blocked by a completed default-chain run on the same day.

ALTER TABLE screening_run_locks
    ADD COLUMN IF NOT EXISTS chain_key VARCHAR(64) NOT NULL DEFAULT 'default_zuoyi_and_other' AFTER timeframe;

ALTER TABLE screening_run_locks
    ADD COLUMN IF NOT EXISTS pool_scope VARCHAR(255) NOT NULL DEFAULT 'best,major_index,industry_top5,recent_ipo_2y,all_etf' AFTER chain_key;

ALTER TABLE single_stock_runs
    ADD COLUMN IF NOT EXISTS chain_key VARCHAR(64) NULL AFTER timeframe;

-- Replace old unique scope (run_date, market, timeframe) with
-- (run_date, market, timeframe, chain_key, pool_scope).
ALTER TABLE screening_run_locks DROP INDEX uk_screening_run_lock_scope;
ALTER TABLE screening_run_locks
    ADD UNIQUE KEY uk_screening_run_lock_scope (run_date, market, timeframe, chain_key, pool_scope);

INSERT IGNORE INTO screening_rule_chains
    (market, timeframe, chain_key, chain_name, expression_json, enabled, priority, description)
VALUES
    ('HK', '*', 'trend_capital_accumulation_watch', '趋势主力缩量观察链',
     '{"and":[{"all_enabled":["market_cap_range","avg_daily_volume_range","price_range","pe_range","profitability"]},{"ref":"zuoyi_signal"},{"any_enabled":["ema_breakout","volume_spike_prior3","daily_rise_4_45"]}]}',
     0, 300, '默认关闭的试跑链：基于现有上涨趋势/放量规则做观察，主力资金与热点板块原子规则接入后可扩展'),
    ('US', '*', 'trend_capital_accumulation_watch', '趋势主力缩量观察链',
     '{"and":[{"all_enabled":["market_cap_range","avg_daily_volume_range","price_range","pe_range","profitability"]},{"ref":"zuoyi_signal"},{"any_enabled":["ema_breakout","volume_spike_prior3","daily_rise_4_45"]}]}',
     0, 300, '默认关闭的试跑链：基于现有上涨趋势/放量规则做观察，主力资金与热点板块原子规则接入后可扩展'),
    ('A', '*', 'trend_capital_accumulation_watch', '趋势主力缩量观察链',
     '{"and":[{"all_enabled":["market_cap_range","avg_daily_volume_range","price_range","pe_range","profitability"]},{"ref":"zuoyi_signal"},{"any_enabled":["ema_breakout","volume_spike_prior3","daily_rise_4_45"]}]}',
     0, 300, '默认关闭的试跑链：基于现有上涨趋势/放量规则做观察，主力资金与热点板块原子规则接入后可扩展');
