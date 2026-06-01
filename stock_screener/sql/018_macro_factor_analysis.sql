-- 宏观因子分析规则 + 企业潜力分析规则 + 规则链
-- 1. macro_factor_analysis — 宏观因子采集
-- 2. enterprise_potential_analysis — 五模块综合评分

-- ══════════════════════════════════════════════════════════════
-- 1. 宏观因子采集规则 (三市场)
-- ══════════════════════════════════════════════════════════════

INSERT IGNORE INTO screening_rule_metadata
    (market, rule_key, rule_name, rule_type, strategy_category, implementation, params_json, enabled, display_order, description)
VALUES
    ('HK', 'macro_factor_analysis', '宏观因子采集与分析', 'strategy', 'macro', 'MacroFactorAnalysisStrategizer', '{"min_factors": 5}', 1, 235, '采集中国宏观（CPI/PMI/M2/LPR）+全球指标（VIX/DXY/美债/恒指/SP500），通过akshare+yfinance拉取结构化宏观因子快照'),
    ('US', 'macro_factor_analysis', '宏观因子采集与分析', 'strategy', 'macro', 'MacroFactorAnalysisStrategizer', '{"min_factors": 3}', 1, 235, '采集美国宏观（美债/VIX/DXY/SP500），通过yfinance拉取结构化宏观因子快照'),
    ('A',  'macro_factor_analysis', '宏观因子采集与分析', 'strategy', 'macro', 'MacroFactorAnalysisStrategizer', '{"min_factors": 5}', 1, 235, '采集中国宏观（CPI/PMI/M2/LPR/社融）+全球指标（VIX/DXY），通过akshare+yfinance拉取结构化宏观因子快照');

-- ══════════════════════════════════════════════════════════════
-- 2. 企业潜力分析规则 (三市场)
-- ══════════════════════════════════════════════════════════════

INSERT IGNORE INTO screening_rule_metadata
    (market, rule_key, rule_name, rule_type, strategy_category, implementation, params_json, enabled, display_order, description)
VALUES
    ('HK', 'enterprise_potential_analysis', '企业潜力分析', 'strategy', 'macro', 'EnterprisePotentialAnalysisStrategizer',
     '{"threshold": 70}', 1, 240,
     '五模块综合评分：宏观(30%)+行业(25%)+企业质量(25%)+估值(10%)+交易(10%)，规则型打分，输出BUY/WATCH/SKIP决策'),
    ('US', 'enterprise_potential_analysis', '企业潜力分析', 'strategy', 'macro', 'EnterprisePotentialAnalysisStrategizer',
     '{"threshold": 70}', 1, 240,
     '五模块综合评分：宏观(30%)+行业(25%)+企业质量(25%)+估值(10%)+交易(10%)，规则型打分，输出BUY/WATCH/SKIP决策'),
    ('A',  'enterprise_potential_analysis', '企业潜力分析', 'strategy', 'macro', 'EnterprisePotentialAnalysisStrategizer',
     '{"threshold": 70}', 1, 240,
     '五模块综合评分：宏观(30%)+行业(25%)+企业质量(25%)+估值(10%)+交易(10%)，规则型打分，输出BUY/WATCH/SKIP决策');

-- ══════════════════════════════════════════════════════════════
-- 3. 规则链
-- ══════════════════════════════════════════════════════════════

-- 宏观因子分析链（只看宏观因子）
INSERT IGNORE INTO screening_rule_chains
    (market, timeframe, chain_key, chain_name, expression_json, enabled, priority, description)
VALUES
    ('HK', '*', 'macro_factor_only', '宏观因子分析链',
     '{"and":[{"ref":"macro_factor_analysis"}]}',
     1, 500, '独立规则链：仅运行宏观因子采集与分析'),
    ('US', '*', 'macro_factor_only', '宏观因子分析链',
     '{"and":[{"ref":"macro_factor_analysis"}]}',
     1, 500, '独立规则链：仅运行宏观因子采集与分析'),
    ('A', '*', 'macro_factor_only', '宏观因子分析链',
     '{"and":[{"ref":"macro_factor_analysis"}]}',
     1, 500, '独立规则链：仅运行宏观因子采集与分析');

-- 企业潜力分析链（五模块综合评分）
INSERT IGNORE INTO screening_rule_chains
    (market, timeframe, chain_key, chain_name, expression_json, enabled, priority, description)
VALUES
    ('HK', '*', 'enterprise_potential_analysis', '企业潜力分析链',
     '{"and":[{"ref":"enterprise_potential_analysis"}]}',
     1, 510, '独立规则链：五模块（宏观+行业+企业质量+估值+交易）综合评分，输出BUY/WATCH/SKIP'),
    ('US', '*', 'enterprise_potential_analysis', '企业潜力分析链',
     '{"and":[{"ref":"enterprise_potential_analysis"}]}',
     1, 510, '独立规则链：五模块综合评分'),
    ('A', '*', 'enterprise_potential_analysis', '企业潜力分析链',
     '{"and":[{"ref":"enterprise_potential_analysis"}]}',
     1, 510, '独立规则链：五模块综合评分');

-- ══════════════════════════════════════════════════════════════
-- 4. 综合筛选链（硬筛选 + 左一 + 技术 + 宏观/事件）
-- ══════════════════════════════════════════════════════════════

-- ══════════════════════════════════════════════════════════════
-- 4. 综合筛选链（分层设计：门禁层→标签层→评分层）
--
--    门禁层: 硬筛选 + 左一 + 技术形态 — 真正的 pass/fail 阻断
--    标签层: CompanyEventHotSector/HotNews/MacroScore — 出pass/fail标签，不阻断链
--            macro_factor_analysis 做 pass-through 兜底
--    评分层: EnterprisePotentialAnalysis — 五模块综合评分，所有通过门禁的股票都参与
-- ══════════════════════════════════════════════════════════════

-- 方案 A: HK/US 日常筛选 — 标签层用 macro_factor_analysis 兜底，sector/news/macro_score 出标签
INSERT IGNORE INTO screening_rule_chains
    (market, timeframe, chain_key, chain_name, expression_json, enabled, priority, description)
VALUES
    ('HK', '*', 'zuoyi_with_macro_enhanced', '左一战法+宏观标签+五模块评分',
     '{"and":[
        {"all_enabled":["market_cap_range","avg_daily_volume_range","price_range","pe_range","profitability"]},
        {"ref":"zuoyi_signal"},
        {"any_enabled":["ema_breakout","rsi_oversold","rsi_overbought","volume_spike_prior3","daily_drop_6_65","daily_rise_4_45"]},
        {"any_enabled":["macro_factor_analysis","company_event_hot_sector_link","company_event_hot_news_link","market_intel_macro_score_link"]},
        {"ref":"enterprise_potential_analysis"}
     ]}',
     1, 200, '门禁(硬筛选+左一+技术)→标签层(宏观因子跑通即过,sector/news/macro_score出pass/fail标签)→五模块综合评分(所有通过门禁的股票都评分)'),

    ('US', '*', 'zuoyi_with_macro_enhanced', '左一战法+宏观标签+五模块评分',
     '{"and":[
        {"all_enabled":["market_cap_range","avg_daily_volume_range","price_range","pe_range","profitability"]},
        {"ref":"zuoyi_signal"},
        {"any_enabled":["ema_breakout","rsi_oversold","rsi_overbought","volume_spike_prior3","daily_drop_6_65","daily_rise_4_45"]},
        {"any_enabled":["macro_factor_analysis","company_event_hot_sector_link","company_event_hot_news_link","market_intel_macro_score_link"]},
        {"ref":"enterprise_potential_analysis"}
     ]}',
     1, 200, '门禁(硬筛选+左一+技术)→标签层(宏观因子跑通即过,sector/news/macro_score出pass/fail标签)→五模块综合评分(所有通过门禁的股票都评分)');

-- 方案 B: A股宏观敏感期 — 宏观因子是真门禁 + 标签层不阻断
INSERT IGNORE INTO screening_rule_chains
    (market, timeframe, chain_key, chain_name, expression_json, enabled, priority, description)
VALUES
    ('A', '*', 'zuoyi_with_macro_strict', '左一战法+宏观严选标签+五模块评分',
     '{"and":[
        {"all_enabled":["market_cap_range","avg_daily_volume_range","price_range","pe_range","profitability"]},
        {"ref":"zuoyi_signal"},
        {"any_enabled":["ema_breakout","rsi_oversold","rsi_overbought","volume_spike_prior3","daily_drop_6_65","daily_rise_4_45"]},
        {"ref":"macro_factor_analysis"},
        {"any_enabled":["company_event_hot_sector_link","company_event_hot_news_link","market_intel_macro_score_link"]},
        {"ref":"enterprise_potential_analysis"}
     ]}',
     1, 200, '门禁(硬筛选+左一+技术+宏观因子必达标)→标签层(sector/news/macro_score出pass/fail标签,不阻断)→五模块综合评分(所有通过门禁的股票都评分)');
