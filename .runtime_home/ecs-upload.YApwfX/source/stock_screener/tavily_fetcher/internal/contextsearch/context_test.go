package contextsearch

import (
	"strings"
	"testing"
	"time"
)

func TestBuildContextQueries(t *testing.T) {
	date := time.Date(2026, 6, 29, 0, 0, 0, 0, time.UTC)
	queries := BuildMarketQueries("HK", date)
	if len(queries) != 2 {
		t.Fatalf("expected market and hot-sector queries, got %d", len(queries))
	}
	if queries[0].ItemType != "market_news" || !strings.Contains(queries[0].Query, "香港") || !strings.Contains(queries[0].Query, "2026-06-29") {
		t.Fatalf("unexpected market query: %+v", queries[0])
	}
	if queries[1].ItemType != "hot_sector" || !strings.Contains(queries[1].Query, "热点板块") {
		t.Fatalf("unexpected sector query: %+v", queries[1])
	}
}

func TestBuildGlobalQueriesUsesConfiguredQueries(t *testing.T) {
	queries := BuildGlobalQueries([]string{" global macro ", "", "central banks"})
	if len(queries) != 2 {
		t.Fatalf("expected 2 non-empty queries, got %d", len(queries))
	}
	for _, query := range queries {
		if query.Market != "GLOBAL" || query.ItemType != "market_news" {
			t.Fatalf("global query should be market scoped: %+v", query)
		}
	}
}
