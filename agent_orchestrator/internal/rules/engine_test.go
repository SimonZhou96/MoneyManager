package rules

import (
	"context"
	"testing"
	"time"

	"github.com/SimonZhou96/MoneyManager/agent_orchestrator/internal/strategies"
)

type staticFilter struct {
	name   string
	result ResultStatus
}

func (f staticFilter) Evaluate(context.Context, EvaluationInput) RuleOutput {
	return RuleOutput{Name: f.name, Result: f.result, Reason: string(f.result)}
}

type staticStrategy struct {
	strategyName string
	satisfied    bool
}

func (s staticStrategy) Key() string { return s.strategyName }

func (s staticStrategy) Evaluate(context.Context, strategies.StrategyInput) (strategies.StrategyResult, error) {
	return strategies.StrategyResult{Name: s.strategyName, Satisfied: s.satisfied, Reason: "static"}, nil
}

func testMetadata(ruleKey, ruleType, implementation string, enabled bool, params map[string]any, order int) RuleMetadata {
	return RuleMetadata{
		Market:         "HK",
		RuleKey:        ruleKey,
		RuleName:       ruleKey,
		RuleType:       ruleType,
		Implementation: implementation,
		Params:         params,
		Enabled:        enabled,
		DisplayOrder:   order,
	}
}

func TestRuleEngineDefaultChainRequiresHardFiltersZuoYiAndOtherStrategy(t *testing.T) {
	registry := NewRegistry()
	registry.RegisterFilter("StaticFilter", func(params map[string]any) FilterEvaluator {
		return staticFilter{name: "StaticFilter", result: ResultStatus(params["result"].(string))}
	})
	registry.RegisterStrategy("StaticStrategy", func(params map[string]any) strategies.Strategy {
		return staticStrategy{strategyName: params["name"].(string), satisfied: params["satisfied"].(bool)}
	})

	engine := NewEngine([]RuleMetadata{
		testMetadata("market_cap_range", RuleTypeFilter, "StaticFilter", true, map[string]any{"result": string(ResultPass)}, 10),
		testMetadata("price_range", RuleTypeFilter, "StaticFilter", true, map[string]any{"result": string(ResultPass)}, 20),
		testMetadata("zuoyi_signal", RuleTypeStrategy, "StaticStrategy", true, map[string]any{"name": "ZuoYiStrategizer", "satisfied": true}, 110),
		testMetadata("ema_breakout", RuleTypeStrategy, "StaticStrategy", true, map[string]any{"name": "EMABreakoutStrategizer", "satisfied": false}, 120),
		testMetadata("rsi_oversold", RuleTypeStrategy, "StaticStrategy", true, map[string]any{"name": "RSIOversoldStrategizer", "satisfied": true}, 130),
	}, RuleChainConfig{
		Market:   "HK",
		ChainKey: "test",
		Expression: map[string]any{
			"and": []any{
				map[string]any{"all_enabled": []any{"market_cap_range", "price_range"}},
				map[string]any{"ref": "zuoyi_signal"},
				map[string]any{"any_enabled": []any{"ema_breakout", "rsi_oversold"}},
			},
		},
	}, registry)

	result := engine.EvaluateStock(context.Background(), EvaluationInput{
		Stock:     Stock{Market: "HK", Code: "HK.00001", Name: "Test"},
		CheckDate: time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC),
	})
	if !result.Passed {
		t.Fatalf("expected stock to pass, got %#v", result)
	}
	if len(result.Outputs) != 5 {
		t.Fatalf("expected all referenced rules to execute, got %d", len(result.Outputs))
	}
}

func TestRuleEngineDisabledAndMissingRulesFollowPythonSemantics(t *testing.T) {
	registry := NewRegistry()
	engine := NewEngine(nil, RuleChainConfig{Expression: map[string]any{"any_enabled": []any{"missing"}}}, registry)
	result := engine.EvaluateStock(context.Background(), EvaluationInput{Stock: Stock{Market: "HK", Code: "HK.00001"}})
	if result.Passed {
		t.Fatal("missing any_enabled rule should fail")
	}

	engine = NewEngine(nil, RuleChainConfig{Expression: map[string]any{"all_enabled": []any{"missing"}}}, registry)
	result = engine.EvaluateStock(context.Background(), EvaluationInput{Stock: Stock{Market: "HK", Code: "HK.00001"}})
	if !result.Passed {
		t.Fatal("empty all_enabled set should pass")
	}
}
