package search

type SearchDocument struct {
	Title   string   `json:"title"`
	URL     string   `json:"url"`
	Content string   `json:"content"`
	Score   *float64 `json:"score"`
	Query   string   `json:"query"`
}

type ScreeningSignalRow struct {
	Market string `json:"market,omitempty"`
	Code   string `json:"code"`
	Name   string `json:"name"`
	IsETF  bool   `json:"is_etf"`
}

type Provider interface {
	Search(query string, maxResults int) ([]SearchDocument, error)
	SearchCompaniesBatch(market string, rows []ScreeningSignalRow, maxResults int) (map[string][]SearchDocument, error)
	IsAvailable() bool
	Name() string
}
