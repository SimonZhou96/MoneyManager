package marketdata

import (
	"context"
	"time"

	"github.com/SimonZhou96/MoneyManager/agent_orchestrator/internal/strategies"
)

type Query struct {
	Market    string
	Code      string
	Timeframe string
	MaxCount  int
}

type Provider interface {
	FetchBars(ctx context.Context, query Query) ([]strategies.Bar, string, error)
}

type ProviderChain struct {
	providers []Provider
}

func NewProviderChain(providers ...Provider) *ProviderChain {
	return &ProviderChain{providers: providers}
}

func (c *ProviderChain) FetchBars(ctx context.Context, query Query) ([]strategies.Bar, string, error) {
	var lastErr error
	for _, provider := range c.providers {
		bars, source, err := provider.FetchBars(ctx, query)
		if err != nil {
			lastErr = err
			continue
		}
		if len(bars) > 0 {
			return normalizeBars(bars, query.MaxCount), source, nil
		}
	}
	return nil, "unavailable", lastErr
}

func normalizeBars(bars []strategies.Bar, maxCount int) []strategies.Bar {
	if len(bars) == 0 {
		return nil
	}
	out := append([]strategies.Bar(nil), bars...)
	sortBars(out)
	if maxCount > 0 && len(out) > maxCount {
		out = out[len(out)-maxCount:]
	}
	return out
}

func sortBars(bars []strategies.Bar) {
	for i := 1; i < len(bars); i++ {
		current := bars[i]
		j := i - 1
		for j >= 0 && bars[j].Time.After(current.Time) {
			bars[j+1] = bars[j]
			j--
		}
		bars[j+1] = current
	}
}

func BarTime(value time.Time) time.Time {
	return value.UTC().Truncate(time.Second)
}
