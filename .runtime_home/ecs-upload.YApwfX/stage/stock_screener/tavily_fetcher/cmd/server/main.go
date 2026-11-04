package main

import (
	"log"
	"net/http"

	"github.com/SimonZhou96/MoneyManager/stock_screener/tavily_fetcher/internal/config"
	"github.com/SimonZhou96/MoneyManager/stock_screener/tavily_fetcher/internal/httpapi"
	"github.com/SimonZhou96/MoneyManager/stock_screener/tavily_fetcher/internal/storage"
	"github.com/SimonZhou96/MoneyManager/stock_screener/tavily_fetcher/internal/tavily"
)

func main() {
	cfg := config.FromEnv()
	if cfg.TavilyAPIKey == "" {
		log.Fatal("TAVILY_API_KEY is required")
	}
	provider := tavily.NewClient(tavily.Config{
		APIKey:         cfg.TavilyAPIKey,
		Endpoint:       cfg.TavilyEndpoint,
		TimeoutSec:     cfg.SearchTimeoutSec,
		MaxRetries:     3,
		QuotaMaxErrors: 3,
	})
	var store storage.Store
	if dsn := cfg.MySQLDSN(); dsn != "" {
		mysqlStore, err := storage.OpenMySQL(dsn)
		if err != nil {
			log.Printf("mysql disabled: %v", err)
		} else {
			defer mysqlStore.Close()
			store = mysqlStore
		}
	}
	log.Printf("tavily HTTP server listening on %s", cfg.HTTPAddr)
	if err := http.ListenAndServe(cfg.HTTPAddr, httpapi.NewServer(provider, store)); err != nil {
		log.Fatal(err)
	}
}
