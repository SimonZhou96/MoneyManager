package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"github.com/SimonZhou96/MoneyManager/stock_screener/tavily_fetcher/internal/config"
	"github.com/SimonZhou96/MoneyManager/stock_screener/tavily_fetcher/internal/fetcher"
	"github.com/SimonZhou96/MoneyManager/stock_screener/tavily_fetcher/internal/storage"
	"github.com/SimonZhou96/MoneyManager/stock_screener/tavily_fetcher/internal/tavily"
)

func main() {
	cfg := config.FromEnv()
	once := flag.Bool("once", false, "run once and exit")
	scope := flag.String("scope", cfg.FetchScope, "fetch scope: global, market, all")
	markets := flag.String("markets", strings.Join(cfg.FetchMarkets, ","), "comma-separated markets")
	schedule := flag.String("schedule", cfg.FetchCron, "cron-like schedule string; non-empty runs periodically")
	flag.Parse()

	if cfg.TavilyAPIKey == "" {
		log.Fatal("TAVILY_API_KEY is required")
	}
	store, err := openStore(cfg)
	if err != nil {
		log.Fatalf("open mysql store: %v", err)
	}
	defer store.Close()

	provider := tavily.NewClient(tavily.Config{
		APIKey:         cfg.TavilyAPIKey,
		Endpoint:       cfg.TavilyEndpoint,
		TimeoutSec:     cfg.SearchTimeoutSec,
		MaxRetries:     3,
		QuotaMaxErrors: 3,
	})
	runner := fetcher.Runner{Provider: provider, Store: store}
	opts := fetcher.Options{
		Scope:         *scope,
		Markets:       splitCSV(*markets),
		GlobalQueries: cfg.GlobalQueries,
		MaxResults:    cfg.SearchMaxResults,
		TTL:           cfg.ContextTTL,
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	if *once || strings.TrimSpace(*schedule) == "" {
		result := runner.RunOnce(ctx, opts)
		fmt.Printf("item_count=%d elapsed_ms=%d warnings=%d error=%q\n", result.ItemCount, result.ElapsedMS, len(result.Warnings), result.Error)
		if result.Error != "" {
			os.Exit(1)
		}
		return
	}
	runScheduled(ctx, runner, opts, *schedule)
}

func openStore(cfg config.Config) (*storage.MySQLStore, error) {
	dsn := cfg.MySQLDSN()
	if dsn == "" {
		return nil, fmt.Errorf("MYSQL_HOST, MYSQL_USER and MYSQL_DATABASE are required")
	}
	return storage.OpenMySQL(dsn)
}

func runScheduled(ctx context.Context, runner fetcher.Runner, opts fetcher.Options, schedule string) {
	interval := scheduleInterval(schedule)
	log.Printf("starting scheduled fetcher schedule=%q interval=%s", schedule, interval)
	for {
		result := runner.RunOnce(ctx, opts)
		log.Printf("fetch complete item_count=%d elapsed_ms=%d warnings=%d error=%q", result.ItemCount, result.ElapsedMS, len(result.Warnings), result.Error)
		timer := time.NewTimer(interval)
		select {
		case <-ctx.Done():
			timer.Stop()
			return
		case <-timer.C:
		}
	}
}

func scheduleInterval(schedule string) time.Duration {
	fields := strings.Fields(schedule)
	if len(fields) >= 2 && strings.HasPrefix(fields[1], "*/") {
		if n, err := time.ParseDuration(strings.TrimPrefix(fields[1], "*/") + "h"); err == nil && n > 0 {
			return n
		}
	}
	return time.Hour
}

func splitCSV(value string) []string {
	parts := strings.Split(value, ",")
	result := make([]string, 0, len(parts))
	for _, part := range parts {
		part = strings.TrimSpace(part)
		if part != "" {
			result = append(result, part)
		}
	}
	return result
}
