package fetcher

import (
	"context"
	"encoding/json"
	"strings"
	"time"

	"github.com/SimonZhou96/MoneyManager/stock_screener/tavily_fetcher/internal/contextsearch"
	"github.com/SimonZhou96/MoneyManager/stock_screener/tavily_fetcher/internal/search"
	"github.com/SimonZhou96/MoneyManager/stock_screener/tavily_fetcher/internal/storage"
)

type Options struct {
	Scope         string
	Markets       []string
	GlobalQueries []string
	MaxResults    int
	TTL           time.Duration
	CheckDate     time.Time
}

type Result struct {
	ItemCount int      `json:"item_count"`
	Warnings  []string `json:"warnings"`
	Error     string   `json:"error"`
	ElapsedMS int      `json:"elapsed_ms"`
}

type Runner struct {
	Provider search.Provider
	Store    storage.Store
	Now      func() time.Time
}

func (r Runner) RunOnce(ctx context.Context, opts Options) Result {
	started := r.now()
	checkDate := opts.CheckDate
	if checkDate.IsZero() {
		checkDate = started
	}
	maxResults := opts.MaxResults
	if maxResults <= 0 {
		maxResults = 5
	}
	ttl := opts.TTL
	if ttl <= 0 {
		ttl = 24 * time.Hour
	}
	result := Result{}
	if r.Provider == nil || !r.Provider.IsAvailable() {
		result.Error = "search provider unavailable"
		r.insertRun(ctx, started, result, opts, nil)
		return result
	}

	specs := contextsearch.BuildQueries(opts.Scope, opts.Markets, opts.GlobalQueries, checkDate)
	allItems := []storage.IntelItem{}
	for _, spec := range specs {
		docs, err := r.Provider.Search(spec.Query, maxResults)
		if err != nil {
			result.Warnings = append(result.Warnings, spec.Market+" "+spec.ItemType+": "+err.Error())
			continue
		}
		items := storage.BuildIntelItems(storage.ItemBuildInput{
			Market:    spec.Market,
			ItemType:  spec.ItemType,
			Query:     spec.Query,
			Scope:     spec.Scope,
			Docs:      docs,
			FetchedAt: started,
			TTL:       ttl,
		})
		allItems = append(allItems, items...)
	}
	if r.Store != nil {
		if err := r.Store.UpsertItems(ctx, allItems); err != nil {
			result.Error = err.Error()
		}
	}
	result.ItemCount = len(allItems)
	result.ElapsedMS = int(r.now().Sub(started).Milliseconds())
	r.insertRun(ctx, started, result, opts, specs)
	return result
}

func (r Runner) insertRun(ctx context.Context, started time.Time, result Result, opts Options, specs []contextsearch.QuerySpec) {
	if r.Store == nil {
		return
	}
	status := "success"
	if strings.TrimSpace(result.Error) != "" {
		status = "failed"
	}
	raw, _ := json.Marshal(map[string]any{"scope": opts.Scope, "markets": opts.Markets, "queries": specs, "warnings": result.Warnings})
	_ = r.Store.InsertProviderRun(ctx, storage.ProviderRun{
		Provider:     "tavily",
		ScopeType:    "market",
		Market:       strings.ToUpper(strings.TrimSpace(opts.Scope)),
		Code:         "",
		Status:       status,
		ErrorMessage: result.Error,
		DurationMS:   result.ElapsedMS,
		ItemCount:    result.ItemCount,
		RawJSON:      string(raw),
		StartedAt:    started,
		FinishedAt:   r.now(),
	})
}

func (r Runner) now() time.Time {
	if r.Now != nil {
		return r.Now().UTC()
	}
	return time.Now().UTC()
}
