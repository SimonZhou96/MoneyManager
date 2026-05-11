-- Rule-chain selection support for Web tasks and local Agent execution.
-- The full-market daily lock scope must include chain_key, otherwise a test
-- chain would be blocked by a completed default-chain run on the same day.

ALTER TABLE screening_run_locks
    ADD COLUMN IF NOT EXISTS chain_key VARCHAR(64) NOT NULL DEFAULT 'default_zuoyi_and_other' AFTER timeframe;

ALTER TABLE single_stock_runs
    ADD COLUMN IF NOT EXISTS chain_key VARCHAR(64) NULL AFTER timeframe;

-- Replace old unique scope (run_date, market, timeframe) with
-- (run_date, market, timeframe, chain_key).
ALTER TABLE screening_run_locks DROP INDEX uk_screening_run_lock_scope;
ALTER TABLE screening_run_locks
    ADD UNIQUE KEY uk_screening_run_lock_scope (run_date, market, timeframe, chain_key);
