package contextsearch

import (
	"strings"
	"time"
)

type QuerySpec struct {
	Scope    string `json:"scope"`
	Market   string `json:"market"`
	ItemType string `json:"item_type"`
	Query    string `json:"query"`
}

var marketNames = map[string]string{
	"HK": "香港",
	"US": "美国",
	"A":  "A股",
}

func BuildMarketQueries(market string, checkDate time.Time) []QuerySpec {
	key := strings.ToUpper(strings.TrimSpace(market))
	name := marketNames[key]
	if name == "" {
		name = key
	}
	date := checkDate.Format("2006-01-02")
	return []QuerySpec{
		{
			Scope:    "market",
			Market:   key,
			ItemType: "market_news",
			Query:    name + " 股票市场 最新 政策 宏观经济 热点新闻 " + date + " stock market policy macro news",
		},
		{
			Scope:    "market",
			Market:   key,
			ItemType: "hot_sector",
			Query:    name + " 股票市场 今日 热点板块 领涨行业 资金流入 " + date + " hot sectors leading industries",
		},
	}
}

func BuildGlobalQueries(rawQueries []string) []QuerySpec {
	queries := make([]QuerySpec, 0, len(rawQueries))
	for _, raw := range rawQueries {
		query := strings.TrimSpace(raw)
		if query == "" {
			continue
		}
		queries = append(queries, QuerySpec{
			Scope:    "global",
			Market:   "GLOBAL",
			ItemType: "market_news",
			Query:    query,
		})
	}
	if len(queries) == 0 {
		queries = append(queries, QuerySpec{
			Scope:    "global",
			Market:   "GLOBAL",
			ItemType: "market_news",
			Query:    "global macro market breaking news policy geopolitics central banks commodities technology supply chain",
		})
	}
	return queries
}

func BuildQueries(scope string, markets []string, globalQueries []string, checkDate time.Time) []QuerySpec {
	scope = strings.ToLower(strings.TrimSpace(scope))
	if scope == "" {
		scope = "global"
	}
	var specs []QuerySpec
	if scope == "global" || scope == "all" {
		specs = append(specs, BuildGlobalQueries(globalQueries)...)
	}
	if scope == "market" || scope == "all" {
		if len(markets) == 0 {
			markets = []string{"HK", "US", "A"}
		}
		for _, market := range markets {
			specs = append(specs, BuildMarketQueries(market, checkDate)...)
		}
	}
	return specs
}
