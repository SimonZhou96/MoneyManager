package rules

import (
	"context"
	"fmt"
	"sort"

	"github.com/SimonZhou96/MoneyManager/agent_orchestrator/internal/strategies"
)

var klineImplementations = map[string]struct{}{
	"AvgDailyVolumeFilter":                   {},
	"PriceFilter":                            {},
	"ZuoYiStrategizer":                       {},
	"EMABreakoutStrategizer":                 {},
	"RSIOversoldStrategizer":                 {},
	"RSIOverboughtStrategizer":               {},
	"TodayVolumeExceedsPrior3MaxStrategizer": {},
	"DailyDrop6To65Strategizer":              {},
	"DailyRise4To45Strategizer":              {},
}

type Engine struct {
	metadata           []RuleMetadata
	chain              RuleChainConfig
	registry           *Registry
	metadataByKey      map[string]RuleMetadata
	referencedRuleKeys map[string]struct{}
}

func NewEngine(metadata []RuleMetadata, chain RuleChainConfig, registry *Registry) *Engine {
	items := append([]RuleMetadata(nil), metadata...)
	sort.SliceStable(items, func(i, j int) bool {
		return items[i].DisplayOrder < items[j].DisplayOrder
	})
	metadataByKey := make(map[string]RuleMetadata, len(items))
	for _, item := range items {
		if item.Params == nil {
			item.Params = map[string]any{}
		}
		metadataByKey[item.RuleKey] = item
	}
	if registry == nil {
		registry = DefaultRegistry()
	}
	return &Engine{
		metadata:           items,
		chain:              chain,
		registry:           registry,
		metadataByKey:      metadataByKey,
		referencedRuleKeys: collectRuleKeys(chain.Expression),
	}
}

func (e *Engine) HasRules() bool {
	return e != nil && len(e.referencedRuleKeys) > 0
}

func (e *Engine) RequiresKline() bool {
	if e == nil {
		return false
	}
	for ruleKey := range e.referencedRuleKeys {
		metadata, ok := e.metadataByKey[ruleKey]
		if !ok || !metadata.Enabled {
			continue
		}
		if _, ok := klineImplementations[metadata.Implementation]; ok {
			return true
		}
	}
	return false
}

func (e *Engine) EvaluateStock(ctx context.Context, input EvaluationInput) StockResult {
	if e == nil {
		return NewStockResult(input.Stock, false, nil)
	}
	execution := newExecutionContext(e.registry, e.metadataByKey, input)
	passed := evaluateExpression(ctx, e.chain.Expression, execution)
	return NewStockResult(input.Stock, passed, execution.orderedOutputs)
}

type executionContext struct {
	registry       *Registry
	metadataByKey  map[string]RuleMetadata
	input          EvaluationInput
	truthByKey     map[string]bool
	outputByKey    map[string]RuleOutput
	orderedOutputs []RuleOutput
}

func newExecutionContext(registry *Registry, metadataByKey map[string]RuleMetadata, input EvaluationInput) *executionContext {
	return &executionContext{
		registry:      registry,
		metadataByKey: metadataByKey,
		input:         input,
		truthByKey:    map[string]bool{},
		outputByKey:   map[string]RuleOutput{},
	}
}

func (c *executionContext) execute(ctx context.Context, ruleKey string) bool {
	if truth, ok := c.truthByKey[ruleKey]; ok {
		return truth
	}

	metadata, ok := c.metadataByKey[ruleKey]
	if !ok || !metadata.Enabled {
		c.truthByKey[ruleKey] = false
		return false
	}

	output, truth := c.evaluateRule(ctx, metadata)
	output.RuleKey = ruleKey
	c.outputByKey[ruleKey] = output
	c.truthByKey[ruleKey] = truth
	c.orderedOutputs = append(c.orderedOutputs, output)
	return truth
}

func (c *executionContext) evaluateRule(ctx context.Context, metadata RuleMetadata) (RuleOutput, bool) {
	switch metadata.RuleType {
	case RuleTypeFilter:
		filter, err := c.registry.CreateFilter(metadata)
		if err != nil {
			return errorOutput(metadata, err), false
		}
		output := filter.Evaluate(ctx, c.input)
		return output, output.Result == ResultPass || output.Result == ResultSkip
	case RuleTypeStrategy:
		strategy, err := c.registry.CreateStrategy(metadata)
		if err != nil {
			return errorOutput(metadata, err), false
		}
		result, err := strategy.Evaluate(ctx, strategies.StrategyInput{
			Market:    c.input.Market(),
			Code:      c.input.Code(),
			CheckDate: c.input.CheckDate,
			Bars:      c.input.effectiveBars(),
		})
		if err != nil {
			return errorOutput(metadata, err), false
		}
		return strategyOutput(result), result.Satisfied
	default:
		err := fmt.Errorf("unsupported rule type: %s", metadata.RuleType)
		return errorOutput(metadata, err), false
	}
}

func strategyOutput(result strategies.StrategyResult) RuleOutput {
	status := ResultFail
	if result.Satisfied {
		status = ResultPass
	}
	return NewRuleOutput("", result.Name, status, result.Reason, result.Details)
}

func errorOutput(metadata RuleMetadata, err error) RuleOutput {
	name := metadata.Implementation
	if name == "" {
		name = metadata.RuleKey
	}
	return NewRuleOutput(metadata.RuleKey, name, ResultError, err.Error(), map[string]any{
		"error":    true,
		"rule_key": metadata.RuleKey,
	})
}
