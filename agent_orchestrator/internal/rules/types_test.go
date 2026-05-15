package rules

import "testing"

func TestRuleChainConfigFromRowReadsTimeframe(t *testing.T) {
	chain, err := RuleChainConfigFromRow(map[string]any{
		"market":          "HK",
		"timeframe":       "5m",
		"chain_key":       "default",
		"chain_name":      "默认",
		"expression_json": map[string]any{"ref": "zuoyi_signal"},
		"enabled":         true,
	})
	if err != nil {
		t.Fatalf("RuleChainConfigFromRow: %v", err)
	}
	if chain.Timeframe != "5m" {
		t.Fatalf("expected timeframe 5m, got %q", chain.Timeframe)
	}
}

func TestRuleChainConfigFromRowDefaultsTimeframe(t *testing.T) {
	chain, err := RuleChainConfigFromRow(map[string]any{
		"market":          "HK",
		"chain_key":       "default",
		"chain_name":      "默认",
		"expression_json": map[string]any{"ref": "zuoyi_signal"},
		"enabled":         true,
		"priority":        100,
	})
	if err != nil {
		t.Fatalf("RuleChainConfigFromRow: %v", err)
	}
	if chain.Timeframe != "*" {
		t.Fatalf("expected wildcard timeframe, got %q", chain.Timeframe)
	}
}
