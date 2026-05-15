package rules

import (
	"context"
	"fmt"

	"github.com/SimonZhou96/MoneyManager/agent_orchestrator/internal/strategies"
)

type FilterEvaluator interface {
	Evaluate(context.Context, EvaluationInput) RuleOutput
}

type FilterFactory func(map[string]any) FilterEvaluator
type StrategyFactory func(map[string]any) strategies.Strategy

type Registry struct {
	filterFactories   map[string]FilterFactory
	strategyFactories map[string]StrategyFactory
}

func NewRegistry() *Registry {
	return &Registry{
		filterFactories:   map[string]FilterFactory{},
		strategyFactories: map[string]StrategyFactory{},
	}
}

func DefaultRegistry() *Registry {
	registry := NewRegistry()
	registry.RegisterFilter("MarketCapFilter", func(params map[string]any) FilterEvaluator {
		return NewMarketCapFilter(optionalFloat(params, "min_cap"), optionalFloat(params, "max_cap"))
	})
	registry.RegisterFilter("AvgDailyVolumeFilter", func(params map[string]any) FilterEvaluator {
		return NewAvgDailyVolumeFilter(optionalFloat(params, "min_volume"), optionalFloat(params, "max_volume"))
	})
	registry.RegisterFilter("PriceFilter", func(params map[string]any) FilterEvaluator {
		return NewPriceFilter(optionalFloat(params, "min_price"), optionalFloat(params, "max_price"))
	})
	registry.RegisterFilter("PEFilter", func(params map[string]any) FilterEvaluator {
		return NewPEFilter(optionalFloat(params, "min_pe"), optionalFloat(params, "max_pe"), boolParam(params, "allow_negative", false))
	})
	registry.RegisterFilter("ProfitabilityFilter", func(params map[string]any) FilterEvaluator {
		return NewProfitabilityFilter(boolParam(params, "require_profitable", true))
	})

	registry.RegisterStrategy("ZuoYiStrategizer", func(params map[string]any) strategies.Strategy {
		return strategies.NewZuoYiStrategy(
			intParam(params, "signal_window", 15),
			boolParam(params, "include_bullish", true),
			boolParam(params, "include_bearish", true),
		)
	})
	registry.RegisterStrategy("EMABreakoutStrategizer", func(params map[string]any) strategies.Strategy {
		return strategies.NewEMABreakoutStrategy(
			intParam(params, "ema_short", 10),
			intParam(params, "ema_long", 150),
		)
	})
	registry.RegisterStrategy("RSIOversoldStrategizer", func(params map[string]any) strategies.Strategy {
		return strategies.NewRSIOversold(
			intParam(params, "period", 14),
			floatParam(params, "threshold", 30.0),
		)
	})
	registry.RegisterStrategy("RSIOverboughtStrategizer", func(params map[string]any) strategies.Strategy {
		return strategies.NewRSIOverbought(
			intParam(params, "period", 14),
			floatParam(params, "threshold", 70.0),
		)
	})
	registry.RegisterStrategy("TodayVolumeExceedsPrior3MaxStrategizer", func(params map[string]any) strategies.Strategy {
		return strategies.NewTodayVolumeExceedsPrior3Max()
	})
	registry.RegisterStrategy("DailyDrop6To65Strategizer", func(params map[string]any) strategies.Strategy {
		return namedStrategy{
			Strategy: strategies.NewDailyPctChangeBand(floatParam(params, "pct_min", -6.5), floatParam(params, "pct_max", -6.0)),
			key:      "DailyDrop6To65Strategizer",
		}
	})
	registry.RegisterStrategy("DailyRise4To45Strategizer", func(params map[string]any) strategies.Strategy {
		return namedStrategy{
			Strategy: strategies.NewDailyPctChangeBand(floatParam(params, "pct_min", 4.0), floatParam(params, "pct_max", 4.5)),
			key:      "DailyRise4To45Strategizer",
		}
	})
	return registry
}

func (r *Registry) RegisterFilter(implementation string, factory FilterFactory) *Registry {
	if r == nil {
		return r
	}
	r.filterFactories[implementation] = factory
	return r
}

func (r *Registry) RegisterStrategy(implementation string, factory StrategyFactory) *Registry {
	if r == nil {
		return r
	}
	r.strategyFactories[implementation] = factory
	return r
}

func (r *Registry) CreateFilter(metadata RuleMetadata) (FilterEvaluator, error) {
	if r == nil {
		return nil, fmt.Errorf("rules registry is nil")
	}
	factory := r.filterFactories[metadata.Implementation]
	if factory == nil {
		return nil, fmt.Errorf("unregistered rule implementation: %s", metadata.Implementation)
	}
	return factory(cloneParams(metadata.Params)), nil
}

func (r *Registry) CreateStrategy(metadata RuleMetadata) (strategies.Strategy, error) {
	if r == nil {
		return nil, fmt.Errorf("rules registry is nil")
	}
	factory := r.strategyFactories[metadata.Implementation]
	if factory == nil {
		return nil, fmt.Errorf("unregistered rule implementation: %s", metadata.Implementation)
	}
	return factory(cloneParams(metadata.Params)), nil
}

func cloneParams(params map[string]any) map[string]any {
	out := map[string]any{}
	for key, value := range params {
		out[key] = value
	}
	return out
}

type namedStrategy struct {
	strategies.Strategy
	key string
}

func (s namedStrategy) Key() string {
	return s.key
}

func (s namedStrategy) Evaluate(ctx context.Context, input strategies.StrategyInput) (strategies.StrategyResult, error) {
	result, err := s.Strategy.Evaluate(ctx, input)
	result.Name = s.key
	return result, err
}
