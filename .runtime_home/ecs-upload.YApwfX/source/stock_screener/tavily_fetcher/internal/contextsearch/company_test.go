package contextsearch

import (
	"testing"

	"github.com/SimonZhou96/MoneyManager/stock_screener/tavily_fetcher/internal/search"
)

func TestSearchCompaniesBatchAssignsDocumentsByAlias(t *testing.T) {
	provider := &fakeProvider{docs: []search.SearchDocument{
		{Title: "1810.HK Xiaomi earnings", URL: "https://example.com/1810", Content: "Xiaomi results"},
		{Title: "BRK.B annual report", URL: "https://example.com/brk", Content: "Berkshire"},
	}}
	rows := []search.ScreeningSignalRow{
		{Market: "HK", Code: "HK.01810", Name: "小米集团"},
		{Market: "US", Code: "BRK-B", Name: "Berkshire Hathaway"},
	}
	grouped, err := SearchCompaniesBatch(provider, "HK", rows, 5, 390)
	if err != nil {
		t.Fatalf("SearchCompaniesBatch returned error: %v", err)
	}
	if len(grouped["HK.01810"]) != 1 {
		t.Fatalf("expected HK alias match, got %+v", grouped["HK.01810"])
	}
	if len(grouped["BRK-B"]) != 1 {
		t.Fatalf("expected US alias match, got %+v", grouped["BRK-B"])
	}
}

type fakeProvider struct {
	docs []search.SearchDocument
}

func (f *fakeProvider) Search(query string, maxResults int) ([]search.SearchDocument, error) {
	return f.docs, nil
}

func (f *fakeProvider) SearchCompaniesBatch(market string, rows []search.ScreeningSignalRow, maxResults int) (map[string][]search.SearchDocument, error) {
	return nil, nil
}

func (f *fakeProvider) IsAvailable() bool { return true }
func (f *fakeProvider) Name() string      { return "fake" }
