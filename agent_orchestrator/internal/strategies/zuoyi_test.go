package strategies

import (
	"context"
	"testing"
	"time"
)

func testBars(rows [][3]float64) []Bar {
	bars := make([]Bar, 0, len(rows))
	start := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	for i, row := range rows {
		bars = append(bars, Bar{
			Time:   start.AddDate(0, 0, i),
			High:   row[0],
			Low:    row[1],
			Close:  row[2],
			Volume: float64(1000 + i),
		})
	}
	return bars
}

func TestZuoYiStrategyBullishBreakoutOnSecondBar(t *testing.T) {
	strategy := NewZuoYiStrategy(3, true, true)
	result, err := strategy.Evaluate(context.Background(), StrategyInput{
		Market:    "HK",
		Code:      "HK.00001",
		CheckDate: time.Date(2026, 1, 5, 0, 0, 0, 0, time.UTC),
		Bars: testBars([][3]float64{
			{10.0, 8.0, 9.0},
			{9.0, 8.5, 8.8},
			{9.2, 7.0, 7.4},
			{9.4, 7.3, 9.0},
			{10.5, 8.7, 10.2},
		}),
	})
	if err != nil {
		t.Fatalf("Evaluate returned error: %v", err)
	}
	if !result.Satisfied {
		t.Fatalf("expected bullish signal, got %#v", result)
	}
	if result.Details["result_type"] != "bullish_breakout" {
		t.Fatalf("unexpected result_type: %#v", result.Details["result_type"])
	}
	signals := result.Details["signals"].([]ZuoYiSignal)
	if len(signals) != 1 {
		t.Fatalf("expected one signal, got %d", len(signals))
	}
	signal := signals[0]
	if signal.Direction != "bullish" || signal.LeftOneDate != "2026-01-01" || signal.MedianDate != "2026-01-03" || signal.BreakoutDate != "2026-01-05" {
		t.Fatalf("unexpected signal: %#v", signal)
	}
	if signal.BarsToBreakout != 2 {
		t.Fatalf("expected bars_to_breakout=2, got %d", signal.BarsToBreakout)
	}
}

func TestZuoYiStrategyBearishBreakdownOnFifteenthBar(t *testing.T) {
	strategy := NewZuoYiStrategy(15, true, true)
	result, err := strategy.Evaluate(context.Background(), StrategyInput{
		Market:    "HK",
		Code:      "HK.00001",
		CheckDate: time.Date(2026, 1, 18, 0, 0, 0, 0, time.UTC),
		Bars: testBars([][3]float64{
			{12.0, 10.0, 11.0},
			{13.0, 10.8, 12.0},
			{14.0, 10.5, 13.8},
			{13.8, 10.6, 12.8},
			{13.7, 10.6, 12.7},
			{13.6, 10.6, 12.6},
			{13.5, 10.6, 12.5},
			{13.4, 10.5, 12.4},
			{13.3, 10.5, 12.3},
			{13.2, 10.5, 12.2},
			{13.1, 10.4, 12.1},
			{13.0, 10.4, 12.0},
			{12.9, 10.4, 11.9},
			{12.8, 10.3, 11.8},
			{12.7, 10.3, 11.7},
			{12.6, 10.2, 11.6},
			{12.5, 10.1, 11.5},
			{12.4, 9.5, 9.8},
		}),
	})
	if err != nil {
		t.Fatalf("Evaluate returned error: %v", err)
	}
	if !result.Satisfied || result.Details["result_type"] != "bearish_breakdown" {
		t.Fatalf("expected bearish breakdown, got %#v", result)
	}
	signals := result.Details["signals"].([]ZuoYiSignal)
	if signals[0].BreakoutDate != "2026-01-18" || signals[0].BarsToBreakout != 15 {
		t.Fatalf("unexpected signal: %#v", signals[0])
	}
}

func TestZuoYiStrategyCheckDateTruncatesFutureBreakout(t *testing.T) {
	strategy := NewZuoYiStrategy(3, true, true)
	result, err := strategy.Evaluate(context.Background(), StrategyInput{
		Market:    "HK",
		Code:      "HK.00001",
		CheckDate: time.Date(2026, 1, 4, 0, 0, 0, 0, time.UTC),
		Bars: testBars([][3]float64{
			{10.0, 8.0, 9.0},
			{9.0, 8.5, 8.8},
			{9.2, 7.0, 7.4},
			{9.4, 7.3, 9.0},
			{10.5, 8.7, 10.2},
		}),
	})
	if err != nil {
		t.Fatalf("Evaluate returned error: %v", err)
	}
	if result.Satisfied {
		t.Fatalf("expected no signal before breakout date, got %#v", result)
	}
	if result.Details["data_rows"] != 4 {
		t.Fatalf("expected data_rows=4, got %#v", result.Details["data_rows"])
	}
}
