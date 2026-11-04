-- Add timeframe scope to screening rule chains.
-- Existing rows become wildcard chains and continue to apply to all timeframes.

ALTER TABLE screening_rule_chains
    ADD COLUMN IF NOT EXISTS timeframe VARCHAR(16) NOT NULL DEFAULT '*' COMMENT '适用周期，* 表示通用规则链' AFTER market;

ALTER TABLE screening_rule_chains DROP INDEX uk_rule_chains_market_key;
ALTER TABLE screening_rule_chains
    ADD UNIQUE KEY uk_rule_chains_market_timeframe_key (market, timeframe, chain_key);

ALTER TABLE screening_rule_chains DROP INDEX idx_rule_chains_market_enabled;
ALTER TABLE screening_rule_chains
    ADD KEY idx_rule_chains_market_timeframe_enabled (market, timeframe, enabled, priority);
