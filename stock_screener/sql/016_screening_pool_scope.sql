-- Add selected stock-pool types to the full-market screening lock scope.
-- This allows different pool combinations to run independently on the same day.

ALTER TABLE screening_run_locks
    ADD COLUMN IF NOT EXISTS pool_scope VARCHAR(255) NOT NULL DEFAULT 'best,major_index,industry_top5,recent_ipo_2y,all_etf' AFTER chain_key;

ALTER TABLE screening_run_locks DROP INDEX uk_screening_run_lock_scope;

ALTER TABLE screening_run_locks
    ADD UNIQUE KEY uk_screening_run_lock_scope (run_date, market, timeframe, chain_key, pool_scope);
