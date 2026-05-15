package strategies

import (
	"context"
	"fmt"
	"time"
)

const (
	emaBreakoutT1             = "breakout_t1"
	emaBreakoutT2             = "breakout_t2"
	emaNoBreakoutBelow        = "no_breakout_below"
	emaNoBreakoutAlreadyAbove = "no_breakout_already_above"
	emaInsufficientData       = "insufficient_data"
	emaInvalidData            = "invalid_data"
)

type EMABreakoutStrategy struct {
	emaShort int
	emaLong  int
}

type emaPoint struct {
	bar      Bar
	emaShort float64
	emaLong  float64
}

func NewEMABreakoutStrategy(emaShort int, emaLong int) *EMABreakoutStrategy {
	if emaShort <= 0 {
		emaShort = 10
	}
	if emaLong <= 0 {
		emaLong = 150
	}
	return &EMABreakoutStrategy{emaShort: emaShort, emaLong: emaLong}
}

func (s *EMABreakoutStrategy) Key() string {
	return "EMABreakoutStrategizer"
}

func (s *EMABreakoutStrategy) Evaluate(ctx context.Context, input StrategyInput) (StrategyResult, error) {
	if err := ctx.Err(); err != nil {
		return StrategyResult{Name: s.Key(), Details: map[string]any{"error": true}}, err
	}

	resultType, breakoutDate, emaShortValue, emaLongValue := checkEMABreakout(
		input.Bars,
		input.CheckDate,
		s.emaShort,
		s.emaLong,
	)
	details := map[string]any{
		"result_type":                    resultType,
		"breakout_date":                  nil,
		fmt.Sprintf("ema%d", s.emaShort): floatValue(emaShortValue),
		fmt.Sprintf("ema%d", s.emaLong):  floatValue(emaLongValue),
	}
	if breakoutDate != nil {
		details["breakout_date"] = dateString(*breakoutDate)
	}

	return StrategyResult{
		Name:      s.Key(),
		Satisfied: resultType == emaBreakoutT1 || resultType == emaBreakoutT2,
		Reason:    emaResultDescription(resultType, s.emaShort, s.emaLong),
		Details:   details,
	}, nil
}

func checkEMABreakout(bars []Bar, checkDate time.Time, emaShort int, emaLong int) (string, *time.Time, *float64, *float64) {
	if emaShort <= 0 || emaLong <= 0 {
		return emaInvalidData, nil, nil, nil
	}

	work := sortedValidCloseBars(bars)
	if len(work) == 0 {
		return emaInvalidData, nil, nil, nil
	}

	if len(work) < emaLong+2 {
		return emaInsufficientData, nil, nil, nil
	}

	shortValues := calculateEMA(work, emaShort)
	longValues := calculateEMA(work, emaLong)
	points := make([]emaPoint, 0, len(work))
	for idx, bar := range work {
		points = append(points, emaPoint{bar: bar, emaShort: shortValues[idx], emaLong: longValues[idx]})
	}

	points = truncateEMAPoints(points, checkDate)
	if len(points) < 3 {
		return emaInsufficientData, nil, nil, nil
	}

	recent := points[len(points)-3:]
	t2, t1, t0 := recent[0], recent[1], recent[2]
	emaShortValue := t0.emaShort
	emaLongValue := t0.emaLong

	if crossedUp(t1, t0) {
		return emaBreakoutT1, &t0.bar.Time, &emaShortValue, &emaLongValue
	}
	if crossedUp(t2, t1) {
		return emaBreakoutT2, &t1.bar.Time, &emaShortValue, &emaLongValue
	}
	if emaShortValue >= emaLongValue {
		return emaNoBreakoutAlreadyAbove, nil, &emaShortValue, &emaLongValue
	}
	return emaNoBreakoutBelow, nil, &emaShortValue, &emaLongValue
}

func calculateEMA(bars []Bar, period int) []float64 {
	values := make([]float64, len(bars))
	if len(bars) == 0 {
		return values
	}
	alpha := 2.0 / float64(period+1)
	values[0] = bars[0].Close
	for idx := 1; idx < len(bars); idx++ {
		values[idx] = alpha*bars[idx].Close + (1-alpha)*values[idx-1]
	}
	return values
}

func truncateEMAPoints(points []emaPoint, checkDate time.Time) []emaPoint {
	if checkDate.IsZero() {
		return points
	}
	out := make([]emaPoint, 0, len(points))
	for _, point := range points {
		if dateOnOrBefore(point.bar.Time, checkDate) {
			out = append(out, point)
		}
	}
	return out
}

func crossedUp(prev emaPoint, curr emaPoint) bool {
	return prev.emaShort <= prev.emaLong && curr.emaShort > curr.emaLong
}

func emaResultDescription(resultType string, emaShort int, emaLong int) string {
	switch resultType {
	case emaBreakoutT1:
		return fmt.Sprintf("前一个交易日EMA%d向上突破EMA%d", emaShort, emaLong)
	case emaBreakoutT2:
		return fmt.Sprintf("前两个交易日EMA%d向上突破EMA%d", emaShort, emaLong)
	case emaNoBreakoutBelow:
		return fmt.Sprintf("EMA%d仍在EMA%d下方，未发生突破", emaShort, emaLong)
	case emaNoBreakoutAlreadyAbove:
		return fmt.Sprintf("EMA%d早已在EMA%d上方（超过2个K线柱）", emaShort, emaLong)
	case emaInsufficientData:
		return "K线数据不足，无法计算EMA"
	case emaInvalidData:
		return "K线数据异常"
	default:
		return "未知状态"
	}
}
