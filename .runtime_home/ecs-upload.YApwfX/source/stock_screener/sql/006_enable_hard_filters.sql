-- Enable default hard filters for existing HK/US/A rule metadata.
-- Empty params still return SKIP/PASS and do not block the rule chain.

UPDATE screening_rule_metadata
SET enabled = 1
WHERE market IN ('HK', 'US', 'A')
  AND rule_key IN (
    'market_cap_range',
    'avg_daily_volume_range',
    'price_range',
    'pe_range',
    'profitability'
  );
