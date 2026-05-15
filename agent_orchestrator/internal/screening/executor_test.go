package screening

import (
	"context"
	"testing"
	"time"

	"github.com/SimonZhou96/MoneyManager/agent_orchestrator/internal/rules"
	"github.com/SimonZhou96/MoneyManager/agent_orchestrator/internal/strategies"
	"github.com/SimonZhou96/MoneyManager/agent_orchestrator/internal/workflow"
)

type fakeRepository struct {
	stocks          []rules.Stock
	results         []Result
	status          string
	loadedTimeframe string
}

func (f *fakeRepository) LoadRuleMetadata(context.Context, string) ([]rules.RuleMetadata, error) {
	return []rules.RuleMetadata{
		rules.NewRuleMetadata("HK", "zuoyi_signal", "左一", rules.RuleTypeStrategy, "StaticStrategy", map[string]any{"name": "ZuoYiStrategizer", "satisfied": true}, true, 1, ""),
		rules.NewRuleMetadata("HK", "ema_breakout", "EMA", rules.RuleTypeStrategy, "StaticStrategy", map[string]any{"name": "EMABreakoutStrategizer", "satisfied": true}, true, 2, ""),
	}, nil
}

func (f *fakeRepository) LoadRuleChain(_ context.Context, _ string, timeframe string, _ string) (rules.RuleChainConfig, error) {
	f.loadedTimeframe = timeframe
	return rules.NewRuleChainConfig("HK", "test", "test", map[string]any{
		"and": []any{
			map[string]any{"ref": "zuoyi_signal"},
			map[string]any{"any_enabled": []any{"ema_breakout"}},
		},
	}, true, 1, ""), nil
}

func (f *fakeRepository) ListPoolStocks(context.Context, string) ([]rules.Stock, error) {
	return f.stocks, nil
}

func (f *fakeRepository) ListStocksByCodes(_ context.Context, _ string, _ []string) ([]rules.Stock, error) {
	return f.stocks, nil
}

func (f *fakeRepository) CreateScreeningTask(context.Context, Task) error { return nil }
func (f *fakeRepository) UpdateTaskProgress(context.Context, string, int, string, string) error {
	return nil
}
func (f *fakeRepository) UpdateTaskStatus(_ context.Context, _ string, status string) error {
	f.status = status
	return nil
}
func (f *fakeRepository) UpsertScreeningResult(_ context.Context, result Result) error {
	f.results = append(f.results, result)
	return nil
}

type fakeStrategy struct {
	name      string
	satisfied bool
}

func (s fakeStrategy) Key() string { return s.name }
func (s fakeStrategy) Evaluate(context.Context, strategies.StrategyInput) (strategies.StrategyResult, error) {
	return strategies.StrategyResult{Name: s.name, Satisfied: s.satisfied, Reason: "ok"}, nil
}

func TestExecutorRunsRuleEngineAndPersistsResults(t *testing.T) {
	repo := &fakeRepository{stocks: []rules.Stock{{Market: "HK", Code: "HK.00001", Name: "Test"}}}
	registry := rules.NewRegistry()
	registry.RegisterStrategy("StaticStrategy", func(params map[string]any) strategies.Strategy {
		return fakeStrategy{name: params["name"].(string), satisfied: params["satisfied"].(bool)}
	})
	executor := NewExecutor(repo, nil, registry)
	executor.checkDate = func() time.Time { return time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC) }

	state, err := executor.ExecuteRules(context.Background(), workflow.State{JobID: "job-1", Markets: []string{"HK"}, Timeframe: "1d"})
	if err != nil {
		t.Fatalf("ExecuteRules: %v", err)
	}
	if len(state.MarketResults) != 1 || state.MarketResults[0].PassedCount != 1 {
		t.Fatalf("unexpected state: %#v", state)
	}
	if repo.status != "completed" || len(repo.results) != 1 || !repo.results[0].Passed {
		t.Fatalf("unexpected persisted result: status=%s results=%#v", repo.status, repo.results)
	}
	if repo.loadedTimeframe != "1d" {
		t.Fatalf("expected rule chain to load with timeframe 1d, got %q", repo.loadedTimeframe)
	}
}
