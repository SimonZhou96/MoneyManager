-- 常用蜡烛图、K线与辅助线原子规则。
-- 仅新增原子规则，不修改默认规则链，避免默认筛选口径突然放宽。

INSERT IGNORE INTO screening_rule_metadata
    (market, rule_key, rule_name, rule_type, strategy_category, implementation, params_json, enabled, display_order, description)
SELECT markets.market, rules.rule_key, rules.rule_name, 'strategy', 'technical', 'TechnicalPatternStrategizer',
       rules.params_json, 1, rules.display_order, rules.description
FROM (
    SELECT 'HK' AS market UNION ALL SELECT 'US' UNION ALL SELECT 'A'
) AS markets
CROSS JOIN (
    SELECT 'bullish_engulfing' AS rule_key, '看涨吞没' AS rule_name, '{"pattern_key":"bullish_engulfing","pattern_label":"看涨吞没","direction":"bullish","display_group":"看涨规则"}' AS params_json, 300 AS display_order, '看涨规则：看涨吞没' AS description UNION ALL
    SELECT 'bearish_engulfing', '看跌吞没', '{"pattern_key":"bearish_engulfing","pattern_label":"看跌吞没","direction":"bearish","display_group":"看跌规则"}', 310, '看跌规则：看跌吞没' UNION ALL
    SELECT 'hammer_reversal', '锤子线反转', '{"pattern_key":"hammer_reversal","pattern_label":"锤子线反转","direction":"bullish","display_group":"看涨规则"}', 320, '看涨规则：锤子线反转' UNION ALL
    SELECT 'shooting_star_reversal', '射击之星反转', '{"pattern_key":"shooting_star_reversal","pattern_label":"射击之星反转","direction":"bearish","display_group":"看跌规则"}', 330, '看跌规则：射击之星反转' UNION ALL
    SELECT 'morning_star', '早晨之星', '{"pattern_key":"morning_star","pattern_label":"早晨之星","direction":"bullish","display_group":"看涨规则"}', 340, '看涨规则：早晨之星' UNION ALL
    SELECT 'evening_star', '黄昏之星', '{"pattern_key":"evening_star","pattern_label":"黄昏之星","direction":"bearish","display_group":"看跌规则"}', 350, '看跌规则：黄昏之星' UNION ALL
    SELECT 'piercing_line', '曙光初现', '{"pattern_key":"piercing_line","pattern_label":"曙光初现","direction":"bullish","display_group":"看涨规则"}', 360, '看涨规则：曙光初现' UNION ALL
    SELECT 'dark_cloud_cover', '乌云盖顶', '{"pattern_key":"dark_cloud_cover","pattern_label":"乌云盖顶","direction":"bearish","display_group":"看跌规则"}', 370, '看跌规则：乌云盖顶' UNION ALL
    SELECT 'three_white_soldiers', '红三兵', '{"pattern_key":"three_white_soldiers","pattern_label":"红三兵","direction":"bullish","display_group":"看涨规则"}', 380, '看涨规则：红三兵' UNION ALL
    SELECT 'three_black_crows', '三只乌鸦', '{"pattern_key":"three_black_crows","pattern_label":"三只乌鸦","direction":"bearish","display_group":"看跌规则"}', 390, '看跌规则：三只乌鸦' UNION ALL
    SELECT 'doji_indecision', '十字星', '{"pattern_key":"doji_indecision","pattern_label":"十字星","direction":"neutral","display_group":"中性规则"}', 400, '中性规则：十字星' UNION ALL
    SELECT 'bullish_marubozu', '看涨光头光脚', '{"pattern_key":"bullish_marubozu","pattern_label":"看涨光头光脚","direction":"bullish","display_group":"看涨规则"}', 410, '看涨规则：看涨光头光脚' UNION ALL
    SELECT 'bearish_marubozu', '看跌光头光脚', '{"pattern_key":"bearish_marubozu","pattern_label":"看跌光头光脚","direction":"bearish","display_group":"看跌规则"}', 420, '看跌规则：看跌光头光脚' UNION ALL
    SELECT 'sma_golden_cross', '均线金叉', '{"pattern_key":"sma_golden_cross","pattern_label":"均线金叉","direction":"bullish","display_group":"看涨规则"}', 430, '看涨规则：均线金叉' UNION ALL
    SELECT 'sma_death_cross', '均线死叉', '{"pattern_key":"sma_death_cross","pattern_label":"均线死叉","direction":"bearish","display_group":"看跌规则"}', 440, '看跌规则：均线死叉' UNION ALL
    SELECT 'ema_golden_cross', 'EMA金叉', '{"pattern_key":"ema_golden_cross","pattern_label":"EMA金叉","direction":"bullish","display_group":"看涨规则"}', 450, '看涨规则：EMA金叉' UNION ALL
    SELECT 'ema_death_cross', 'EMA死叉', '{"pattern_key":"ema_death_cross","pattern_label":"EMA死叉","direction":"bearish","display_group":"看跌规则"}', 460, '看跌规则：EMA死叉' UNION ALL
    SELECT 'macd_bullish_cross', 'MACD金叉', '{"pattern_key":"macd_bullish_cross","pattern_label":"MACD金叉","direction":"bullish","display_group":"看涨规则"}', 470, '看涨规则：MACD金叉' UNION ALL
    SELECT 'macd_bearish_cross', 'MACD死叉', '{"pattern_key":"macd_bearish_cross","pattern_label":"MACD死叉","direction":"bearish","display_group":"看跌规则"}', 480, '看跌规则：MACD死叉' UNION ALL
    SELECT 'bollinger_lower_rebound', '布林下轨反弹', '{"pattern_key":"bollinger_lower_rebound","pattern_label":"布林下轨反弹","direction":"bullish","display_group":"看涨规则"}', 490, '看涨规则：布林下轨反弹' UNION ALL
    SELECT 'bollinger_upper_rejection', '布林上轨回落', '{"pattern_key":"bollinger_upper_rejection","pattern_label":"布林上轨回落","direction":"bearish","display_group":"看跌规则"}', 500, '看跌规则：布林上轨回落' UNION ALL
    SELECT 'vwap_bullish_reclaim', '成交量加权均价上穿', '{"pattern_key":"vwap_bullish_reclaim","pattern_label":"成交量加权均价上穿","direction":"bullish","display_group":"看涨规则"}', 510, '看涨规则：成交量加权均价上穿' UNION ALL
    SELECT 'vwap_bearish_loss', '成交量加权均价下破', '{"pattern_key":"vwap_bearish_loss","pattern_label":"成交量加权均价下破","direction":"bearish","display_group":"看跌规则"}', 520, '看跌规则：成交量加权均价下破' UNION ALL
    SELECT 'atr_up_breakout', 'ATR向上突破', '{"pattern_key":"atr_up_breakout","pattern_label":"ATR向上突破","direction":"bullish","display_group":"看涨规则"}', 530, '看涨规则：ATR向上突破' UNION ALL
    SELECT 'atr_down_breakdown', 'ATR向下跌破', '{"pattern_key":"atr_down_breakdown","pattern_label":"ATR向下跌破","direction":"bearish","display_group":"看跌规则"}', 540, '看跌规则：ATR向下跌破' UNION ALL
    SELECT 'kdj_bullish_cross', 'KDJ金叉', '{"pattern_key":"kdj_bullish_cross","pattern_label":"KDJ金叉","direction":"bullish","display_group":"看涨规则"}', 550, '看涨规则：KDJ金叉' UNION ALL
    SELECT 'kdj_bearish_cross', 'KDJ死叉', '{"pattern_key":"kdj_bearish_cross","pattern_label":"KDJ死叉","direction":"bearish","display_group":"看跌规则"}', 560, '看跌规则：KDJ死叉' UNION ALL
    SELECT 'rsi_bullish_rebound', 'RSI超卖回升', '{"pattern_key":"rsi_bullish_rebound","pattern_label":"RSI超卖回升","direction":"bullish","display_group":"看涨规则"}', 570, '看涨规则：RSI超卖回升' UNION ALL
    SELECT 'rsi_bearish_pullback', 'RSI超买回落', '{"pattern_key":"rsi_bearish_pullback","pattern_label":"RSI超买回落","direction":"bearish","display_group":"看跌规则"}', 580, '看跌规则：RSI超买回落' UNION ALL
    SELECT 'volume_price_breakout', '放量突破', '{"pattern_key":"volume_price_breakout","pattern_label":"放量突破","direction":"bullish","display_group":"看涨规则"}', 590, '看涨规则：放量突破' UNION ALL
    SELECT 'volume_price_breakdown', '放量跌破', '{"pattern_key":"volume_price_breakdown","pattern_label":"放量跌破","direction":"bearish","display_group":"看跌规则"}', 600, '看跌规则：放量跌破'
) AS rules;

INSERT IGNORE INTO screening_rule_metadata
    (market, rule_key, rule_name, rule_type, strategy_category, implementation, params_json, enabled, display_order, description)
SELECT markets.market, 'kdj_low_bullish_cross', '低位KDJ金叉', 'strategy', 'technical', 'TechnicalPatternStrategizer',
       '{"pattern_key":"kdj_low_bullish_cross","pattern_label":"低位KDJ金叉","direction":"bullish","display_group":"看涨规则","signal_group":"rebound","low_threshold":30.0}',
       1, 555, '准备反弹规则：低位KDJ金叉'
FROM (
    SELECT 'HK' AS market UNION ALL SELECT 'US' UNION ALL SELECT 'A'
) AS markets;

UPDATE screening_rule_metadata
SET params_json = JSON_SET(COALESCE(params_json, JSON_OBJECT()), '$.signal_group', 'bullish')
WHERE rule_key IN (
    'bullish_engulfing', 'three_white_soldiers', 'bullish_marubozu',
    'sma_golden_cross', 'ema_golden_cross', 'macd_bullish_cross',
    'vwap_bullish_reclaim', 'atr_up_breakout', 'kdj_bullish_cross',
    'volume_price_breakout'
)
  AND rule_type = 'strategy'
  AND JSON_UNQUOTE(JSON_EXTRACT(params_json, '$.direction')) = 'bullish';

UPDATE screening_rule_metadata
SET params_json = JSON_SET(COALESCE(params_json, JSON_OBJECT()), '$.signal_group', 'rebound')
WHERE rule_key IN (
    'rsi_bullish_rebound', 'bollinger_lower_rebound', 'hammer_reversal',
    'morning_star', 'piercing_line', 'kdj_low_bullish_cross'
)
  AND rule_type = 'strategy'
  AND JSON_UNQUOTE(JSON_EXTRACT(params_json, '$.direction')) = 'bullish';
