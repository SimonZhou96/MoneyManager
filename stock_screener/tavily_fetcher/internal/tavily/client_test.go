package tavily

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestSearchNormalizesTavilyResults(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"results":[{"title":"Policy","url":"https://example.com/a","content":"macro news","score":0.91},{"title":"","url":"","content":""},{"title":"Snippet","url":"https://example.com/b","snippet":"from snippet","score":"bad"}]}`))
	}))
	defer server.Close()

	client := NewClient(Config{APIKey: "key", Endpoint: server.URL, TimeoutSec: 5, MaxRetries: 0})
	docs, err := client.Search("HK market", 3)
	if err != nil {
		t.Fatalf("Search returned error: %v", err)
	}
	if len(docs) != 2 {
		t.Fatalf("expected 2 normalized docs, got %d", len(docs))
	}
	if docs[0].Title != "Policy" || docs[0].URL != "https://example.com/a" || docs[0].Content != "macro news" || docs[0].Query != "HK market" {
		t.Fatalf("unexpected first doc: %+v", docs[0])
	}
	if docs[0].Score == nil || *docs[0].Score != 0.91 {
		t.Fatalf("expected numeric score, got %+v", docs[0].Score)
	}
	if docs[1].Content != "from snippet" {
		t.Fatalf("expected snippet fallback, got %+v", docs[1])
	}
	if docs[1].Score != nil {
		t.Fatalf("expected invalid score to become nil, got %+v", docs[1].Score)
	}
}

func TestSearchRetriesAndMarksQuotaExhausted(t *testing.T) {
	calls := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		http.Error(w, "rate limited", http.StatusTooManyRequests)
	}))
	defer server.Close()

	client := NewClient(Config{APIKey: "key", Endpoint: server.URL, TimeoutSec: 5, MaxRetries: 0, QuotaMaxErrors: 3})
	for i := 0; i < 3; i++ {
		if _, err := client.Search("global", 1); err == nil {
			t.Fatalf("expected 429 error on call %d", i+1)
		}
	}
	if client.IsAvailable() {
		t.Fatalf("expected client to be unavailable after consecutive quota errors")
	}
	if _, err := client.Search("global", 1); err == nil {
		t.Fatalf("expected quota exhausted error")
	}
	if calls != 3 {
		t.Fatalf("expected no HTTP call after quota exhaustion, got %d calls", calls)
	}
}
