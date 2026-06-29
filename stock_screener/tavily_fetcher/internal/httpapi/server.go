package httpapi

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"time"

	"github.com/SimonZhou96/MoneyManager/stock_screener/tavily_fetcher/internal/contextsearch"
	"github.com/SimonZhou96/MoneyManager/stock_screener/tavily_fetcher/internal/search"
	"github.com/SimonZhou96/MoneyManager/stock_screener/tavily_fetcher/internal/storage"
)

type Server struct {
	provider search.Provider
	store    storage.Store
	mux      *http.ServeMux
}

func NewServer(provider search.Provider, store storage.Store) http.Handler {
	s := &Server{provider: provider, store: store, mux: http.NewServeMux()}
	s.mux.HandleFunc("/search", s.handleSearch)
	s.mux.HandleFunc("/search/context", s.handleContext)
	s.mux.HandleFunc("/search/companies:batch", s.handleCompaniesBatch)
	return s
}

func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	s.mux.ServeHTTP(w, r)
}

type searchRequest struct {
	Prompt      string `json:"prompt"`
	MaxResults  int    `json:"max_results"`
	SearchDepth string `json:"search_depth"`
	Persist     bool   `json:"persist"`
}

type searchResponse struct {
	Provider  string                  `json:"provider"`
	Query     string                  `json:"query"`
	Documents []search.SearchDocument `json:"documents"`
	Warnings  []string                `json:"warnings"`
	Error     string                  `json:"error"`
	ElapsedMS int                     `json:"elapsed_ms"`
}

func (s *Server) handleSearch(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	var req searchRequest
	if !decodeJSON(w, r, &req) {
		return
	}
	req.Prompt = strings.TrimSpace(req.Prompt)
	if req.Prompt == "" {
		writeError(w, http.StatusBadRequest, "prompt is required")
		return
	}
	started := time.Now()
	docs, err := s.provider.Search(req.Prompt, defaultMax(req.MaxResults))
	status := http.StatusOK
	resp := searchResponse{Provider: s.provider.Name(), Query: req.Prompt, Documents: docs, ElapsedMS: int(time.Since(started).Milliseconds())}
	if err != nil {
		resp.Error = err.Error()
		status = http.StatusBadGateway
		if strings.Contains(strings.ToLower(err.Error()), "quota") || strings.Contains(err.Error(), "429") {
			status = http.StatusTooManyRequests
		}
	}
	if err == nil && req.Persist && s.store != nil {
		items := storage.BuildIntelItems(storage.ItemBuildInput{Market: "GLOBAL", ItemType: "search_document", Query: req.Prompt, Scope: "http", Docs: docs, FetchedAt: time.Now().UTC(), TTL: 24 * time.Hour})
		if storeErr := s.store.UpsertItems(r.Context(), items); storeErr != nil {
			resp.Warnings = append(resp.Warnings, storeErr.Error())
		}
	}
	writeJSON(w, status, resp)
}

type contextRequest struct {
	Scope       string   `json:"scope"`
	Markets     []string `json:"markets"`
	CheckDate   string   `json:"check_date"`
	MaxResults  int      `json:"max_results"`
	Persist     bool     `json:"persist"`
	GlobalQuery []string `json:"global_queries"`
}

type contextResponse struct {
	Provider  string                             `json:"provider"`
	Results   map[string][]search.SearchDocument `json:"results"`
	Warnings  []string                           `json:"warnings"`
	Error     string                             `json:"error"`
	ElapsedMS int                                `json:"elapsed_ms"`
}

func (s *Server) handleContext(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	var req contextRequest
	if !decodeJSON(w, r, &req) {
		return
	}
	checkDate := time.Now().UTC()
	if strings.TrimSpace(req.CheckDate) != "" {
		parsed, err := time.Parse("2006-01-02", req.CheckDate)
		if err != nil {
			writeError(w, http.StatusBadRequest, "check_date must be YYYY-MM-DD")
			return
		}
		checkDate = parsed
	}
	started := time.Now()
	specs := contextsearch.BuildQueries(req.Scope, req.Markets, req.GlobalQuery, checkDate)
	resp := contextResponse{Provider: s.provider.Name(), Results: map[string][]search.SearchDocument{}}
	var items []storage.IntelItem
	for _, spec := range specs {
		docs, err := s.provider.Search(spec.Query, defaultMax(req.MaxResults))
		key := spec.Market + ":" + spec.ItemType
		if err != nil {
			resp.Warnings = append(resp.Warnings, key+": "+err.Error())
			continue
		}
		resp.Results[key] = docs
		if req.Persist {
			items = append(items, storage.BuildIntelItems(storage.ItemBuildInput{Market: spec.Market, ItemType: spec.ItemType, Query: spec.Query, Scope: spec.Scope, Docs: docs, FetchedAt: time.Now().UTC(), TTL: 24 * time.Hour})...)
		}
	}
	if req.Persist && s.store != nil {
		if err := s.store.UpsertItems(context.Background(), items); err != nil {
			resp.Warnings = append(resp.Warnings, err.Error())
		}
	}
	resp.ElapsedMS = int(time.Since(started).Milliseconds())
	writeJSON(w, http.StatusOK, resp)
}

type companiesBatchRequest struct {
	Market     string                      `json:"market"`
	MaxResults int                         `json:"max_results"`
	Rows       []search.ScreeningSignalRow `json:"rows"`
	Persist    bool                        `json:"persist"`
}

type companiesBatchResponse struct {
	Provider  string                             `json:"provider"`
	Market    string                             `json:"market"`
	Documents map[string][]search.SearchDocument `json:"documents"`
	Warnings  []string                           `json:"warnings"`
	Error     string                             `json:"error"`
	ElapsedMS int                                `json:"elapsed_ms"`
}

func (s *Server) handleCompaniesBatch(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	var req companiesBatchRequest
	if !decodeJSON(w, r, &req) {
		return
	}
	if len(req.Rows) == 0 {
		writeError(w, http.StatusBadRequest, "rows are required")
		return
	}
	started := time.Now()
	grouped, err := contextsearch.SearchCompaniesBatch(s.provider, req.Market, req.Rows, defaultMax(req.MaxResults), 390)
	resp := companiesBatchResponse{Provider: s.provider.Name(), Market: req.Market, Documents: grouped, ElapsedMS: int(time.Since(started).Milliseconds())}
	if err != nil {
		resp.Error = err.Error()
		writeJSON(w, http.StatusBadGateway, resp)
		return
	}
	if req.Persist && s.store != nil {
		var items []storage.IntelItem
		now := time.Now().UTC()
		for _, row := range req.Rows {
			items = append(items, storage.BuildIntelItems(storage.ItemBuildInput{Market: req.Market, ItemType: "search_document", Query: "", Scope: "http_company_batch", Docs: grouped[row.Code], FetchedAt: now, TTL: 24 * time.Hour, StockCode: row.Code})...)
		}
		if storeErr := s.store.UpsertItems(r.Context(), items); storeErr != nil {
			resp.Warnings = append(resp.Warnings, storeErr.Error())
		}
	}
	writeJSON(w, http.StatusOK, resp)
}

func decodeJSON(w http.ResponseWriter, r *http.Request, target any) bool {
	defer r.Body.Close()
	if err := json.NewDecoder(r.Body).Decode(target); err != nil {
		writeError(w, http.StatusBadRequest, "invalid json: "+err.Error())
		return false
	}
	return true
}

func writeError(w http.ResponseWriter, status int, message string) {
	writeJSON(w, status, map[string]string{"error": message})
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

func defaultMax(value int) int {
	if value <= 0 {
		return 5
	}
	return value
}
