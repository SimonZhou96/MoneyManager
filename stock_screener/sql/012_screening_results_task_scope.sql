-- Keep results isolated per screening task.
-- This prevents same-day 1d/5m or different rule-chain runs for the same stock
-- from overwriting each other's task_id in screening_results.

ALTER TABLE screening_results DROP INDEX uk_screening_market_code_date;
ALTER TABLE screening_results
    ADD UNIQUE KEY uk_screening_task_market_code (task_id, market, code);
