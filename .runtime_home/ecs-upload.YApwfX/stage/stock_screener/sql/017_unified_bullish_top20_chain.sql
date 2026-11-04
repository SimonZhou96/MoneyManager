-- Optional unified bullish/rebound/left-one-bullish technical Top20 chain.
-- Existing metadata rows are updated to add direction and signal_group
-- needed by the dynamic scanner.

UPDATE screening_rule_metadata
SET strategy_category = 'technical',
    params_json = JSON_SET(COALESCE(params_json, JSON_OBJECT()), '$.direction', 'bullish', '$.signal_group', 'bullish')
WHERE rule_key IN (
    'ema_breakout', 'volume_spike_prior3', 'daily_rise_4_45',
    'bullish_engulfing', 'three_white_soldiers', 'bullish_marubozu',
    'sma_golden_cross', 'ema_golden_cross', 'macd_bullish_cross',
    'vwap_bullish_reclaim', 'atr_up_breakout', 'kdj_bullish_cross',
    'volume_price_breakout'
)
  AND rule_type = 'strategy'
  AND (strategy_category IS NULL OR strategy_category = '' OR strategy_category = 'technical');

UPDATE screening_rule_metadata
SET strategy_category = 'technical',
    params_json = JSON_SET(COALESCE(params_json, JSON_OBJECT()), '$.direction', 'bullish', '$.signal_group', 'rebound')
WHERE rule_key IN (
    'rsi_oversold', 'rsi_bullish_rebound', 'bollinger_lower_rebound',
    'hammer_reversal', 'morning_star', 'piercing_line'
)
  AND rule_type = 'strategy'
  AND (strategy_category IS NULL OR strategy_category = '' OR strategy_category = 'technical');

INSERT IGNORE INTO screening_rule_metadata
    (market, rule_key, rule_name, rule_type, strategy_category, implementation, params_json, enabled, display_order, description)
SELECT markets.market, 'zuoyi_bullish_signal', '左一战法-看涨', 'strategy', 'technical', 'ZuoYiStrategizer',
       '{"signal_window":15,"include_bullish":true,"include_bearish":false,"direction":"bullish","signal_group":"zuoyi_bullish"}',
       1, 115, '当前周期15根K线内左一战法看涨信号'
FROM (
    SELECT 'HK' AS market UNION ALL SELECT 'US' UNION ALL SELECT 'A'
) AS markets;

UPDATE screening_rule_metadata
SET strategy_category = 'technical',
    params_json = JSON_SET(
        COALESCE(params_json, JSON_OBJECT()),
        '$.signal_window', 15,
        '$.include_bullish', TRUE,
        '$.include_bearish', FALSE,
        '$.direction', 'bullish',
        '$.signal_group', 'zuoyi_bullish'
    )
WHERE rule_key = 'zuoyi_bullish_signal'
  AND rule_type = 'strategy';

INSERT IGNORE INTO screening_rule_metadata
    (market, rule_key, rule_name, rule_type, strategy_category, implementation, params_json, enabled, display_order, description)
SELECT markets.market, 'kdj_low_bullish_cross', '低位KDJ金叉', 'strategy', 'technical', 'TechnicalPatternStrategizer',
       '{"pattern_key":"kdj_low_bullish_cross","pattern_label":"低位KDJ金叉","direction":"bullish","display_group":"看涨规则","signal_group":"rebound","low_threshold":30.0}',
       1, 555, '准备反弹规则：低位KDJ金叉'
FROM (
    SELECT 'HK' AS market UNION ALL SELECT 'US' UNION ALL SELECT 'A'
) AS markets;

UPDATE screening_rule_metadata
SET strategy_category = 'technical',
    params_json = JSON_SET(COALESCE(params_json, JSON_OBJECT()), '$.direction', 'bullish', '$.signal_group', 'rebound', '$.low_threshold', 30.0)
WHERE rule_key = 'kdj_low_bullish_cross'
  AND rule_type = 'strategy';

INSERT IGNORE INTO screening_rule_chains
    (market, timeframe, chain_key, chain_name, expression_json, enabled, priority, description)
VALUES
    ('HK', '*', 'unified_bullish_top20', '统一看涨技术规则Top20',
     '{"ref":"ema_breakout"}',
     0, 400, '遍历所有启用看涨、准备反弹、左一看涨技术规则，按总命中数选Top20后进入AI复核'),
    ('US', '*', 'unified_bullish_top20', '统一看涨技术规则Top20',
     '{"ref":"ema_breakout"}',
     0, 400, '遍历所有启用看涨、准备反弹、左一看涨技术规则，按总命中数选Top20后进入AI复核'),
    ('A', '*', 'unified_bullish_top20', '统一看涨技术规则Top20',
     '{"ref":"ema_breakout"}',
     0, 400, '遍历所有启用看涨、准备反弹、左一看涨技术规则，按总命中数选Top20后进入AI复核');
