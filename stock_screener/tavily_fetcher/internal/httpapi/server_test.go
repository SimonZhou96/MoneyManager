package httpapi

import (
	"bytes"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/SimonZhou96/MoneyManager/stock_screener/tavily_fetcher/internal/search"
)

func TestSearchEndpointReturnsDocuments(t *testing.T) {
	server := NewServer(&fakeProvider{docs: []search.SearchDocument{{Title: "News", URL: "https://example.com", Content: "content"}}}, nil)
	req := httptest.NewRequest(http.MethodPost, "/search", bytes.NewBufferString(`{"prompt":"global macro","max_results":1}`))
	rec := httptest.NewRecorder()
	server.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", rec.Code, rec.Body.String())
	}
	if !bytes.Contains(rec.Body.Bytes(), []byte(`"documents"`)) || !bytes.Contains(rec.Body.Bytes(), []byte(`"title":"News"`)) {
		t.Fatalf("unexpected response: %s", rec.Body.String())
	}
}

func TestCompaniesBatchRequiresExplicitRows(t *testing.T) {
	server := NewServer(&fakeProvider{}, nil)
	req := httptest.NewRequest(http.MethodPost, "/search/companies:batch", bytes.NewBufferString(`{"market":"HK","rows":[]}`))
	rec := httptest.NewRecorder()
	server.ServeHTTP(rec, req)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 for empty rows, got %d", rec.Code)
	}
}

type fakeProvider struct {
	docs []search.SearchDocument
}

func (f *fakeProvider) Search(query string, maxResults int) ([]search.SearchDocument, error) {
	return f.docs, nil
}

func (f *fakeProvider) SearchCompaniesBatch(market string, rows []search.ScreeningSignalRow, maxResults int) (map[string][]search.SearchDocument, error) {
	result := map[string][]search.SearchDocument{}
	for _, row := range rows {
		result[row.Code] = f.docs
	}
	return result, nil
}

func (f *fakeProvider) IsAvailable() bool { return true }
func (f *fakeProvider) Name() string      { return "fake" }
