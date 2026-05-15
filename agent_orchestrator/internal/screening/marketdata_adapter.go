package screening

import (
	"context"

	"github.com/SimonZhou96/MoneyManager/agent_orchestrator/internal/marketdata"
	"github.com/SimonZhou96/MoneyManager/agent_orchestrator/internal/strategies"
)

type MarketDataAdapter struct {
	provider marketdata.Provider
}

func NewMarketDataAdapter(provider marketdata.Provider) *MarketDataAdapter {
	return &MarketDataAdapter{provider: provider}
}

func (a *MarketDataAdapter) FetchBars(ctx context.Context, market string, code string, timeframe string, maxCount int) ([]strategies.Bar, string, error) {
	if a == nil || a.provider == nil {
		return nil, "unavailable", nil
	}
	return a.provider.FetchBars(ctx, marketdata.Query{
		Market:    market,
		Code:      code,
		Timeframe: timeframe,
		MaxCount:  maxCount,
	})
}
