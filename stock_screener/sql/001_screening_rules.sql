-- 数据库化筛选规则引擎表结构与默认规则链
-- 可在项目部署建库时直接执行；INSERT IGNORE 不覆盖已有配置。

CREATE TABLE IF NOT EXISTS screening_rule_metadata (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    market VARCHAR(8) NOT NULL COMMENT '市场: HK/US/A',
    rule_key VARCHAR(64) NOT NULL COMMENT '原子规则键',
    rule_name VARCHAR(128) NOT NULL COMMENT '规则展示名称',
    rule_type VARCHAR(16) NOT NULL COMMENT 'filter/strategy',
    strategy_category VARCHAR(16) NULL COMMENT 'technical/macro',
    implementation VARCHAR(128) NOT NULL COMMENT '代码侧白名单实现名',
    params_json JSON NULL COMMENT '规则参数',
    enabled TINYINT(1) NOT NULL DEFAULT 1 COMMENT '是否启用',
    display_order INT NOT NULL DEFAULT 100 COMMENT '执行展示顺序',
    description VARCHAR(512) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uk_rule_metadata_market_key (market, rule_key),
    KEY idx_rule_metadata_market_enabled (market, enabled),
    KEY idx_rule_metadata_type (rule_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
COMMENT='筛选原子规则元数据';

CREATE TABLE IF NOT EXISTS screening_rule_chains (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    market VARCHAR(8) NOT NULL COMMENT '市场: HK/US/A',
    timeframe VARCHAR(16) NOT NULL DEFAULT '*' COMMENT '适用周期，* 表示通用规则链',
    chain_key VARCHAR(64) NOT NULL COMMENT '规则链键',
    chain_name VARCHAR(128) NOT NULL COMMENT '规则链名称',
    expression_json JSON NOT NULL COMMENT '规则链 JSON DSL',
    enabled TINYINT(1) NOT NULL DEFAULT 1 COMMENT '是否启用',
    priority INT NOT NULL DEFAULT 100 COMMENT '优先级，越小越优先',
    description VARCHAR(512) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uk_rule_chains_market_timeframe_key (market, timeframe, chain_key),
    KEY idx_rule_chains_market_timeframe_enabled (market, timeframe, enabled, priority)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
COMMENT='筛选规则使用链';

INSERT IGNORE INTO screening_rule_metadata
    (market, rule_key, rule_name, rule_type, strategy_category, implementation, params_json, enabled, display_order, description)
VALUES
    ('HK', 'market_cap_range', '市值范围', 'filter', '', 'MarketCapFilter', '{"min_cap": null, "max_cap": null}', 1, 10, '按市值上下限筛选'),
    ('HK', 'avg_daily_volume_range', '每日平均交易量范围', 'filter', '', 'AvgDailyVolumeFilter', '{"min_volume": null, "max_volume": null}', 1, 20, '按 K 线计算每日平均交易量'),
    ('HK', 'price_range', '价格范围', 'filter', '', 'PriceFilter', '{"min_price": null, "max_price": null}', 1, 30, '按最新收盘价筛选'),
    ('HK', 'pe_range', 'PE 范围', 'filter', '', 'PEFilter', '{"min_pe": null, "max_pe": null, "allow_negative": false}', 1, 40, '按 PE 上下限筛选'),
    ('HK', 'profitability', '公司盈利', 'filter', '', 'ProfitabilityFilter', '{"require_profitable": true}', 1, 50, '要求 PE 为正'),
    ('HK', 'zuoyi_signal', '左一战法', 'strategy', 'technical', 'ZuoYiStrategizer', '{"signal_window": 15, "include_bullish": true, "include_bearish": true}', 1, 110, '当前周期15根K线内左一战法看涨/看跌信号'),
    ('HK', 'ema_breakout', 'EMA 突破', 'strategy', 'technical', 'EMABreakoutStrategizer', '{"ema_short": 10, "ema_long": 150, "direction": "bullish"}', 1, 120, 'EMA 短线向上突破长线'),
    ('HK', 'rsi_oversold', 'RSI 超卖', 'strategy', 'technical', 'RSIOversoldStrategizer', '{"period": 14, "threshold": 30.0, "direction": "bullish"}', 1, 130, 'RSI 低于等于阈值'),
    ('HK', 'rsi_overbought', 'RSI 超买', 'strategy', 'technical', 'RSIOverboughtStrategizer', '{"period": 14, "threshold": 70.0}', 1, 140, 'RSI 高于等于阈值'),
    ('HK', 'volume_spike_prior3', '放量超前三日', 'strategy', 'technical', 'TodayVolumeExceedsPrior3MaxStrategizer', '{"direction": "bullish"}', 1, 150, '当日成交量大于前三日最大值'),
    ('HK', 'daily_drop_6_65', '当日跌 6%~6.5%', 'strategy', 'technical', 'DailyDrop6To65Strategizer', '{"pct_min": -6.5, "pct_max": -6.0}', 1, 160, '当日跌幅在指定区间'),
    ('HK', 'daily_rise_4_45', '当日涨 4%~4.5%', 'strategy', 'technical', 'DailyRise4To45Strategizer', '{"pct_min": 4.0, "pct_max": 4.5, "direction": "bullish"}', 1, 170, '当日涨幅在指定区间'),
    ('HK', 'company_event_hot_sector_link', '公司时事与热点板块关联', 'strategy', 'macro', 'CompanyEventHotSectorStrategizer', '{}', 1, 210, '复用 AI 分析结果，判断公司时事是否与热点板块形成共振'),
    ('HK', 'company_event_hot_news_link', '公司时事与热点新闻关联', 'strategy', 'macro', 'CompanyEventHotNewsStrategizer', '{}', 1, 220, '复用 AI 分析结果，判断公司时事是否被热点新闻验证'),
    ('HK', 'market_intel_macro_score_link', '市场情报宏观评分', 'strategy', 'macro', 'MarketIntelMacroScoreStrategizer', '{"threshold": 60, "refresh_policy": "cache_or_refresh", "technical_weight": 0.6, "macro_weight": 0.4}', 1, 230, '基于公司事件、热点板块与新闻证据生成时间感知宏观评分'),

    ('US', 'market_cap_range', '市值范围', 'filter', '', 'MarketCapFilter', '{"min_cap": 5000000000, "max_cap": null, "min_exclusive": true}', 1, 10, '按市值上下限筛选'),
    ('US', 'avg_daily_volume_range', '10天平均成交额范围', 'filter', '', 'AvgDailyVolumeFilter', '{"min_volume": 20000000, "max_volume": null, "lookback_days": 10, "metric": "turnover", "min_exclusive": true}', 1, 20, '复用每日平均交易量规则，按 K 线计算最近10天平均成交额'),
    ('US', 'price_range', '价格范围', 'filter', '', 'PriceFilter', '{"min_price": 5, "max_price": null, "min_exclusive": true}', 1, 30, '按最新收盘价筛选'),
    ('US', 'pe_range', 'PE 范围', 'filter', '', 'PEFilter', '{"min_pe": 5, "max_pe": null, "allow_negative": false, "min_exclusive": true}', 1, 40, '按 PE 上下限筛选'),
    ('US', 'profitability', '公司盈利', 'filter', '', 'ProfitabilityFilter', '{"require_profitable": true}', 1, 50, '要求 PE 为正'),
    ('US', 'zuoyi_signal', '左一战法', 'strategy', 'technical', 'ZuoYiStrategizer', '{"signal_window": 15, "include_bullish": true, "include_bearish": true}', 1, 110, '当前周期15根K线内左一战法看涨/看跌信号'),
    ('US', 'ema_breakout', 'EMA 突破', 'strategy', 'technical', 'EMABreakoutStrategizer', '{"ema_short": 10, "ema_long": 150, "direction": "bullish"}', 1, 120, 'EMA 短线向上突破长线'),
    ('US', 'rsi_oversold', 'RSI 超卖', 'strategy', 'technical', 'RSIOversoldStrategizer', '{"period": 14, "threshold": 30.0, "direction": "bullish"}', 1, 130, 'RSI 低于等于阈值'),
    ('US', 'rsi_overbought', 'RSI 超买', 'strategy', 'technical', 'RSIOverboughtStrategizer', '{"period": 14, "threshold": 70.0}', 1, 140, 'RSI 高于等于阈值'),
    ('US', 'volume_spike_prior3', '放量超前三日', 'strategy', 'technical', 'TodayVolumeExceedsPrior3MaxStrategizer', '{"direction": "bullish"}', 1, 150, '当日成交量大于前三日最大值'),
    ('US', 'daily_drop_6_65', '当日跌 6%~6.5%', 'strategy', 'technical', 'DailyDrop6To65Strategizer', '{"pct_min": -6.5, "pct_max": -6.0}', 1, 160, '当日跌幅在指定区间'),
    ('US', 'daily_rise_4_45', '当日涨 4%~4.5%', 'strategy', 'technical', 'DailyRise4To45Strategizer', '{"pct_min": 4.0, "pct_max": 4.5, "direction": "bullish"}', 1, 170, '当日涨幅在指定区间'),
    ('US', 'company_event_hot_sector_link', '公司时事与热点板块关联', 'strategy', 'macro', 'CompanyEventHotSectorStrategizer', '{}', 1, 210, '复用 AI 分析结果，判断公司时事是否与热点板块形成共振'),
    ('US', 'company_event_hot_news_link', '公司时事与热点新闻关联', 'strategy', 'macro', 'CompanyEventHotNewsStrategizer', '{}', 1, 220, '复用 AI 分析结果，判断公司时事是否被热点新闻验证'),
    ('US', 'market_intel_macro_score_link', '市场情报宏观评分', 'strategy', 'macro', 'MarketIntelMacroScoreStrategizer', '{"threshold": 60, "refresh_policy": "cache_or_refresh", "technical_weight": 0.6, "macro_weight": 0.4}', 1, 230, '基于公司事件、热点板块与新闻证据生成时间感知宏观评分'),

    ('A', 'market_cap_range', '市值范围', 'filter', '', 'MarketCapFilter', '{"min_cap": null, "max_cap": null}', 1, 10, '按市值上下限筛选'),
    ('A', 'avg_daily_volume_range', '每日平均交易量范围', 'filter', '', 'AvgDailyVolumeFilter', '{"min_volume": null, "max_volume": null}', 1, 20, '按 K 线计算每日平均交易量'),
    ('A', 'price_range', '价格范围', 'filter', '', 'PriceFilter', '{"min_price": null, "max_price": null}', 1, 30, '按最新收盘价筛选'),
    ('A', 'pe_range', 'PE 范围', 'filter', '', 'PEFilter', '{"min_pe": null, "max_pe": null, "allow_negative": false}', 1, 40, '按 PE 上下限筛选'),
    ('A', 'profitability', '公司盈利', 'filter', '', 'ProfitabilityFilter', '{"require_profitable": true}', 1, 50, '要求 PE 为正'),
    ('A', 'zuoyi_signal', '左一战法', 'strategy', 'technical', 'ZuoYiStrategizer', '{"signal_window": 15, "include_bullish": true, "include_bearish": true}', 1, 110, '当前周期15根K线内左一战法看涨/看跌信号'),
    ('A', 'ema_breakout', 'EMA 突破', 'strategy', 'technical', 'EMABreakoutStrategizer', '{"ema_short": 10, "ema_long": 150, "direction": "bullish"}', 1, 120, 'EMA 短线向上突破长线'),
    ('A', 'rsi_oversold', 'RSI 超卖', 'strategy', 'technical', 'RSIOversoldStrategizer', '{"period": 14, "threshold": 30.0, "direction": "bullish"}', 1, 130, 'RSI 低于等于阈值'),
    ('A', 'rsi_overbought', 'RSI 超买', 'strategy', 'technical', 'RSIOverboughtStrategizer', '{"period": 14, "threshold": 70.0}', 1, 140, 'RSI 高于等于阈值'),
    ('A', 'volume_spike_prior3', '放量超前三日', 'strategy', 'technical', 'TodayVolumeExceedsPrior3MaxStrategizer', '{"direction": "bullish"}', 1, 150, '当日成交量大于前三日最大值'),
    ('A', 'daily_drop_6_65', '当日跌 6%~6.5%', 'strategy', 'technical', 'DailyDrop6To65Strategizer', '{"pct_min": -6.5, "pct_max": -6.0}', 1, 160, '当日跌幅在指定区间'),
    ('A', 'daily_rise_4_45', '当日涨 4%~4.5%', 'strategy', 'technical', 'DailyRise4To45Strategizer', '{"pct_min": 4.0, "pct_max": 4.5, "direction": "bullish"}', 1, 170, '当日涨幅在指定区间'),
    ('A', 'company_event_hot_sector_link', '公司时事与热点板块关联', 'strategy', 'macro', 'CompanyEventHotSectorStrategizer', '{}', 1, 210, '复用 AI 分析结果，判断公司时事是否与热点板块形成共振'),
    ('A', 'company_event_hot_news_link', '公司时事与热点新闻关联', 'strategy', 'macro', 'CompanyEventHotNewsStrategizer', '{}', 1, 220, '复用 AI 分析结果，判断公司时事是否被热点新闻验证'),
    ('A', 'market_intel_macro_score_link', '市场情报宏观评分', 'strategy', 'macro', 'MarketIntelMacroScoreStrategizer', '{"threshold": 60, "refresh_policy": "cache_or_refresh", "technical_weight": 0.6, "macro_weight": 0.4}', 1, 230, '基于公司事件、热点板块与新闻证据生成时间感知宏观评分');

INSERT IGNORE INTO screening_rule_metadata
    (market, rule_key, rule_name, rule_type, strategy_category, implementation, params_json, enabled, display_order, description)
SELECT markets.market, 'zuoyi_bullish_signal', '左一战法-看涨', 'strategy', 'technical', 'ZuoYiStrategizer',
       '{"signal_window":15,"include_bullish":true,"include_bearish":false,"direction":"bullish","signal_group":"zuoyi_bullish"}',
       1, 115, '当前周期15根K线内左一战法看涨信号'
FROM (
    SELECT 'HK' AS market UNION ALL SELECT 'US' UNION ALL SELECT 'A'
) AS markets;

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
SET strategy_category = 'technical',
    params_json = JSON_SET(COALESCE(params_json, JSON_OBJECT()), '$.direction', 'bullish', '$.signal_group', 'bullish')
WHERE rule_key IN (
    'ema_breakout', 'volume_spike_prior3', 'daily_rise_4_45',
    'bullish_engulfing', 'three_white_soldiers', 'bullish_marubozu',
    'sma_golden_cross', 'ema_golden_cross', 'macd_bullish_cross',
    'vwap_bullish_reclaim', 'atr_up_breakout', 'kdj_bullish_cross',
    'volume_price_breakout'
)
  AND rule_type = 'strategy';

UPDATE screening_rule_metadata
SET strategy_category = 'technical',
    params_json = JSON_SET(COALESCE(params_json, JSON_OBJECT()), '$.direction', 'bullish', '$.signal_group', 'rebound')
WHERE rule_key IN (
    'rsi_oversold', 'rsi_bullish_rebound', 'bollinger_lower_rebound',
    'hammer_reversal', 'morning_star', 'piercing_line', 'kdj_low_bullish_cross'
)
  AND rule_type = 'strategy';

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

INSERT IGNORE INTO screening_rule_chains
    (market, timeframe, chain_key, chain_name, expression_json, enabled, priority, description)
VALUES
    ('HK', '*', 'default_zuoyi_and_other', '左一战法与其他策略默认链',
     '{"and":[{"all_enabled":["market_cap_range","avg_daily_volume_range","price_range","pe_range","profitability"]},{"ref":"zuoyi_signal"},{"any_enabled":["ema_breakout","rsi_oversold","rsi_overbought","volume_spike_prior3","daily_drop_6_65","daily_rise_4_45"]}]}',
     1, 100, '启用硬筛选全部通过 && 左一战法命中 && 至少一个其他策略命中'),
    ('HK', '*', 'trend_capital_accumulation_watch', '趋势主力缩量观察链',
     '{"and":[{"all_enabled":["market_cap_range","avg_daily_volume_range","price_range","pe_range","profitability"]},{"ref":"zuoyi_signal"},{"any_enabled":["ema_breakout","volume_spike_prior3","daily_rise_4_45"]}]}',
     0, 300, '默认关闭的试跑链：基于现有上涨趋势/放量规则做观察，主力资金与热点板块原子规则接入后可扩展'),
    ('HK', '*', 'unified_bullish_top20', '统一看涨技术规则Top20',
     '{"ref":"ema_breakout"}',
     0, 400, '遍历所有启用看涨、准备反弹、左一看涨技术规则，按总命中数选Top20后进入AI复核'),
    ('US', '*', 'default_zuoyi_and_other', '左一战法与其他策略默认链',
     '{"and":[{"all_enabled":["market_cap_range","avg_daily_volume_range","price_range","pe_range","profitability"]},{"ref":"zuoyi_signal"},{"any_enabled":["ema_breakout","rsi_oversold","rsi_overbought","volume_spike_prior3","daily_drop_6_65","daily_rise_4_45"]}]}',
     1, 100, '启用硬筛选全部通过 && 左一战法命中 && 至少一个其他策略命中'),
    ('US', '*', 'trend_capital_accumulation_watch', '趋势主力缩量观察链',
     '{"and":[{"all_enabled":["market_cap_range","avg_daily_volume_range","price_range","pe_range","profitability"]},{"ref":"zuoyi_signal"},{"any_enabled":["ema_breakout","volume_spike_prior3","daily_rise_4_45"]}]}',
     0, 300, '默认关闭的试跑链：基于现有上涨趋势/放量规则做观察，主力资金与热点板块原子规则接入后可扩展'),
    ('US', '*', 'unified_bullish_top20', '统一看涨技术规则Top20',
     '{"ref":"ema_breakout"}',
     0, 400, '遍历所有启用看涨、准备反弹、左一看涨技术规则，按总命中数选Top20后进入AI复核'),
    ('A', '*', 'default_zuoyi_and_other', '左一战法与其他策略默认链',
     '{"and":[{"all_enabled":["market_cap_range","avg_daily_volume_range","price_range","pe_range","profitability"]},{"ref":"zuoyi_signal"},{"any_enabled":["ema_breakout","rsi_oversold","rsi_overbought","volume_spike_prior3","daily_drop_6_65","daily_rise_4_45"]}]}',
     1, 100, '启用硬筛选全部通过 && 左一战法命中 && 至少一个其他策略命中'),
    ('A', '*', 'trend_capital_accumulation_watch', '趋势主力缩量观察链',
     '{"and":[{"all_enabled":["market_cap_range","avg_daily_volume_range","price_range","pe_range","profitability"]},{"ref":"zuoyi_signal"},{"any_enabled":["ema_breakout","volume_spike_prior3","daily_rise_4_45"]}]}',
     0, 300, '默认关闭的试跑链：基于现有上涨趋势/放量规则做观察，主力资金与热点板块原子规则接入后可扩展'),
    ('A', '*', 'unified_bullish_top20', '统一看涨技术规则Top20',
     '{"ref":"ema_breakout"}',
     0, 400, '遍历所有启用看涨、准备反弹、左一看涨技术规则，按总命中数选Top20后进入AI复核');
