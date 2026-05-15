package main

import (
	"bufio"
	"context"
	"flag"
	"fmt"
	"log"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/SimonZhou96/MoneyManager/agent_orchestrator/internal/app"
	"github.com/SimonZhou96/MoneyManager/agent_orchestrator/internal/marketdata"
	"github.com/SimonZhou96/MoneyManager/agent_orchestrator/internal/mcp"
	"github.com/SimonZhou96/MoneyManager/agent_orchestrator/internal/repository"
	"github.com/SimonZhou96/MoneyManager/agent_orchestrator/internal/screening"
	"github.com/SimonZhou96/MoneyManager/agent_orchestrator/internal/workflow"
	"github.com/go-sql-driver/mysql"
)

func main() {
	var (
		mode            = flag.String("mode", "worker-once", "worker mode: worker or worker-once")
		envFile         = flag.String("env-file", envDefault("ENV_FILE", ""), "optional env file")
		pollIntervalSec = flag.Int("poll-interval-sec", 0, "poll interval in seconds")
		agentID         = flag.String("agent-id", "", "worker agent id")
	)
	flag.Parse()
	visited := visitedFlags()

	if *envFile != "" {
		for _, path := range splitEnvFiles(*envFile) {
			if err := loadEnvFile(path); err != nil {
				log.Fatalf("load env file %s: %v", path, err)
			}
		}
	}
	resolvedMode := *mode
	if !visited["mode"] || resolvedMode == "" {
		resolvedMode = envDefault("AGENT_MODE", "worker")
	}
	resolvedAgentID := *agentID
	if !visited["agent-id"] || resolvedAgentID == "" {
		resolvedAgentID = envDefault("AGENT_ID", "go-agent")
	}
	resolvedPollIntervalSec := *pollIntervalSec
	if !visited["poll-interval-sec"] || resolvedPollIntervalSec <= 0 {
		resolvedPollIntervalSec = envIntDefault("AGENT_POLL_INTERVAL_SEC", 30)
	}
	if resolvedMode != "worker" && resolvedMode != "worker-once" {
		log.Fatalf("unsupported mode %q", resolvedMode)
	}

	repo, err := repository.OpenMySQLRepository(mysqlDSNFromEnv())
	if err != nil {
		log.Fatalf("open repository: %v", err)
	}
	defer repo.Close()

	evidence := mcp.NewEvidenceService(
		mcp.NewTradingViewClient(mcp.Config{
			Command: os.Getenv("TRADINGVIEW_MCP_CMD"),
			Timeout: envDurationDefault("TRADINGVIEW_MCP_TIMEOUT_SEC", 5*time.Second),
		}),
		envDefault("TRADINGVIEW_MCP_TOOL", "combined_analysis"),
	)
	marketData := marketdata.NewProviderChain(marketdata.NewMySQLCacheProvider(repo.DB()))
	ruleExecutor := screening.NewExecutor(repo, screening.NewMarketDataAdapter(marketData), nil)
	runner, err := workflow.NewRunner(ruleExecutor, evidence)
	if err != nil {
		log.Fatalf("build workflow: %v", err)
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	worker := app.NewWorker(app.Config{
		AgentID:      resolvedAgentID,
		PollInterval: time.Duration(resolvedPollIntervalSec) * time.Second,
	}, repo, runner)
	if resolvedMode == "worker-once" {
		processed, err := worker.RunOnce(ctx)
		if err != nil {
			log.Fatalf("worker-once failed: %v", err)
		}
		log.Printf("worker-once processed jobs: %d", processed)
		return
	}
	if err := worker.Run(ctx); err != nil && ctx.Err() == nil {
		log.Fatalf("worker failed: %v", err)
	}
}

func visitedFlags() map[string]bool {
	visited := make(map[string]bool)
	flag.Visit(func(f *flag.Flag) {
		visited[f.Name] = true
	})
	return visited
}

func loadEnvFile(path string) error {
	file, err := os.Open(path)
	if err != nil {
		return err
	}
	defer file.Close()

	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		key, value, ok := strings.Cut(line, "=")
		if !ok {
			continue
		}
		key = strings.TrimSpace(key)
		value = strings.Trim(strings.TrimSpace(value), `"'`)
		if key == "" || os.Getenv(key) != "" {
			continue
		}
		if err := os.Setenv(key, value); err != nil {
			return fmt.Errorf("set %s: %w", key, err)
		}
	}
	return scanner.Err()
}

func splitEnvFiles(value string) []string {
	parts := strings.Split(value, ",")
	out := make([]string, 0, len(parts))
	for _, part := range parts {
		part = strings.TrimSpace(part)
		if part != "" {
			out = append(out, part)
		}
	}
	return out
}

func firstEnv(keys ...string) string {
	for _, key := range keys {
		if value := strings.TrimSpace(os.Getenv(key)); value != "" {
			return value
		}
	}
	return ""
}

func mysqlDSNFromEnv() string {
	if dsn := firstEnv("AGENT_MYSQL_DSN", "MYSQL_DSN", "STOCK_SCREENER_MYSQL_DSN"); dsn != "" {
		return dsn
	}
	cfg := mysql.NewConfig()
	cfg.User = envDefault("MYSQL_USER", "root")
	cfg.Passwd = os.Getenv("MYSQL_PASSWORD")
	cfg.Net = "tcp"
	cfg.Addr = fmt.Sprintf("%s:%s", envDefault("MYSQL_HOST", "127.0.0.1"), envDefault("MYSQL_PORT", "3306"))
	cfg.DBName = envDefault("MYSQL_DATABASE", "market_data")
	cfg.ParseTime = true
	cfg.Params = map[string]string{"charset": "utf8mb4"}
	return cfg.FormatDSN()
}

func envDefault(key string, fallback string) string {
	if value := strings.TrimSpace(os.Getenv(key)); value != "" {
		return value
	}
	return fallback
}

func envIntDefault(key string, fallback int) int {
	value := strings.TrimSpace(os.Getenv(key))
	if value == "" {
		return fallback
	}
	parsed, err := strconv.Atoi(value)
	if err != nil || parsed <= 0 {
		return fallback
	}
	return parsed
}

func envDurationDefault(key string, fallback time.Duration) time.Duration {
	seconds := envIntDefault(key, int(fallback/time.Second))
	return time.Duration(seconds) * time.Second
}
