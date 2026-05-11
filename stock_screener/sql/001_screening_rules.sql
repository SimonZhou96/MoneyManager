-- 数据库化筛选规则引擎表结构与默认规则链
-- 可在项目部署建库时直接执行；INSERT IGNORE 不覆盖已有配置。

CREATE TABLE IF NOT EXISTS screening_rule_metadata (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    market VARCHAR(8) NOT NULL COMMENT '市场: HK/US/A',
    rule_key VARCHAR(64) NOT NULL COMMENT '原子规则键',
    rule_name VARCHAR(128) NOT NULL COMMENT '规则展示名称',
    rule_type VARCHAR(16) NOT NULL COMMENT 'filter/strategy',
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
    chain_key VARCHAR(64) NOT NULL COMMENT '规则链键',
    chain_name VARCHAR(128) NOT NULL COMMENT '规则链名称',
    expression_json JSON NOT NULL COMMENT '规则链 JSON DSL',
    enabled TINYINT(1) NOT NULL DEFAULT 1 COMMENT '是否启用',
    priority INT NOT NULL DEFAULT 100 COMMENT '优先级，越小越优先',
    description VARCHAR(512) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uk_rule_chains_market_key (market, chain_key),
    KEY idx_rule_chains_market_enabled (market, enabled, priority)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
COMMENT='筛选规则使用链';

INSERT IGNORE INTO screening_rule_metadata
    (market, rule_key, rule_name, rule_type, implementation, params_json, enabled, display_order, description)
VALUES
    ('HK', 'market_cap_range', '市值范围', 'filter', 'MarketCapFilter', '{"min_cap": null, "max_cap": null}', 1, 10, '按市值上下限筛选'),
    ('HK', 'avg_daily_volume_range', '每日平均交易量范围', 'filter', 'AvgDailyVolumeFilter', '{"min_volume": null, "max_volume": null}', 1, 20, '按 K 线计算每日平均交易量'),
    ('HK', 'price_range', '价格范围', 'filter', 'PriceFilter', '{"min_price": null, "max_price": null}', 1, 30, '按最新收盘价筛选'),
    ('HK', 'pe_range', 'PE 范围', 'filter', 'PEFilter', '{"min_pe": null, "max_pe": null, "allow_negative": false}', 1, 40, '按 PE 上下限筛选'),
    ('HK', 'profitability', '公司盈利', 'filter', 'ProfitabilityFilter', '{"require_profitable": true}', 1, 50, '要求 PE 为正'),
    ('HK', 'zuoyi_signal', '左一战法', 'strategy', 'ZuoYiStrategizer', '{"signal_window": 15, "include_bullish": true, "include_bearish": true}', 1, 110, '当前周期15根K线内左一战法看涨/看跌信号'),
    ('HK', 'ema_breakout', 'EMA 突破', 'strategy', 'EMABreakoutStrategizer', '{"ema_short": 10, "ema_long": 150}', 1, 120, 'EMA 短线向上突破长线'),
    ('HK', 'rsi_oversold', 'RSI 超卖', 'strategy', 'RSIOversoldStrategizer', '{"period": 14, "threshold": 30.0}', 1, 130, 'RSI 低于等于阈值'),
    ('HK', 'rsi_overbought', 'RSI 超买', 'strategy', 'RSIOverboughtStrategizer', '{"period": 14, "threshold": 70.0}', 1, 140, 'RSI 高于等于阈值'),
    ('HK', 'volume_spike_prior3', '放量超前三日', 'strategy', 'TodayVolumeExceedsPrior3MaxStrategizer', '{}', 1, 150, '当日成交量大于前三日最大值'),
    ('HK', 'daily_drop_6_65', '当日跌 6%~6.5%', 'strategy', 'DailyDrop6To65Strategizer', '{"pct_min": -6.5, "pct_max": -6.0}', 1, 160, '当日跌幅在指定区间'),
    ('HK', 'daily_rise_4_45', '当日涨 4%~4.5%', 'strategy', 'DailyRise4To45Strategizer', '{"pct_min": 4.0, "pct_max": 4.5}', 1, 170, '当日涨幅在指定区间'),

    ('US', 'market_cap_range', '市值范围', 'filter', 'MarketCapFilter', '{"min_cap": null, "max_cap": null}', 1, 10, '按市值上下限筛选'),
    ('US', 'avg_daily_volume_range', '每日平均交易量范围', 'filter', 'AvgDailyVolumeFilter', '{"min_volume": null, "max_volume": null}', 1, 20, '按 K 线计算每日平均交易量'),
    ('US', 'price_range', '价格范围', 'filter', 'PriceFilter', '{"min_price": null, "max_price": null}', 1, 30, '按最新收盘价筛选'),
    ('US', 'pe_range', 'PE 范围', 'filter', 'PEFilter', '{"min_pe": null, "max_pe": null, "allow_negative": false}', 1, 40, '按 PE 上下限筛选'),
    ('US', 'profitability', '公司盈利', 'filter', 'ProfitabilityFilter', '{"require_profitable": true}', 1, 50, '要求 PE 为正'),
    ('US', 'zuoyi_signal', '左一战法', 'strategy', 'ZuoYiStrategizer', '{"signal_window": 15, "include_bullish": true, "include_bearish": true}', 1, 110, '当前周期15根K线内左一战法看涨/看跌信号'),
    ('US', 'ema_breakout', 'EMA 突破', 'strategy', 'EMABreakoutStrategizer', '{"ema_short": 10, "ema_long": 150}', 1, 120, 'EMA 短线向上突破长线'),
    ('US', 'rsi_oversold', 'RSI 超卖', 'strategy', 'RSIOversoldStrategizer', '{"period": 14, "threshold": 30.0}', 1, 130, 'RSI 低于等于阈值'),
    ('US', 'rsi_overbought', 'RSI 超买', 'strategy', 'RSIOverboughtStrategizer', '{"period": 14, "threshold": 70.0}', 1, 140, 'RSI 高于等于阈值'),
    ('US', 'volume_spike_prior3', '放量超前三日', 'strategy', 'TodayVolumeExceedsPrior3MaxStrategizer', '{}', 1, 150, '当日成交量大于前三日最大值'),
    ('US', 'daily_drop_6_65', '当日跌 6%~6.5%', 'strategy', 'DailyDrop6To65Strategizer', '{"pct_min": -6.5, "pct_max": -6.0}', 1, 160, '当日跌幅在指定区间'),
    ('US', 'daily_rise_4_45', '当日涨 4%~4.5%', 'strategy', 'DailyRise4To45Strategizer', '{"pct_min": 4.0, "pct_max": 4.5}', 1, 170, '当日涨幅在指定区间'),

    ('A', 'market_cap_range', '市值范围', 'filter', 'MarketCapFilter', '{"min_cap": null, "max_cap": null}', 1, 10, '按市值上下限筛选'),
    ('A', 'avg_daily_volume_range', '每日平均交易量范围', 'filter', 'AvgDailyVolumeFilter', '{"min_volume": null, "max_volume": null}', 1, 20, '按 K 线计算每日平均交易量'),
    ('A', 'price_range', '价格范围', 'filter', 'PriceFilter', '{"min_price": null, "max_price": null}', 1, 30, '按最新收盘价筛选'),
    ('A', 'pe_range', 'PE 范围', 'filter', 'PEFilter', '{"min_pe": null, "max_pe": null, "allow_negative": false}', 1, 40, '按 PE 上下限筛选'),
    ('A', 'profitability', '公司盈利', 'filter', 'ProfitabilityFilter', '{"require_profitable": true}', 1, 50, '要求 PE 为正'),
    ('A', 'zuoyi_signal', '左一战法', 'strategy', 'ZuoYiStrategizer', '{"signal_window": 15, "include_bullish": true, "include_bearish": true}', 1, 110, '当前周期15根K线内左一战法看涨/看跌信号'),
    ('A', 'ema_breakout', 'EMA 突破', 'strategy', 'EMABreakoutStrategizer', '{"ema_short": 10, "ema_long": 150}', 1, 120, 'EMA 短线向上突破长线'),
    ('A', 'rsi_oversold', 'RSI 超卖', 'strategy', 'RSIOversoldStrategizer', '{"period": 14, "threshold": 30.0}', 1, 130, 'RSI 低于等于阈值'),
    ('A', 'rsi_overbought', 'RSI 超买', 'strategy', 'RSIOverboughtStrategizer', '{"period": 14, "threshold": 70.0}', 1, 140, 'RSI 高于等于阈值'),
    ('A', 'volume_spike_prior3', '放量超前三日', 'strategy', 'TodayVolumeExceedsPrior3MaxStrategizer', '{}', 1, 150, '当日成交量大于前三日最大值'),
    ('A', 'daily_drop_6_65', '当日跌 6%~6.5%', 'strategy', 'DailyDrop6To65Strategizer', '{"pct_min": -6.5, "pct_max": -6.0}', 1, 160, '当日跌幅在指定区间'),
    ('A', 'daily_rise_4_45', '当日涨 4%~4.5%', 'strategy', 'DailyRise4To45Strategizer', '{"pct_min": 4.0, "pct_max": 4.5}', 1, 170, '当日涨幅在指定区间');

INSERT IGNORE INTO screening_rule_chains
    (market, chain_key, chain_name, expression_json, enabled, priority, description)
VALUES
    ('HK', 'default_zuoyi_and_other', '左一战法与其他策略默认链',
     '{"and":[{"all_enabled":["market_cap_range","avg_daily_volume_range","price_range","pe_range","profitability"]},{"ref":"zuoyi_signal"},{"any_enabled":["ema_breakout","rsi_oversold","rsi_overbought","volume_spike_prior3","daily_drop_6_65","daily_rise_4_45"]}]}',
     1, 100, '启用硬筛选全部通过 && 左一战法命中 && 至少一个其他策略命中'),
    ('US', 'default_zuoyi_and_other', '左一战法与其他策略默认链',
     '{"and":[{"all_enabled":["market_cap_range","avg_daily_volume_range","price_range","pe_range","profitability"]},{"ref":"zuoyi_signal"},{"any_enabled":["ema_breakout","rsi_oversold","rsi_overbought","volume_spike_prior3","daily_drop_6_65","daily_rise_4_45"]}]}',
     1, 100, '启用硬筛选全部通过 && 左一战法命中 && 至少一个其他策略命中'),
    ('A', 'default_zuoyi_and_other', '左一战法与其他策略默认链',
     '{"and":[{"all_enabled":["market_cap_range","avg_daily_volume_range","price_range","pe_range","profitability"]},{"ref":"zuoyi_signal"},{"any_enabled":["ema_breakout","rsi_oversold","rsi_overbought","volume_spike_prior3","daily_drop_6_65","daily_rise_4_45"]}]}',
     1, 100, '启用硬筛选全部通过 && 左一战法命中 && 至少一个其他策略命中');
