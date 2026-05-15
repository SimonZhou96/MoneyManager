package rules

import (
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"github.com/SimonZhou96/MoneyManager/agent_orchestrator/internal/strategies"
)

const DefaultRuleChainKey = "default_zuoyi_and_other"

const (
	RuleTypeFilter   = "filter"
	RuleTypeStrategy = "strategy"
)

type ResultStatus string

const (
	ResultPass  ResultStatus = "pass"
	ResultFail  ResultStatus = "fail"
	ResultSkip  ResultStatus = "skip"
	ResultError ResultStatus = "error"
)

type RuleMetadata struct {
	Market         string         `json:"market" db:"market"`
	RuleKey        string         `json:"rule_key" db:"rule_key"`
	RuleName       string         `json:"rule_name" db:"rule_name"`
	RuleType       string         `json:"rule_type" db:"rule_type"`
	Implementation string         `json:"implementation" db:"implementation"`
	Params         map[string]any `json:"params" db:"params_json"`
	Enabled        bool           `json:"enabled" db:"enabled"`
	DisplayOrder   int            `json:"display_order" db:"display_order"`
	Description    string         `json:"description" db:"description"`
}

func NewRuleMetadata(market, ruleKey, ruleName, ruleType, implementation string, params map[string]any, enabled bool, displayOrder int, description string) RuleMetadata {
	if params == nil {
		params = map[string]any{}
	}
	return RuleMetadata{
		Market:         market,
		RuleKey:        ruleKey,
		RuleName:       ruleName,
		RuleType:       ruleType,
		Implementation: implementation,
		Params:         params,
		Enabled:        enabled,
		DisplayOrder:   displayOrder,
		Description:    description,
	}
}

func RuleMetadataFromRow(row map[string]any) (RuleMetadata, error) {
	params, err := mapParam(row, "params_json", "params")
	if err != nil {
		return RuleMetadata{}, err
	}
	return RuleMetadata{
		Market:         stringParam(row, "market"),
		RuleKey:        stringParam(row, "rule_key"),
		RuleName:       stringParam(row, "rule_name"),
		RuleType:       stringParam(row, "rule_type"),
		Implementation: stringParam(row, "implementation"),
		Params:         params,
		Enabled:        boolValue(row["enabled"], false),
		DisplayOrder:   intValue(row["display_order"], 0),
		Description:    stringParam(row, "description"),
	}, nil
}

type RuleChainConfig struct {
	Market      string         `json:"market" db:"market"`
	Timeframe   string         `json:"timeframe" db:"timeframe"`
	ChainKey    string         `json:"chain_key" db:"chain_key"`
	ChainName   string         `json:"chain_name" db:"chain_name"`
	Expression  map[string]any `json:"expression" db:"expression_json"`
	Enabled     bool           `json:"enabled" db:"enabled"`
	Priority    int            `json:"priority" db:"priority"`
	Description string         `json:"description" db:"description"`
}

func NewRuleChainConfig(market, chainKey, chainName string, expression map[string]any, enabled bool, priority int, description string) RuleChainConfig {
	if expression == nil {
		expression = map[string]any{}
	}
	return RuleChainConfig{
		Market:      market,
		Timeframe:   "*",
		ChainKey:    chainKey,
		ChainName:   chainName,
		Expression:  expression,
		Enabled:     enabled,
		Priority:    priority,
		Description: description,
	}
}

func RuleChainConfigFromRow(row map[string]any) (RuleChainConfig, error) {
	expression, err := mapParam(row, "expression_json", "expression")
	if err != nil {
		return RuleChainConfig{}, err
	}
	return RuleChainConfig{
		Market:      stringParam(row, "market"),
		Timeframe:   stringParamDefault(row, "*", "timeframe"),
		ChainKey:    stringParam(row, "chain_key"),
		ChainName:   stringParam(row, "chain_name"),
		Expression:  expression,
		Enabled:     boolValue(row["enabled"], false),
		Priority:    intValue(row["priority"], 100),
		Description: stringParam(row, "description"),
	}, nil
}

func DefaultRuleMetadata(market string) []RuleMetadata {
	return []RuleMetadata{
		NewRuleMetadata(market, "market_cap_range", "市值范围", RuleTypeFilter, "MarketCapFilter", map[string]any{"min_cap": nil, "max_cap": nil}, true, 10, "按市值上下限筛选"),
		NewRuleMetadata(market, "avg_daily_volume_range", "每日平均交易量范围", RuleTypeFilter, "AvgDailyVolumeFilter", map[string]any{"min_volume": nil, "max_volume": nil}, true, 20, "按 K 线计算每日平均交易量"),
		NewRuleMetadata(market, "price_range", "价格范围", RuleTypeFilter, "PriceFilter", map[string]any{"min_price": nil, "max_price": nil}, true, 30, "按最新收盘价筛选"),
		NewRuleMetadata(market, "pe_range", "PE 范围", RuleTypeFilter, "PEFilter", map[string]any{"min_pe": nil, "max_pe": nil, "allow_negative": false}, true, 40, "按 PE 上下限筛选"),
		NewRuleMetadata(market, "profitability", "公司盈利", RuleTypeFilter, "ProfitabilityFilter", map[string]any{"require_profitable": true}, true, 50, "要求 PE 为正"),
		NewRuleMetadata(market, "zuoyi_signal", "左一战法", RuleTypeStrategy, "ZuoYiStrategizer", map[string]any{"signal_window": 15, "include_bullish": true, "include_bearish": true}, true, 110, "当前周期15根K线内左一战法看涨/看跌信号"),
		NewRuleMetadata(market, "ema_breakout", "EMA 突破", RuleTypeStrategy, "EMABreakoutStrategizer", map[string]any{"ema_short": 10, "ema_long": 150}, true, 120, "EMA 短线向上突破长线"),
		NewRuleMetadata(market, "rsi_oversold", "RSI 超卖", RuleTypeStrategy, "RSIOversoldStrategizer", map[string]any{"period": 14, "threshold": 30.0}, true, 130, "RSI 低于等于阈值"),
		NewRuleMetadata(market, "rsi_overbought", "RSI 超买", RuleTypeStrategy, "RSIOverboughtStrategizer", map[string]any{"period": 14, "threshold": 70.0}, true, 140, "RSI 高于等于阈值"),
		NewRuleMetadata(market, "volume_spike_prior3", "放量超前三日", RuleTypeStrategy, "TodayVolumeExceedsPrior3MaxStrategizer", map[string]any{}, true, 150, "当日成交量大于前三日最大值"),
		NewRuleMetadata(market, "daily_drop_6_65", "当日跌 6%~6.5%", RuleTypeStrategy, "DailyDrop6To65Strategizer", map[string]any{"pct_min": -6.5, "pct_max": -6.0}, true, 160, "当日跌幅在指定区间"),
		NewRuleMetadata(market, "daily_rise_4_45", "当日涨 4%~4.5%", RuleTypeStrategy, "DailyRise4To45Strategizer", map[string]any{"pct_min": 4.0, "pct_max": 4.5}, true, 170, "当日涨幅在指定区间"),
	}
}

func DefaultRuleChainConfig(market string) RuleChainConfig {
	return NewRuleChainConfig(market, DefaultRuleChainKey, "左一战法与其他策略默认链", map[string]any{
		"and": []any{
			map[string]any{"all_enabled": []any{"market_cap_range", "avg_daily_volume_range", "price_range", "pe_range", "profitability"}},
			map[string]any{"ref": "zuoyi_signal"},
			map[string]any{"any_enabled": []any{"ema_breakout", "rsi_oversold", "rsi_overbought", "volume_spike_prior3", "daily_drop_6_65", "daily_rise_4_45"}},
		},
	}, true, 100, "启用硬筛选全部通过 && 左一战法命中 && 至少一个其他策略命中")
}

type Stock struct {
	Market   string
	Code     string
	Name     string
	Sector   string
	Industry string

	MarketCap    float64
	PERatio      float64
	PBRatio      float64
	TurnoverRate float64
	Volume       float64

	HasMarketCap    bool
	HasPERatio      bool
	HasPBRatio      bool
	HasTurnoverRate bool
	HasVolume       bool

	Bars  []strategies.Bar
	Extra map[string]any
}

func mapParam(row map[string]any, keys ...string) (map[string]any, error) {
	for _, key := range keys {
		value, ok := row[key]
		if !ok || value == nil {
			continue
		}
		switch typed := value.(type) {
		case map[string]any:
			return typed, nil
		case string:
			if typed == "" {
				return map[string]any{}, nil
			}
			var decoded map[string]any
			if err := json.Unmarshal([]byte(typed), &decoded); err != nil {
				return nil, fmt.Errorf("decode %s: %w", key, err)
			}
			return decoded, nil
		case []byte:
			if len(typed) == 0 {
				return map[string]any{}, nil
			}
			var decoded map[string]any
			if err := json.Unmarshal(typed, &decoded); err != nil {
				return nil, fmt.Errorf("decode %s: %w", key, err)
			}
			return decoded, nil
		default:
			return nil, fmt.Errorf("%s must be map or JSON object", key)
		}
	}
	return map[string]any{}, nil
}

func stringParam(row map[string]any, key string) string {
	if row == nil {
		return ""
	}
	value, ok := row[key]
	if !ok || value == nil {
		return ""
	}
	if typed, ok := value.(string); ok {
		return typed
	}
	return fmt.Sprint(value)
}

func stringParamDefault(row map[string]any, fallback string, key string) string {
	value := strings.TrimSpace(stringParam(row, key))
	if value == "" {
		return fallback
	}
	return value
}

func intValue(value any, fallback int) int {
	number, ok := toFloat(value)
	if !ok {
		return fallback
	}
	return int(number)
}

func boolValue(value any, fallback bool) bool {
	switch typed := value.(type) {
	case bool:
		return typed
	case nil:
		return fallback
	default:
		if parsed := boolParam(map[string]any{"value": typed}, "value", fallback); parsed != fallback {
			return parsed
		}
		number, ok := toFloat(typed)
		if ok {
			return number != 0
		}
	}
	return fallback
}

func NewStock(market, code, name string) Stock {
	return Stock{
		Market: market,
		Code:   code,
		Name:   name,
		Extra:  map[string]any{},
	}
}

type EvaluationInput struct {
	Stock     Stock
	CheckDate time.Time
	Timeframe string
	Bars      []strategies.Bar
	Extra     map[string]any
}

func NewEvaluationInput(stock Stock, checkDate time.Time, timeframe string, bars []strategies.Bar) EvaluationInput {
	return EvaluationInput{
		Stock:     stock,
		CheckDate: checkDate,
		Timeframe: timeframe,
		Bars:      bars,
		Extra:     map[string]any{},
	}
}

func (i EvaluationInput) Market() string {
	if i.Stock.Market != "" {
		return i.Stock.Market
	}
	if value, ok := stringFromMap(i.Extra, "market"); ok {
		return value
	}
	return ""
}

func (i EvaluationInput) Code() string {
	if i.Stock.Code != "" {
		return i.Stock.Code
	}
	if value, ok := stringFromMap(i.Extra, "code"); ok {
		return value
	}
	return ""
}

func (i EvaluationInput) effectiveBars() []strategies.Bar {
	if len(i.Bars) > 0 {
		return i.Bars
	}
	return i.Stock.Bars
}

type RuleOutput struct {
	RuleKey string
	Name    string
	Result  ResultStatus
	Reason  string
	Details map[string]any
}

func NewRuleOutput(ruleKey, name string, result ResultStatus, reason string, details map[string]any) RuleOutput {
	if details == nil {
		details = map[string]any{}
	}
	return RuleOutput{
		RuleKey: ruleKey,
		Name:    name,
		Result:  result,
		Reason:  reason,
		Details: details,
	}
}

type StockResult struct {
	Stock   Stock
	Passed  bool
	Outputs []RuleOutput
}

func NewStockResult(stock Stock, passed bool, outputs []RuleOutput) StockResult {
	if outputs == nil {
		outputs = []RuleOutput{}
	}
	return StockResult{
		Stock:   stock,
		Passed:  passed,
		Outputs: outputs,
	}
}
