package strategies

import (
	"context"
	"testing"
	"time"
)

func volumeBars(rows [][2]float64) []Bar {
	bars := make([]Bar, 0, len(rows))
	start := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	for i, row := range rows {
		closePrice := row[0]
		bars = append(bars, Bar{
			Time:   start.AddDate(0, 0, i),
			High:   closePrice + 1,
			Low:    closePrice - 1,
			Close:  closePrice,
			Volume: row[1],
		})
	}
	return bars
}

func TestTodayVolumeExceedsPrior3Max(t *testing.T) {
	strategy := NewTodayVolumeExceedsPrior3Max()
	result, err := strategy.Evaluate(context.Background(), StrategyInput{Bars: volumeBars([][2]float64{
		{10, 100},
		{10, 200},
		{10, 300},
		{10, 250},
		{11, 500},
	})})
	if err != nil {
		t.Fatalf("Evaluate returned error: %v", err)
	}
	if strategy.Key() != "TodayVolumeExceedsPrior3MaxStrategizer" {
		t.Fatalf("unexpected key: %s", strategy.Key())
	}
	if !result.Satisfied || result.Details["today_volume"] != 500.0 || result.Details["max_volume_prior3"] != 300.0 {
		t.Fatalf("expected volume breakout, got %#v", result)
	}
}

func TestDailyPctChangeBandUsesLatestDailyChange(t *testing.T) {
	bars := volumeBars([][2]float64{
		{10, 100},
		{10, 200},
		{10, 300},
		{10, 250},
		{11, 500},
	})

	strategy := NewDailyPctChangeBand(-2, 12)
	result, err := strategy.Evaluate(context.Background(), StrategyInput{Bars: bars})
	if err != nil {
		t.Fatalf("Evaluate returned error: %v", err)
	}
	if strategy.Key() != "DailyPctChangeBandStrategizer" {
		t.Fatalf("unexpected key: %s", strategy.Key())
	}
	if !result.Satisfied || result.Details["pct_change"] != 10.0 {
		t.Fatalf("expected pct change inside band, got %#v", result)
	}

	outside := NewDailyPctChangeBand(11, 20)
	outsideResult, err := outside.Evaluate(context.Background(), StrategyInput{Bars: bars})
	if err != nil {
		t.Fatalf("Evaluate returned error: %v", err)
	}
	if outsideResult.Satisfied {
		t.Fatalf("expected pct change outside band, got %#v", outsideResult)
	}
}
