package strategies

import (
	"context"
	"strings"
)

type ZuoYiStrategy struct {
	signalWindow   int
	includeBullish bool
	includeBearish bool
}

type zuoyiAnalysis struct {
	satisfied   bool
	resultType  string
	reason      string
	signals     []ZuoYiSignal
	dataRows    int
	latestClose any
}

func NewZuoYiStrategy(signalWindow int, includeBullish bool, includeBearish bool) *ZuoYiStrategy {
	return &ZuoYiStrategy{
		signalWindow:   signalWindow,
		includeBullish: includeBullish,
		includeBearish: includeBearish,
	}
}

func (s *ZuoYiStrategy) Key() string {
	return "ZuoYiStrategizer"
}

func (s *ZuoYiStrategy) Evaluate(ctx context.Context, input StrategyInput) (StrategyResult, error) {
	if err := ctx.Err(); err != nil {
		return StrategyResult{Name: s.Key(), Details: map[string]any{"error": true}}, err
	}

	analysis := s.analyze(input)
	directions := make([]string, 0, len(analysis.signals))
	for _, signal := range analysis.signals {
		directions = append(directions, signal.Direction)
	}

	var direction any
	if len(directions) > 0 {
		direction = strings.Join(directions, "|")
	}

	return StrategyResult{
		Name:      s.Key(),
		Satisfied: analysis.satisfied,
		Reason:    analysis.reason,
		Details: map[string]any{
			"result_type":   analysis.resultType,
			"direction":     direction,
			"signals":       analysis.signals,
			"signal_window": s.signalWindow,
			"data_rows":     analysis.dataRows,
			"latest_close":  analysis.latestClose,
		},
	}, nil
}

func (s *ZuoYiStrategy) analyze(input StrategyInput) zuoyiAnalysis {
	if s.signalWindow <= 0 {
		return zuoyiAnalysis{
			resultType: "invalid_data",
			reason:     "signal_window 必须大于 0",
		}
	}

	work, reason := prepareZuoYiBars(input.Bars)
	if work == nil {
		return zuoyiAnalysis{
			resultType: "invalid_data",
			reason:     reason,
		}
	}

	work = truncateByCheckDate(work, input.CheckDate)
	if len(work) < 3 {
		return zuoyiAnalysis{
			resultType: "insufficient_data",
			reason:     "K线数据不足，至少需要左一、中位线、突破K线",
			dataRows:   len(work),
		}
	}

	signals := make([]ZuoYiSignal, 0, 2)
	if s.includeBullish {
		if signal, ok := findRecentZuoYiSignal(work, "bullish", findBullishMedianCandidates(work), s.signalWindow); ok {
			signals = append(signals, signal)
		}
	}
	if s.includeBearish {
		if signal, ok := findRecentZuoYiSignal(work, "bearish", findBearishMedianCandidates(work), s.signalWindow); ok {
			signals = append(signals, signal)
		}
	}

	latestClose := work[len(work)-1].Close
	if len(signals) == 0 {
		return zuoyiAnalysis{
			resultType:  "no_signal",
			reason:      "最近" + intString(s.signalWindow) + "根K线内无左一战法有效突破",
			dataRows:    len(work),
			latestClose: latestClose,
			signals:     signals,
			satisfied:   false,
		}
	}

	hasBullish := false
	hasBearish := false
	for _, signal := range signals {
		if signal.Direction == "bullish" {
			hasBullish = true
		}
		if signal.Direction == "bearish" {
			hasBearish = true
		}
	}

	resultType := "bearish_breakdown"
	reason = "左一战法看跌：收盘价在" + intString(s.signalWindow) + "根K线内跌破左一低点"
	if hasBullish && hasBearish {
		resultType = "both_breakout"
		reason = "同时出现左一战法看涨与看跌信号"
	} else if hasBullish {
		resultType = "bullish_breakout"
		reason = "左一战法看涨：收盘价在" + intString(s.signalWindow) + "根K线内突破左一高点"
	}

	return zuoyiAnalysis{
		satisfied:   true,
		resultType:  resultType,
		reason:      reason,
		signals:     signals,
		dataRows:    len(work),
		latestClose: latestClose,
	}
}

func prepareZuoYiBars(bars []Bar) ([]Bar, string) {
	if len(bars) == 0 {
		return nil, "K线数据为空"
	}
	sorted := sortedBars(bars)
	work := make([]Bar, 0, len(sorted))
	for _, bar := range sorted {
		if validBarTime(bar.Time) &&
			validPositive(bar.High) &&
			validPositive(bar.Low) &&
			validPositive(bar.Close) &&
			bar.High >= bar.Low {
			work = append(work, bar)
		}
	}
	if len(work) == 0 {
		return nil, "无有效 high/low/close K线"
	}
	return work, ""
}

func hasKlineContainment(left Bar, right Bar) bool {
	leftContainsRight := left.High >= right.High && left.Low <= right.Low
	rightContainsLeft := right.High >= left.High && right.Low <= left.Low
	return leftContainsRight || rightContainsLeft
}

func findLeftOneIndex(bars []Bar, medianIdx int) (int, bool) {
	median := bars[medianIdx]
	for idx := medianIdx - 1; idx >= 0; idx-- {
		if !hasKlineContainment(bars[idx], median) {
			return idx, true
		}
	}
	return 0, false
}

func findBullishMedianCandidates(bars []Bar) []int {
	candidates := make([]int, 0)
	var lowestLow float64
	var sameLowBestHigh float64
	for idx, bar := range bars {
		if idx == 0 {
			lowestLow = bar.Low
			sameLowBestHigh = bar.High
			continue
		}
		if bar.Low < lowestLow {
			lowestLow = bar.Low
			sameLowBestHigh = bar.High
			candidates = append(candidates, idx)
		} else if bar.Low == lowestLow && bar.High > sameLowBestHigh {
			sameLowBestHigh = bar.High
			candidates = append(candidates, idx)
		}
	}
	return candidates
}

func findBearishMedianCandidates(bars []Bar) []int {
	candidates := make([]int, 0)
	var highestHigh float64
	var sameHighBestLow float64
	for idx, bar := range bars {
		if idx == 0 {
			highestHigh = bar.High
			sameHighBestLow = bar.Low
			continue
		}
		if bar.High > highestHigh {
			highestHigh = bar.High
			sameHighBestLow = bar.Low
			candidates = append(candidates, idx)
		} else if bar.High == highestHigh && bar.Low < sameHighBestLow {
			sameHighBestLow = bar.Low
			candidates = append(candidates, idx)
		}
	}
	return candidates
}

func findRecentZuoYiSignal(bars []Bar, direction string, medianCandidates []int, signalWindow int) (ZuoYiSignal, bool) {
	latestIdx := len(bars) - 1
	for i := len(medianCandidates) - 1; i >= 0; i-- {
		medianIdx := medianCandidates[i]
		if medianIdx >= latestIdx {
			continue
		}

		leftOneIdx, ok := findLeftOneIndex(bars, medianIdx)
		if !ok {
			continue
		}

		leftOne := bars[leftOneIdx]
		endIdx := medianIdx + signalWindow
		if endIdx > latestIdx {
			endIdx = latestIdx
		}
		for breakoutIdx := medianIdx + 1; breakoutIdx <= endIdx; breakoutIdx++ {
			if latestIdx-breakoutIdx >= signalWindow {
				continue
			}
			closePrice := bars[breakoutIdx].Close
			if direction == "bullish" && closePrice > leftOne.High {
				return buildZuoYiSignal(bars, direction, medianIdx, leftOneIdx, breakoutIdx), true
			}
			if direction == "bearish" && closePrice < leftOne.Low {
				return buildZuoYiSignal(bars, direction, medianIdx, leftOneIdx, breakoutIdx), true
			}
		}
	}
	return ZuoYiSignal{}, false
}

func buildZuoYiSignal(bars []Bar, direction string, medianIdx int, leftOneIdx int, breakoutIdx int) ZuoYiSignal {
	leftOne := bars[leftOneIdx]
	median := bars[medianIdx]
	breakout := bars[breakoutIdx]
	latest := bars[len(bars)-1]
	return ZuoYiSignal{
		Direction:      direction,
		LeftOneDate:    dateString(leftOne.Time),
		MedianDate:     dateString(median.Time),
		BreakoutDate:   dateString(breakout.Time),
		BarsToBreakout: breakoutIdx - medianIdx,
		LeftOneHigh:    leftOne.High,
		LeftOneLow:     leftOne.Low,
		MedianHigh:     median.High,
		MedianLow:      median.Low,
		BreakoutClose:  breakout.Close,
		LatestClose:    latest.Close,
	}
}

func intString(value int) string {
	if value == 0 {
		return "0"
	}
	digits := make([]byte, 0, 12)
	negative := value < 0
	if negative {
		value = -value
	}
	for value > 0 {
		digits = append(digits, byte('0'+value%10))
		value /= 10
	}
	if negative {
		digits = append(digits, '-')
	}
	for i, j := 0, len(digits)-1; i < j; i, j = i+1, j-1 {
		digits[i], digits[j] = digits[j], digits[i]
	}
	return string(digits)
}
