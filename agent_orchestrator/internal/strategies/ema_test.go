package strategies

import (
	"context"
	"testing"
	"time"
)

func TestEMABreakoutStrategyDetectsRecentBreakout(t *testing.T) {
	bars := make([]Bar, 0, 152)
	start := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	for i := 0; i < 150; i++ {
		bars = append(bars, Bar{Time: start.AddDate(0, 0, i), High: 11, Low: 9, Close: 10, Volume: 1000})
	}
	bars = append(bars,
		Bar{Time: start.AddDate(0, 0, 150), High: 51, Low: 49, Close: 50, Volume: 1000},
		Bar{Time: start.AddDate(0, 0, 151), High: 51, Low: 49, Close: 50, Volume: 1000},
	)

	strategy := NewEMABreakoutStrategy(10, 150)
	result, err := strategy.Evaluate(context.Background(), StrategyInput{
		Market:    "HK",
		Code:      "HK.00001",
		CheckDate: start.AddDate(0, 0, 151),
		Bars:      bars,
	})
	if err != nil {
		t.Fatalf("Evaluate returned error: %v", err)
	}
	if strategy.Key() != "EMABreakoutStrategizer" {
		t.Fatalf("unexpected key: %s", strategy.Key())
	}
	if !result.Satisfied || result.Details["result_type"] != "breakout_t2" {
		t.Fatalf("expected T-2 EMA breakout, got %#v", result)
	}
	if result.Details["breakout_date"] != "2026-05-31" {
		t.Fatalf("unexpected breakout_date: %#v", result.Details["breakout_date"])
	}
}
