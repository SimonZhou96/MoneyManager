package storage

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"strings"
	"time"

	_ "github.com/go-sql-driver/mysql"

	"github.com/SimonZhou96/MoneyManager/stock_screener/tavily_fetcher/internal/search"
)

type Store interface {
	UpsertItems(ctx context.Context, items []IntelItem) error
	InsertProviderRun(ctx context.Context, run ProviderRun) error
}

type IntelItem struct {
	ScopeType string
	Market    string
	Code      string
	Source    string
	Provider  string
	ItemType  string
	Title     string
	Summary   string
	URL       string
	RawJSON   string
	FetchedAt time.Time
	ExpiresAt time.Time
	DedupeKey string
}

type ProviderRun struct {
	Provider     string
	ScopeType    string
	Market       string
	Code         string
	Status       string
	ErrorMessage string
	DurationMS   int
	ItemCount    int
	RawJSON      string
	StartedAt    time.Time
	FinishedAt   time.Time
}

type ItemBuildInput struct {
	Market    string
	ItemType  string
	Query     string
	Scope     string
	Docs      []search.SearchDocument
	FetchedAt time.Time
	TTL       time.Duration
	StockCode string
}

func BuildIntelItems(input ItemBuildInput) []IntelItem {
	market := strings.ToUpper(strings.TrimSpace(input.Market))
	if market == "" {
		market = "GLOBAL"
	}
	itemType := strings.TrimSpace(input.ItemType)
	if itemType == "" {
		itemType = "search_document"
	}
	fetchedAt := input.FetchedAt
	if fetchedAt.IsZero() {
		fetchedAt = time.Now().UTC()
	}
	ttl := input.TTL
	if ttl <= 0 {
		ttl = 24 * time.Hour
	}
	scopeType := "market"
	code := ""
	if strings.TrimSpace(input.StockCode) != "" {
		scopeType = "stock"
		code = strings.TrimSpace(input.StockCode)
	}
	items := make([]IntelItem, 0, len(input.Docs))
	for _, doc := range input.Docs {
		raw, _ := json.Marshal(map[string]any{
			"query": input.Query,
			"scope": input.Scope,
			"result": map[string]any{
				"title":   doc.Title,
				"url":     doc.URL,
				"content": doc.Content,
				"score":   doc.Score,
			},
		})
		items = append(items, IntelItem{
			ScopeType: scopeType,
			Market:    market,
			Code:      code,
			Source:    "Tavily",
			Provider:  "tavily",
			ItemType:  itemType,
			Title:     doc.Title,
			Summary:   doc.Content,
			URL:       doc.URL,
			RawJSON:   string(raw),
			FetchedAt: fetchedAt,
			ExpiresAt: fetchedAt.Add(ttl),
			DedupeKey: DedupeKey("tavily", market, code, itemType, doc.URL, doc.Title),
		})
	}
	return items
}

func DedupeKey(parts ...string) string {
	h := sha256.New()
	for _, part := range parts {
		h.Write([]byte(strings.TrimSpace(part)))
		h.Write([]byte{0})
	}
	return hex.EncodeToString(h.Sum(nil))
}

type MySQLStore struct {
	db *sql.DB
}

func OpenMySQL(dsn string) (*MySQLStore, error) {
	db, err := sql.Open("mysql", dsn)
	if err != nil {
		return nil, err
	}
	if err := db.Ping(); err != nil {
		_ = db.Close()
		return nil, err
	}
	return &MySQLStore{db: db}, nil
}

func (s *MySQLStore) Close() error {
	if s == nil || s.db == nil {
		return nil
	}
	return s.db.Close()
}

func (s *MySQLStore) UpsertItems(ctx context.Context, items []IntelItem) error {
	if len(items) == 0 {
		return nil
	}
	const stmt = `
INSERT INTO market_intel_items
(scope_type, market, code, source, provider, item_type, title, summary, url, raw_json, fetched_at, expires_at, dedupe_key)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CAST(? AS JSON), ?, ?, ?)
ON DUPLICATE KEY UPDATE
title=VALUES(title), summary=VALUES(summary), url=VALUES(url), raw_json=VALUES(raw_json),
fetched_at=VALUES(fetched_at), expires_at=VALUES(expires_at), is_stale=0, updated_at=CURRENT_TIMESTAMP(6)`
	for _, item := range items {
		if _, err := s.db.ExecContext(ctx, stmt,
			item.ScopeType, item.Market, item.Code, item.Source, item.Provider, item.ItemType,
			item.Title, item.Summary, item.URL, item.RawJSON, item.FetchedAt, item.ExpiresAt, item.DedupeKey,
		); err != nil {
			return err
		}
	}
	return nil
}

func (s *MySQLStore) InsertProviderRun(ctx context.Context, run ProviderRun) error {
	const stmt = `
INSERT INTO market_intel_provider_runs
(provider, scope_type, market, code, status, error_message, duration_ms, item_count, raw_json, started_at, finished_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, CAST(? AS JSON), ?, ?)`
	rawJSON := run.RawJSON
	if strings.TrimSpace(rawJSON) == "" {
		rawJSON = "{}"
	}
	_, err := s.db.ExecContext(ctx, stmt,
		run.Provider, run.ScopeType, run.Market, run.Code, run.Status, nullableString(run.ErrorMessage),
		run.DurationMS, run.ItemCount, rawJSON, run.StartedAt, run.FinishedAt,
	)
	return err
}

func nullableString(value string) any {
	if strings.TrimSpace(value) == "" {
		return nil
	}
	return value
}
