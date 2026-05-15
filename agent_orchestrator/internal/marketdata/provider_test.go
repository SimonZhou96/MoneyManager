package marketdata

import (
	"context"
	"testing"
	"time"

	"github.com/SimonZhou96/MoneyManager/agent_orchestrator/internal/strategies"
)

type fakeProvider struct {
	bars []strategies.Bar
	err  error
}

func (f fakeProvider) FetchBars(context.Context, Query) ([]strategies.Bar, string, error) {
	return f.bars, "fake", f.err
}

func TestProviderChainReturnsSortedTail(t *testing.T) {
	chain := NewProviderChain(fakeProvider{bars: []strategies.Bar{
		{Time: time.Date(2026, 1, 3, 0, 0, 0, 0, time.UTC), Close: 3},
		{Time: time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC), Close: 1},
		{Time: time.Date(2026, 1, 2, 0, 0, 0, 0, time.UTC), Close: 2},
	}})
	bars, source, err := chain.FetchBars(context.Background(), Query{MaxCount: 2})
	if err != nil {
		t.Fatalf("FetchBars: %v", err)
	}
	if source != "fake" || len(bars) != 2 || bars[0].Close != 2 || bars[1].Close != 3 {
		t.Fatalf("unexpected bars: source=%s bars=%#v", source, bars)
	}
}
