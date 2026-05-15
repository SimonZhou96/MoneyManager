package screening

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"strings"
	"time"

	"github.com/SimonZhou96/MoneyManager/agent_orchestrator/internal/rules"
	"github.com/SimonZhou96/MoneyManager/agent_orchestrator/internal/strategies"
	"github.com/SimonZhou96/MoneyManager/agent_orchestrator/internal/workflow"
)

type Repository interface {
	LoadRuleMetadata(ctx context.Context, market string) ([]rules.RuleMetadata, error)
	LoadRuleChain(ctx context.Context, market string, timeframe string, chainKey string) (rules.RuleChainConfig, error)
	ListPoolStocks(ctx context.Context, market string) ([]rules.Stock, error)
	ListStocksByCodes(ctx context.Context, market string, codes []string) ([]rules.Stock, error)
	CreateScreeningTask(ctx context.Context, task Task) error
	UpdateTaskProgress(ctx context.Context, taskID string, completed int, code string, name string) error
	UpdateTaskStatus(ctx context.Context, taskID string, status string) error
	UpsertScreeningResult(ctx context.Context, result Result) error
}

type MarketDataProvider interface {
	FetchBars(ctx context.Context, market string, code string, timeframe string, maxCount int) ([]strategies.Bar, string, error)
}

type Task struct {
	TaskID     string
	Market     string
	Timeframe  string
	TotalCount int
	Params     map[string]any
	CheckDate  time.Time
}

type Result struct {
	TaskID        string
	Market        string
	Code          string
	Name          string
	CheckDate     time.Time
	Passed        bool
	FilterSummary string
	FilterDetails []map[string]any
	Sector        string
	Industry      string
	MarketCap     *float64
	PERatio       *float64
	ClosePrice    *float64
}

type Executor struct {
	repository Repository
	marketData MarketDataProvider
	registry   *rules.Registry
	checkDate  func() time.Time
}

func NewExecutor(repository Repository, marketData MarketDataProvider, registry *rules.Registry) *Executor {
	if registry == nil {
		registry = rules.DefaultRegistry()
	}
	return &Executor{
		repository: repository,
		marketData: marketData,
		registry:   registry,
		checkDate:  func() time.Time { return time.Now() },
	}
}

func (e *Executor) ExecuteRules(ctx context.Context, state workflow.State) (workflow.State, error) {
	if e == nil || e.repository == nil {
		return state, fmt.Errorf("screening executor repository is not configured")
	}
	state.Trace = append(state.Trace, "execute_rules")
	markets := state.Markets
	if len(markets) == 0 {
		return state.WithWarning("no markets selected"), nil
	}
	timeframe := state.Timeframe
	if timeframe == "" {
		timeframe = "1d"
	}
	for _, market := range markets {
		result, err := e.executeMarket(ctx, market, timeframe, state.ChainKey, targetCode(state))
		if err != nil {
			state.MarketResults = append(state.MarketResults, workflow.MarketResult{
				Market:  market,
				Status:  "failed",
				Warning: err.Error(),
			})
			state.Warnings = append(state.Warnings, fmt.Sprintf("%s: %v", market, err))
			continue
		}
		state.MarketResults = append(state.MarketResults, result)
	}
	return state, nil
}

func (e *Executor) executeMarket(ctx context.Context, market string, timeframe string, chainKey string, code string) (workflow.MarketResult, error) {
	metadata, err := e.repository.LoadRuleMetadata(ctx, market)
	if err != nil {
		return workflow.MarketResult{}, err
	}
	if len(metadata) == 0 {
		metadata = rules.DefaultRuleMetadata(market)
	}
	chain, err := e.repository.LoadRuleChain(ctx, market, timeframe, chainKey)
	if err != nil {
		return workflow.MarketResult{}, err
	}
	if len(chain.Expression) == 0 {
		chain = rules.DefaultRuleChainConfig(market)
	}
	engine := rules.NewEngine(metadata, chain, e.registry)
	stocks, err := e.loadStocks(ctx, market, code)
	if err != nil {
		return workflow.MarketResult{}, err
	}
	if len(stocks) == 0 {
		return workflow.MarketResult{Market: market, Status: "skipped", Warning: "no stock pool data"}, nil
	}

	checkDate := dateOnly(e.checkDate())
	taskID := newTaskID()
	if err := e.repository.CreateScreeningTask(ctx, Task{
		TaskID:     taskID,
		Market:     market,
		Timeframe:  timeframe,
		TotalCount: len(stocks),
		CheckDate:  checkDate,
		Params: map[string]any{
			"use_db_rule_engine": true,
			"chain_key":          chain.ChainKey,
		},
	}); err != nil {
		return workflow.MarketResult{}, err
	}

	passed := 0
	for i, stock := range stocks {
		bars := stock.Bars
		if engine.RequiresKline() && e.marketData != nil {
			fetched, _, err := e.marketData.FetchBars(ctx, market, stock.Code, timeframe, 500)
			if err == nil && len(fetched) > 0 {
				bars = fetched
			}
		}
		eval := engine.EvaluateStock(ctx, rules.NewEvaluationInput(stock, checkDate, timeframe, bars))
		if eval.Passed {
			passed++
		}
		_ = e.repository.UpdateTaskProgress(ctx, taskID, i+1, stock.Code, stock.Name)
		if err := e.repository.UpsertScreeningResult(ctx, resultFromEvaluation(taskID, market, checkDate, eval)); err != nil {
			_ = e.repository.UpdateTaskStatus(ctx, taskID, "failed")
			return workflow.MarketResult{}, err
		}
	}
	if err := e.repository.UpdateTaskStatus(ctx, taskID, "completed"); err != nil {
		return workflow.MarketResult{}, err
	}
	return workflow.MarketResult{
		Market:      market,
		TaskID:      taskID,
		PassedCount: passed,
		TotalCount:  len(stocks),
		Status:      "completed",
	}, nil
}

func (e *Executor) loadStocks(ctx context.Context, market string, code string) ([]rules.Stock, error) {
	code = strings.TrimSpace(code)
	if code != "" {
		return e.repository.ListStocksByCodes(ctx, market, []string{code})
	}
	return e.repository.ListPoolStocks(ctx, market)
}

func resultFromEvaluation(taskID string, market string, checkDate time.Time, eval rules.StockResult) Result {
	details := make([]map[string]any, 0, len(eval.Outputs))
	closePrice := (*float64)(nil)
	for _, output := range eval.Outputs {
		item := map[string]any{
			"filter_name": output.Name,
			"result":      string(output.Result),
			"reason":      output.Reason,
			"details":     output.Details,
		}
		details = append(details, item)
		if closePrice == nil {
			if value, ok := output.Details["price"].(float64); ok {
				closePrice = &value
			} else if value, ok := output.Details["latest_close"].(float64); ok {
				closePrice = &value
			}
		}
	}
	return Result{
		TaskID:        taskID,
		Market:        market,
		Code:          eval.Stock.Code,
		Name:          eval.Stock.Name,
		CheckDate:     checkDate,
		Passed:        eval.Passed,
		FilterSummary: summary(eval.Outputs),
		FilterDetails: details,
		Sector:        eval.Stock.Sector,
		Industry:      eval.Stock.Industry,
		MarketCap:     optional(eval.Stock.MarketCap, eval.Stock.HasMarketCap),
		PERatio:       optional(eval.Stock.PERatio, eval.Stock.HasPERatio),
		ClosePrice:    closePrice,
	}
}

func summary(outputs []rules.RuleOutput) string {
	failed := make([]string, 0)
	for _, output := range outputs {
		if output.Result == rules.ResultFail || output.Result == rules.ResultError {
			failed = append(failed, output.Name)
		}
	}
	if len(failed) == 0 {
		return "全部规则通过"
	}
	return "未通过: " + strings.Join(failed, ", ")
}

func optional(value float64, ok bool) *float64 {
	if !ok && value == 0 {
		return nil
	}
	return &value
}

func dateOnly(value time.Time) time.Time {
	if value.IsZero() {
		value = time.Now()
	}
	y, m, d := value.Date()
	return time.Date(y, m, d, 0, 0, 0, 0, value.Location())
}

func newTaskID() string {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		return fmt.Sprintf("go-%d", time.Now().UnixNano())
	}
	return hex.EncodeToString(b[:])
}

func targetCode(state workflow.State) string {
	if strings.TrimSpace(state.NormalizedCode) != "" {
		return state.NormalizedCode
	}
	return state.Code
}
