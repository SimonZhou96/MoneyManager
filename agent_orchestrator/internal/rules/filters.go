package rules

import (
	"context"
	"fmt"
	"strings"

	"github.com/SimonZhou96/MoneyManager/agent_orchestrator/internal/strategies"
)

type MarketCapFilter struct {
	minCap *float64
	maxCap *float64
}

func NewMarketCapFilter(minCap, maxCap *float64) *MarketCapFilter {
	return &MarketCapFilter{minCap: minCap, maxCap: maxCap}
}

func (f *MarketCapFilter) Evaluate(_ context.Context, input EvaluationInput) RuleOutput {
	value, ok := stockNumber(input.Stock, "market_cap", input.Stock.MarketCap, input.Stock.HasMarketCap)
	if !ok {
		return passOutput("MarketCapFilter", "market cap data missing; treated as pass", map[string]any{"market_cap": nil})
	}
	if f.minCap != nil && value < *f.minCap {
		return failOutput("MarketCapFilter", fmt.Sprintf("market cap %.2f < min %.2f", value, *f.minCap), map[string]any{"market_cap": value, "min_cap": *f.minCap})
	}
	if f.maxCap != nil && value > *f.maxCap {
		return failOutput("MarketCapFilter", fmt.Sprintf("market cap %.2f > max %.2f", value, *f.maxCap), map[string]any{"market_cap": value, "max_cap": *f.maxCap})
	}
	return passOutput("MarketCapFilter", fmt.Sprintf("market cap %.2f in range", value), map[string]any{"market_cap": value})
}

type AvgDailyVolumeFilter struct {
	minVolume *float64
	maxVolume *float64
}

func NewAvgDailyVolumeFilter(minVolume, maxVolume *float64) *AvgDailyVolumeFilter {
	return &AvgDailyVolumeFilter{minVolume: minVolume, maxVolume: maxVolume}
}

func (f *AvgDailyVolumeFilter) Evaluate(_ context.Context, input EvaluationInput) RuleOutput {
	bars := input.effectiveBars()
	if len(bars) == 0 {
		return skipOutput("AvgDailyVolumeFilter", "kline data missing", nil)
	}
	avgVolume, ok := averageDailyVolume(bars, input.Timeframe)
	if !ok {
		return skipOutput("AvgDailyVolumeFilter", "unable to calculate average daily volume", nil)
	}
	if f.minVolume != nil && avgVolume < *f.minVolume {
		return failOutput("AvgDailyVolumeFilter", fmt.Sprintf("average daily volume %.0f < min %.0f", avgVolume, *f.minVolume), map[string]any{"avg_daily_volume": avgVolume, "min_volume": *f.minVolume})
	}
	if f.maxVolume != nil && avgVolume > *f.maxVolume {
		return failOutput("AvgDailyVolumeFilter", fmt.Sprintf("average daily volume %.0f > max %.0f", avgVolume, *f.maxVolume), map[string]any{"avg_daily_volume": avgVolume, "max_volume": *f.maxVolume})
	}
	return passOutput("AvgDailyVolumeFilter", fmt.Sprintf("average daily volume %.0f in range", avgVolume), map[string]any{"avg_daily_volume": avgVolume})
}

type PriceFilter struct {
	minPrice *float64
	maxPrice *float64
}

func NewPriceFilter(minPrice, maxPrice *float64) *PriceFilter {
	return &PriceFilter{minPrice: minPrice, maxPrice: maxPrice}
}

func (f *PriceFilter) Evaluate(_ context.Context, input EvaluationInput) RuleOutput {
	bars := input.effectiveBars()
	if len(bars) == 0 {
		return skipOutput("PriceFilter", "kline data missing", nil)
	}
	price := bars[len(bars)-1].Close
	if !finite(price) {
		return skipOutput("PriceFilter", "unable to get close price", nil)
	}
	if f.minPrice != nil && price < *f.minPrice {
		return failOutput("PriceFilter", fmt.Sprintf("price %.2f < min %.2f", price, *f.minPrice), map[string]any{"price": price, "min_price": *f.minPrice})
	}
	if f.maxPrice != nil && price > *f.maxPrice {
		return failOutput("PriceFilter", fmt.Sprintf("price %.2f > max %.2f", price, *f.maxPrice), map[string]any{"price": price, "max_price": *f.maxPrice})
	}
	return passOutput("PriceFilter", fmt.Sprintf("price %.2f in range", price), map[string]any{"price": price})
}

type PEFilter struct {
	minPE         *float64
	maxPE         *float64
	allowNegative bool
}

func NewPEFilter(minPE, maxPE *float64, allowNegative bool) *PEFilter {
	return &PEFilter{minPE: minPE, maxPE: maxPE, allowNegative: allowNegative}
}

func (f *PEFilter) Evaluate(_ context.Context, input EvaluationInput) RuleOutput {
	pe, ok := stockNumber(input.Stock, "pe_ratio", input.Stock.PERatio, input.Stock.HasPERatio)
	if !ok {
		return passOutput("PEFilter", "PE data missing; treated as pass", map[string]any{"pe": nil})
	}
	if !f.allowNegative && pe < 0 {
		return failOutput("PEFilter", fmt.Sprintf("PE %.2f is negative", pe), map[string]any{"pe": pe})
	}
	if f.minPE != nil && pe < *f.minPE {
		return failOutput("PEFilter", fmt.Sprintf("PE %.2f < min %.2f", pe, *f.minPE), map[string]any{"pe": pe, "min_pe": *f.minPE})
	}
	if f.maxPE != nil && pe > *f.maxPE {
		return failOutput("PEFilter", fmt.Sprintf("PE %.2f > max %.2f", pe, *f.maxPE), map[string]any{"pe": pe, "max_pe": *f.maxPE})
	}
	return passOutput("PEFilter", fmt.Sprintf("PE %.2f in range", pe), map[string]any{"pe": pe})
}

type ProfitabilityFilter struct {
	requireProfitable bool
}

func NewProfitabilityFilter(requireProfitable bool) *ProfitabilityFilter {
	return &ProfitabilityFilter{requireProfitable: requireProfitable}
}

func (f *ProfitabilityFilter) Evaluate(_ context.Context, input EvaluationInput) RuleOutput {
	pe, ok := stockNumber(input.Stock, "pe_ratio", input.Stock.PERatio, input.Stock.HasPERatio)
	if !ok {
		return passOutput("ProfitabilityFilter", "PE data missing; treated as pass", map[string]any{"pe": nil, "is_profitable": nil})
	}
	isProfitable := pe > 0 && finite(pe)
	if f.requireProfitable && !isProfitable {
		return failOutput("ProfitabilityFilter", fmt.Sprintf("company is not profitable (PE=%.2f)", pe), map[string]any{"pe": pe, "is_profitable": false})
	}
	return passOutput("ProfitabilityFilter", fmt.Sprintf("profitability check passed (PE=%.2f)", pe), map[string]any{"pe": pe, "is_profitable": isProfitable})
}

func stockNumber(stock Stock, extraKey string, fieldValue float64, hasField bool) (float64, bool) {
	if stock.Extra != nil {
		if value, ok := stock.Extra[extraKey]; ok && value != nil {
			return toFloat(value)
		}
	}
	if hasField {
		return fieldValue, true
	}
	if fieldValue != 0 {
		return fieldValue, true
	}
	return 0, false
}

func averageDailyVolume(bars []strategies.Bar, timeframe string) (float64, bool) {
	if len(bars) == 0 {
		return 0, false
	}
	if isIntraday(timeframe) {
		volumeByDate := map[string]float64{}
		for _, bar := range bars {
			day := bar.Time.Format("2006-01-02")
			volumeByDate[day] += bar.Volume
		}
		if len(volumeByDate) == 0 {
			return 0, false
		}
		total := 0.0
		for _, volume := range volumeByDate {
			total += volume
		}
		return total / float64(len(volumeByDate)), true
	}
	total := 0.0
	for _, bar := range bars {
		total += bar.Volume
	}
	return total / float64(len(bars)), true
}

func passOutput(name, reason string, details map[string]any) RuleOutput {
	return NewRuleOutput("", name, ResultPass, reason, details)
}

func failOutput(name, reason string, details map[string]any) RuleOutput {
	return NewRuleOutput("", name, ResultFail, reason, details)
}

func skipOutput(name, reason string, details map[string]any) RuleOutput {
	return NewRuleOutput("", name, ResultSkip, reason, details)
}

func isIntraday(timeframe string) bool {
	value := strings.ToLower(strings.TrimSpace(timeframe))
	return strings.HasSuffix(value, "m") || strings.HasSuffix(value, "min") || strings.HasSuffix(value, "h")
}
