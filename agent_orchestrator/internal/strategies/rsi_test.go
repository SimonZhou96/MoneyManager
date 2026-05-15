package strategies

import (
	"context"
	"testing"
	"time"
)

func rsiBars(closes ...float64) []Bar {
	bars := make([]Bar, 0, len(closes))
	start := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	for i, closePrice := range closes {
		bars = append(bars, Bar{
			Time:   start.AddDate(0, 0, i),
			High:   closePrice + 1,
			Low:    closePrice - 1,
			Close:  closePrice,
			Volume: 1000,
		})
	}
	return bars
}

func TestRSIStrategiesUseWilderLatestValue(t *testing.T) {
	oversold := NewRSIOversold(3, 30)
	oversoldResult, err := oversold.Evaluate(context.Background(), StrategyInput{Bars: rsiBars(10, 9, 8, 7, 6)})
	if err != nil {
		t.Fatalf("oversold Evaluate returned error: %v", err)
	}
	if oversold.Key() != "RSIOversoldStrategizer" {
		t.Fatalf("unexpected oversold key: %s", oversold.Key())
	}
	if !oversoldResult.Satisfied || oversoldResult.Details["rsi"] != 0.0 {
		t.Fatalf("expected oversold RSI=0, got %#v", oversoldResult)
	}

	overbought := NewRSIOverbought(3, 70)
	overboughtResult, err := overbought.Evaluate(context.Background(), StrategyInput{Bars: rsiBars(1, 2, 3, 4, 5)})
	if err != nil {
		t.Fatalf("overbought Evaluate returned error: %v", err)
	}
	if overbought.Key() != "RSIOverboughtStrategizer" {
		t.Fatalf("unexpected overbought key: %s", overbought.Key())
	}
	if !overboughtResult.Satisfied || overboughtResult.Details["rsi"] != 100.0 {
		t.Fatalf("expected overbought RSI=100, got %#v", overboughtResult)
	}
}
