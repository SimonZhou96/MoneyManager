package strategies

import (
	"context"
	"math"
	"sort"
	"time"
)

type Strategy interface {
	Key() string
	Evaluate(context.Context, StrategyInput) (StrategyResult, error)
}

type Bar struct {
	Time     time.Time
	Open     float64
	High     float64
	Low      float64
	Close    float64
	Volume   float64
	Turnover float64
}

type StrategyInput struct {
	Market    string
	Code      string
	CheckDate time.Time
	Bars      []Bar
}

type StrategyResult struct {
	Name      string
	Satisfied bool
	Reason    string
	Details   map[string]any
}

type ZuoYiSignal struct {
	Direction      string  `json:"direction"`
	LeftOneDate    string  `json:"left_one_date"`
	MedianDate     string  `json:"median_date"`
	BreakoutDate   string  `json:"breakout_date"`
	BarsToBreakout int     `json:"bars_to_breakout"`
	LeftOneHigh    float64 `json:"left_one_high"`
	LeftOneLow     float64 `json:"left_one_low"`
	MedianHigh     float64 `json:"median_high"`
	MedianLow      float64 `json:"median_low"`
	BreakoutClose  float64 `json:"breakout_close"`
	LatestClose    float64 `json:"latest_close"`
}

func sortedBars(bars []Bar) []Bar {
	if len(bars) == 0 {
		return nil
	}
	out := append([]Bar(nil), bars...)
	sort.SliceStable(out, func(i, j int) bool {
		return out[i].Time.Before(out[j].Time)
	})
	return out
}

func validPositive(value float64) bool {
	return value > 0 && !math.IsNaN(value) && !math.IsInf(value, 0)
}

func validFinite(value float64) bool {
	return !math.IsNaN(value) && !math.IsInf(value, 0)
}

func validBarTime(value time.Time) bool {
	return !value.IsZero()
}

func dateOnOrBefore(value time.Time, checkDate time.Time) bool {
	if checkDate.IsZero() {
		return true
	}
	vy, vm, vd := value.Date()
	cy, cm, cd := checkDate.Date()
	valueDate := time.Date(vy, vm, vd, 0, 0, 0, 0, time.UTC)
	check := time.Date(cy, cm, cd, 0, 0, 0, 0, time.UTC)
	return !valueDate.After(check)
}

func dateString(value time.Time) string {
	if value.IsZero() {
		return ""
	}
	return value.Format("2006-01-02")
}

func sortedValidCloseBars(bars []Bar) []Bar {
	sorted := sortedBars(bars)
	out := make([]Bar, 0, len(sorted))
	for _, bar := range sorted {
		if validBarTime(bar.Time) && validPositive(bar.Close) {
			out = append(out, bar)
		}
	}
	return out
}

func truncateByCheckDate(bars []Bar, checkDate time.Time) []Bar {
	if checkDate.IsZero() {
		return bars
	}
	out := make([]Bar, 0, len(bars))
	for _, bar := range bars {
		if dateOnOrBefore(bar.Time, checkDate) {
			out = append(out, bar)
		}
	}
	return out
}

func floatValue(value *float64) any {
	if value == nil {
		return nil
	}
	return *value
}

func floatPtr(value float64) *float64 {
	return &value
}
