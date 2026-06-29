package tavily

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"math/rand"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/SimonZhou96/MoneyManager/stock_screener/tavily_fetcher/internal/search"
)

type Config struct {
	APIKey         string
	Endpoint       string
	TimeoutSec     int
	SearchDepth    string
	MaxRetries     int
	QuotaMaxErrors int
	HTTPClient     *http.Client
}

type Client struct {
	apiKey         string
	endpoint       string
	timeoutSec     int
	searchDepth    string
	maxRetries     int
	quotaMaxErrors int
	httpClient     *http.Client

	mu              sync.Mutex
	quotaExhausted  bool
	quotaErrorCount int
}

func NewClient(cfg Config) *Client {
	endpoint := strings.TrimSpace(cfg.Endpoint)
	if endpoint == "" {
		endpoint = "https://api.tavily.com/search"
	}
	timeout := cfg.TimeoutSec
	if timeout <= 0 {
		timeout = 30
	}
	depth := strings.TrimSpace(cfg.SearchDepth)
	if depth == "" {
		depth = "basic"
	}
	retries := cfg.MaxRetries
	if retries < 0 {
		retries = 0
	}
	quotaMax := cfg.QuotaMaxErrors
	if quotaMax <= 0 {
		quotaMax = 3
	}
	httpClient := cfg.HTTPClient
	if httpClient == nil {
		httpClient = &http.Client{Timeout: time.Duration(timeout) * time.Second}
	}
	return &Client{
		apiKey:         strings.TrimSpace(cfg.APIKey),
		endpoint:       endpoint,
		timeoutSec:     timeout,
		searchDepth:    depth,
		maxRetries:     retries,
		quotaMaxErrors: quotaMax,
		httpClient:     httpClient,
	}
}

func (c *Client) Name() string { return "tavily" }

func (c *Client) IsAvailable() bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.apiKey != "" && !c.quotaExhausted
}

func (c *Client) Search(query string, maxResults int) ([]search.SearchDocument, error) {
	query = strings.TrimSpace(query)
	if query == "" {
		return nil, nil
	}
	if !c.IsAvailable() {
		return nil, ErrQuotaExhausted
	}
	if maxResults <= 0 {
		maxResults = 1
	}
	payload := map[string]any{
		"api_key":             c.apiKey,
		"query":               query,
		"search_depth":        c.searchDepth,
		"include_answer":      false,
		"include_raw_content": false,
		"max_results":         maxResults,
	}
	body, err := json.Marshal(payload)
	if err != nil {
		return nil, err
	}

	respBody, status, err := c.postWithRetry(body)
	if err != nil {
		if status == http.StatusTooManyRequests {
			c.recordQuotaError()
		}
		return nil, err
	}
	c.resetQuotaErrors()
	return normalizeResponse(respBody, query)
}

func (c *Client) SearchCompaniesBatch(market string, rows []search.ScreeningSignalRow, maxResults int) (map[string][]search.SearchDocument, error) {
	return nil, errors.New("tavily.Client does not implement company batching directly; use contextsearch.SearchCompaniesBatch")
}

var ErrQuotaExhausted = errors.New("tavily quota exhausted")

func (c *Client) postWithRetry(body []byte) ([]byte, int, error) {
	var lastStatus int
	var lastErr error
	for attempt := 0; attempt <= c.maxRetries; attempt++ {
		req, err := http.NewRequest(http.MethodPost, c.endpoint, bytes.NewReader(body))
		if err != nil {
			return nil, 0, err
		}
		req.Header.Set("Content-Type", "application/json")
		resp, err := c.httpClient.Do(req)
		if err != nil {
			lastErr = err
		} else {
			lastStatus = resp.StatusCode
			respBody, readErr := io.ReadAll(resp.Body)
			_ = resp.Body.Close()
			if readErr != nil {
				return nil, lastStatus, readErr
			}
			if resp.StatusCode < 400 {
				return respBody, resp.StatusCode, nil
			}
			lastErr = fmt.Errorf("tavily search failed: HTTP %d %s", resp.StatusCode, firstN(string(respBody), 200))
			if !isRetryableStatus(resp.StatusCode) {
				return nil, resp.StatusCode, lastErr
			}
		}
		if attempt < c.maxRetries {
			delay := time.Duration(math.Pow(2, float64(attempt))*1000)*time.Millisecond + time.Duration(rand.Intn(250))*time.Millisecond
			time.Sleep(delay)
		}
	}
	return nil, lastStatus, lastErr
}

func normalizeResponse(body []byte, query string) ([]search.SearchDocument, error) {
	var payload struct {
		Results []map[string]any `json:"results"`
	}
	if err := json.Unmarshal(body, &payload); err != nil {
		return nil, err
	}
	docs := make([]search.SearchDocument, 0, len(payload.Results))
	for _, item := range payload.Results {
		title := strings.TrimSpace(asString(item["title"]))
		url := strings.TrimSpace(asString(item["url"]))
		content := strings.TrimSpace(firstNonEmpty(asString(item["content"]), asString(item["snippet"])))
		if title == "" && url == "" && content == "" {
			continue
		}
		docs = append(docs, search.SearchDocument{
			Title:   title,
			URL:     url,
			Content: content,
			Score:   asFloatPtr(item["score"]),
			Query:   query,
		})
	}
	return docs, nil
}

func (c *Client) recordQuotaError() {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.quotaErrorCount++
	if c.quotaErrorCount >= c.quotaMaxErrors {
		c.quotaExhausted = true
	}
}

func (c *Client) resetQuotaErrors() {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.quotaErrorCount = 0
}

func isRetryableStatus(status int) bool {
	switch status {
	case 429, 432, 500, 502, 503, 504:
		return true
	default:
		return false
	}
}

func asString(value any) string {
	switch v := value.(type) {
	case string:
		return v
	case fmt.Stringer:
		return v.String()
	default:
		return ""
	}
}

func asFloatPtr(value any) *float64 {
	switch v := value.(type) {
	case float64:
		return &v
	case float32:
		f := float64(v)
		return &f
	case int:
		f := float64(v)
		return &f
	case string:
		f, err := strconv.ParseFloat(strings.TrimSpace(v), 64)
		if err != nil {
			return nil
		}
		return &f
	default:
		return nil
	}
}

func firstNonEmpty(values ...string) string {
	for _, value := range values {
		if strings.TrimSpace(value) != "" {
			return value
		}
	}
	return ""
}

func firstN(value string, n int) string {
	if len(value) <= n {
		return value
	}
	return value[:n]
}
