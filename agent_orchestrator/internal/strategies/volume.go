package strategies

import (
	"context"
	"fmt"
	"time"
)

type TodayVolumeExceedsPrior3MaxStrategy struct{}

type DailyPctChangeBandStrategy struct {
	name   string
	pctMin float64
	pctMax float64
}

func NewTodayVolumeExceedsPrior3Max() *TodayVolumeExceedsPrior3MaxStrategy {
	return &TodayVolumeExceedsPrior3MaxStrategy{}
}

func (s *TodayVolumeExceedsPrior3MaxStrategy) Key() string {
	return "TodayVolumeExceedsPrior3MaxStrategizer"
}

func (s *TodayVolumeExceedsPrior3MaxStrategy) Evaluate(ctx context.Context, input StrategyInput) (StrategyResult, error) {
	if err := ctx.Err(); err != nil {
		return StrategyResult{Name: s.Key(), Details: map[string]any{"error": true}}, err
	}

	todayVolume, maxPrior3, _, _, _ := computeDailyVolumeVsPrior3AndPctChange(input.Bars, input.CheckDate)
	if todayVolume == nil || maxPrior3 == nil {
		return StrategyResult{
			Name:      s.Key(),
			Satisfied: false,
			Reason:    "K线或成交量不足（需至少4根日线）",
			Details: map[string]any{
				"today_volume":      floatValue(todayVolume),
				"max_volume_prior3": floatValue(maxPrior3),
			},
		}, nil
	}

	satisfied := *todayVolume > *maxPrior3
	operator := "<="
	if satisfied {
		operator = ">"
	}
	return StrategyResult{
		Name:      s.Key(),
		Satisfied: satisfied,
		Reason:    fmt.Sprintf("当日成交量 %.0f %s 前三日最大 %.0f", *todayVolume, operator, *maxPrior3),
		Details: map[string]any{
			"today_volume":      *todayVolume,
			"max_volume_prior3": *maxPrior3,
		},
	}, nil
}

func NewDailyPctChangeBand(pctMin float64, pctMax float64, name ...string) *DailyPctChangeBandStrategy {
	strategyName := "DailyPctChangeBandStrategizer"
	if len(name) > 0 && name[0] != "" {
		strategyName = name[0]
	}
	return &DailyPctChangeBandStrategy{name: strategyName, pctMin: pctMin, pctMax: pctMax}
}

func (s *DailyPctChangeBandStrategy) Key() string {
	return s.name
}

func (s *DailyPctChangeBandStrategy) Evaluate(ctx context.Context, input StrategyInput) (StrategyResult, error) {
	if err := ctx.Err(); err != nil {
		return StrategyResult{Name: s.Key(), Details: map[string]any{"error": true}}, err
	}

	_, _, pctChange, previousClose, todayClose := computeDailyVolumeVsPrior3AndPctChange(input.Bars, input.CheckDate)
	if pctChange == nil {
		return StrategyResult{
			Name:      s.Key(),
			Satisfied: false,
			Reason:    "无法计算当日涨跌幅",
			Details: map[string]any{
				"pct_change": floatValue(pctChange),
				"prev_close": floatValue(previousClose),
				"close":      floatValue(todayClose),
			},
		}, nil
	}

	satisfied := s.pctMin <= *pctChange && *pctChange <= s.pctMax
	inText := "不在"
	if satisfied {
		inText = "在"
	}
	return StrategyResult{
		Name:      s.Key(),
		Satisfied: satisfied,
		Reason:    fmt.Sprintf("当日涨跌 %.2f%% %s [%.2f%%, %.2f%%]", *pctChange, inText, s.pctMin, s.pctMax),
		Details: map[string]any{
			"pct_change": *pctChange,
			"prev_close": *previousClose,
			"close":      *todayClose,
			"band_min":   s.pctMin,
			"band_max":   s.pctMax,
		},
	}, nil
}

func computeDailyVolumeVsPrior3AndPctChange(
	bars []Bar,
	checkDate time.Time,
) (*float64, *float64, *float64, *float64, *float64) {
	work := sortedValidCloseBars(bars)
	work = truncateByCheckDate(work, checkDate)
	if len(work) < 4 {
		return nil, nil, nil, nil, nil
	}

	prior3 := work[len(work)-4 : len(work)-1]
	today := work[len(work)-1]
	previous := work[len(work)-2]

	previousClose := previous.Close
	todayClose := today.Close
	if !validPositive(today.Volume) {
		return nil, nil, nil, floatPtr(previousClose), floatPtr(todayClose)
	}

	maxPrior3 := prior3[0].Volume
	for _, bar := range prior3 {
		if !validPositive(bar.Volume) {
			return nil, nil, nil, floatPtr(previousClose), floatPtr(todayClose)
		}
		if bar.Volume > maxPrior3 {
			maxPrior3 = bar.Volume
		}
	}

	if previousClose <= 0 {
		return floatPtr(today.Volume), floatPtr(maxPrior3), nil, floatPtr(previousClose), floatPtr(todayClose)
	}
	pctChange := (todayClose - previousClose) / previousClose * 100.0
	return floatPtr(today.Volume), floatPtr(maxPrior3), floatPtr(pctChange), floatPtr(previousClose), floatPtr(todayClose)
}
