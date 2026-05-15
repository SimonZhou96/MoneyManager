package strategies

import (
	"context"
	"fmt"
	"math"
	"time"
)

type RSIOversoldStrategy struct {
	period    int
	threshold float64
}

type RSIOverboughtStrategy struct {
	period    int
	threshold float64
}

func NewRSIOversold(period int, threshold float64) *RSIOversoldStrategy {
	if period <= 0 {
		period = 14
	}
	if threshold <= 0 {
		threshold = 30
	}
	return &RSIOversoldStrategy{period: period, threshold: threshold}
}

func NewRSIOverbought(period int, threshold float64) *RSIOverboughtStrategy {
	if period <= 0 {
		period = 14
	}
	if threshold <= 0 {
		threshold = 70
	}
	return &RSIOverboughtStrategy{period: period, threshold: threshold}
}

func (s *RSIOversoldStrategy) Key() string {
	return "RSIOversoldStrategizer"
}

func (s *RSIOversoldStrategy) Evaluate(ctx context.Context, input StrategyInput) (StrategyResult, error) {
	if err := ctx.Err(); err != nil {
		return StrategyResult{Name: s.Key(), Details: map[string]any{"error": true}}, err
	}

	rsi, ok := latestRSI(input.Bars, s.period, input.CheckDate)
	if !ok {
		return StrategyResult{
			Name:      s.Key(),
			Satisfied: false,
			Reason:    "RSI数据不足或无效",
			Details:   map[string]any{"rsi": nil, "period": s.period, "threshold": s.threshold},
		}, nil
	}

	satisfied := rsi <= s.threshold
	operator := ">"
	if satisfied {
		operator = "<="
	}
	return StrategyResult{
		Name:      s.Key(),
		Satisfied: satisfied,
		Reason:    fmt.Sprintf("RSI(%d)=%.2f %s %.2f", s.period, rsi, operator, s.threshold),
		Details:   map[string]any{"rsi": rsi, "period": s.period, "threshold": s.threshold},
	}, nil
}

func (s *RSIOverboughtStrategy) Key() string {
	return "RSIOverboughtStrategizer"
}

func (s *RSIOverboughtStrategy) Evaluate(ctx context.Context, input StrategyInput) (StrategyResult, error) {
	if err := ctx.Err(); err != nil {
		return StrategyResult{Name: s.Key(), Details: map[string]any{"error": true}}, err
	}

	rsi, ok := latestRSI(input.Bars, s.period, input.CheckDate)
	if !ok {
		return StrategyResult{
			Name:      s.Key(),
			Satisfied: false,
			Reason:    "RSI数据不足或无效",
			Details:   map[string]any{"rsi": nil, "period": s.period, "threshold": s.threshold},
		}, nil
	}

	satisfied := rsi >= s.threshold
	operator := "<"
	if satisfied {
		operator = ">="
	}
	return StrategyResult{
		Name:      s.Key(),
		Satisfied: satisfied,
		Reason:    fmt.Sprintf("RSI(%d)=%.2f %s %.2f", s.period, rsi, operator, s.threshold),
		Details:   map[string]any{"rsi": rsi, "period": s.period, "threshold": s.threshold},
	}, nil
}

func latestRSI(bars []Bar, period int, checkDate time.Time) (float64, bool) {
	if period <= 0 {
		return 0, false
	}
	work := sortedBars(bars)
	closes := make([]float64, 0, len(work))
	for _, bar := range work {
		if !validBarTime(bar.Time) || !validFinite(bar.Close) {
			continue
		}
		if !dateOnOrBefore(bar.Time, checkDate) {
			continue
		}
		closes = append(closes, bar.Close)
	}
	if len(closes) < period+1 {
		return 0, false
	}
	return calculateLatestRSI(closes, period)
}

func calculateLatestRSI(closes []float64, period int) (float64, bool) {
	if len(closes) < period+1 {
		return 0, false
	}
	alpha := 1.0 / float64(period)
	avgGain := 0.0
	avgLoss := 0.0
	for idx := 1; idx < len(closes); idx++ {
		delta := closes[idx] - closes[idx-1]
		gain := 0.0
		loss := 0.0
		if delta > 0 {
			gain = delta
		} else if delta < 0 {
			loss = -delta
		}
		avgGain = alpha*gain + (1-alpha)*avgGain
		avgLoss = alpha*loss + (1-alpha)*avgLoss
	}

	var rsi float64
	switch {
	case avgLoss == 0 && avgGain == 0:
		return 0, false
	case avgLoss == 0:
		rsi = 100
	default:
		rs := avgGain / avgLoss
		rsi = 100.0 - (100.0 / (1.0 + rs))
	}
	if math.IsNaN(rsi) || math.IsInf(rsi, 0) {
		return 0, false
	}
	return rsi, true
}
