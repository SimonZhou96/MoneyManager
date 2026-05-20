-- 美股默认基础门槛。
-- 不新增筛选器，复用 avg_daily_volume_range，在 US 参数里切换为成交额模式。

UPDATE screening_rule_metadata
SET params_json='{"min_cap": 5000000000, "max_cap": null, "min_exclusive": true}'
WHERE market='US' AND rule_key='market_cap_range';

UPDATE screening_rule_metadata
SET params_json='{"min_price": 5, "max_price": null, "min_exclusive": true}'
WHERE market='US' AND rule_key='price_range';

UPDATE screening_rule_metadata
SET params_json='{"min_pe": 5, "max_pe": null, "allow_negative": false, "min_exclusive": true}'
WHERE market='US' AND rule_key='pe_range';

UPDATE screening_rule_metadata
SET params_json='{"min_volume": 20000000, "max_volume": null, "lookback_days": 10, "metric": "turnover", "min_exclusive": true}',
    rule_name='10天平均成交额范围',
    description='复用每日平均交易量规则，按 K 线计算最近10天平均成交额'
WHERE market='US' AND rule_key='avg_daily_volume_range';

UPDATE screening_rule_chains
SET expression_json='{"and":[{"all_enabled":["market_cap_range","avg_daily_volume_range","price_range","pe_range","profitability"]},{"ref":"zuoyi_signal"},{"any_enabled":["ema_breakout","rsi_oversold","rsi_overbought","volume_spike_prior3","daily_drop_6_65","daily_rise_4_45"]}]}'
WHERE market='US' AND timeframe='*' AND chain_key='default_zuoyi_and_other';
