package storage

import (
	"testing"
	"time"

	"github.com/SimonZhou96/MoneyManager/stock_screener/tavily_fetcher/internal/search"
)

func TestBuildIntelItemsForTaskAreMarketScoped(t *testing.T) {
	now := time.Date(2026, 6, 29, 10, 0, 0, 0, time.UTC)
	items := BuildIntelItems(ItemBuildInput{
		Market:   "HK",
		ItemType: "market_news",
		Query:    "HK market",
		Docs: []search.SearchDocument{
			{Title: "News", URL: "https://example.com/news", Content: "content", Query: "HK market"},
		},
		FetchedAt: now,
		TTL:       24 * time.Hour,
	})
	if len(items) != 1 {
		t.Fatalf("expected one item, got %d", len(items))
	}
	item := items[0]
	if item.ScopeType != "market" || item.Code != "" || item.Market != "HK" {
		t.Fatalf("task items must be market scoped without stock code: %+v", item)
	}
	if item.DedupeKey == "" {
		t.Fatalf("expected stable dedupe key")
	}
	if item.ExpiresAt.Sub(item.FetchedAt) != 24*time.Hour {
		t.Fatalf("unexpected ttl: fetched=%v expires=%v", item.FetchedAt, item.ExpiresAt)
	}
}
