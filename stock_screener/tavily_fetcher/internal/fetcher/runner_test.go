package fetcher

import (
	"context"
	"testing"
	"time"

	"github.com/SimonZhou96/MoneyManager/stock_screener/tavily_fetcher/internal/search"
	"github.com/SimonZhou96/MoneyManager/stock_screener/tavily_fetcher/internal/storage"
)

func TestRunnerPersistsOnlyMarketScopedItems(t *testing.T) {
	provider := &recordingProvider{docs: []search.SearchDocument{{Title: "News", URL: "https://example.com", Content: "content"}}}
	store := &memoryStore{}
	runner := Runner{Provider: provider, Store: store, Now: func() time.Time { return time.Date(2026, 6, 29, 0, 0, 0, 0, time.UTC) }}

	result := runner.RunOnce(context.Background(), Options{Scope: "all", Markets: []string{"HK", "US"}, GlobalQueries: []string{"global macro"}, MaxResults: 2, TTL: time.Hour})
	if result.Error != "" {
		t.Fatalf("RunOnce returned error: %s", result.Error)
	}
	if len(store.items) == 0 {
		t.Fatalf("expected persisted items")
	}
	for _, item := range store.items {
		if item.ScopeType != "market" || item.Code != "" {
			t.Fatalf("manual/scheduled fetch must not persist stock scoped item: %+v", item)
		}
	}
	if provider.companyBatchCalls != 0 {
		t.Fatalf("manual/scheduled fetch must not call company batch search")
	}
}

type recordingProvider struct {
	docs              []search.SearchDocument
	companyBatchCalls int
}

func (p *recordingProvider) Search(query string, maxResults int) ([]search.SearchDocument, error) {
	return p.docs, nil
}

func (p *recordingProvider) SearchCompaniesBatch(market string, rows []search.ScreeningSignalRow, maxResults int) (map[string][]search.SearchDocument, error) {
	p.companyBatchCalls++
	return map[string][]search.SearchDocument{}, nil
}

func (p *recordingProvider) IsAvailable() bool { return true }
func (p *recordingProvider) Name() string      { return "fake" }

type memoryStore struct {
	items []storage.IntelItem
	runs  []storage.ProviderRun
}

func (s *memoryStore) UpsertItems(ctx context.Context, items []storage.IntelItem) error {
	s.items = append(s.items, items...)
	return nil
}

func (s *memoryStore) InsertProviderRun(ctx context.Context, run storage.ProviderRun) error {
	s.runs = append(s.runs, run)
	return nil
}
