package config

import (
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/go-sql-driver/mysql"
)

type Config struct {
	TavilyAPIKey       string
	TavilyEndpoint     string
	SearchTimeoutSec   int
	SearchMaxResults   int
	FetchScope         string
	FetchMarkets       []string
	FetchCron          string
	GlobalQueries      []string
	ContextTTL         time.Duration
	HTTPAddr           string
	MySQLHost          string
	MySQLPort          int
	MySQLUser          string
	MySQLPassword      string
	MySQLDatabase      string
	CompanyQueryMaxLen int
}

func FromEnv() Config {
	return Config{
		TavilyAPIKey:       strings.TrimSpace(os.Getenv("TAVILY_API_KEY")),
		TavilyEndpoint:     envString("TAVILY_API_ENDPOINT", "https://api.tavily.com/search"),
		SearchTimeoutSec:   envInt("SIGNAL_SEARCH_TIMEOUT_SEC", 30),
		SearchMaxResults:   envInt("SIGNAL_SEARCH_MAX_RESULTS", 5),
		FetchScope:         envString("TAVILY_FETCH_SCOPE", "global"),
		FetchMarkets:       splitCSV(envString("TAVILY_FETCH_MARKETS", "HK,US,A")),
		FetchCron:          strings.TrimSpace(os.Getenv("TAVILY_FETCH_CRON")),
		GlobalQueries:      splitByDoublePipe(os.Getenv("TAVILY_GLOBAL_QUERIES")),
		ContextTTL:         time.Duration(envInt("TAVILY_CONTEXT_TTL_HOURS", 24)) * time.Hour,
		HTTPAddr:           envString("TAVILY_HTTP_ADDR", ":8088"),
		MySQLHost:          strings.TrimSpace(os.Getenv("MYSQL_HOST")),
		MySQLPort:          envInt("MYSQL_PORT", 3306),
		MySQLUser:          strings.TrimSpace(os.Getenv("MYSQL_USER")),
		MySQLPassword:      os.Getenv("MYSQL_PASSWORD"),
		MySQLDatabase:      strings.TrimSpace(os.Getenv("MYSQL_DATABASE")),
		CompanyQueryMaxLen: envInt("SIGNAL_COMPANY_SEARCH_QUERY_MAX_CHARS", 390),
	}
}

func (c Config) MySQLDSN() string {
	if c.MySQLHost == "" || c.MySQLUser == "" || c.MySQLDatabase == "" {
		return ""
	}
	host := c.MySQLHost
	if c.MySQLPort > 0 {
		host = fmt.Sprintf("%s:%d", c.MySQLHost, c.MySQLPort)
	}
	return (&mysql.Config{
		User:                 c.MySQLUser,
		Passwd:               c.MySQLPassword,
		Net:                  "tcp",
		Addr:                 host,
		DBName:               c.MySQLDatabase,
		ParseTime:            true,
		Loc:                  time.Local,
		Timeout:              10 * time.Second,
		AllowNativePasswords: true,
		Params:               map[string]string{"charset": "utf8mb4"},
	}).FormatDSN()
}

func envString(name, fallback string) string {
	value := strings.TrimSpace(os.Getenv(name))
	if value == "" {
		return fallback
	}
	return value
}

func envInt(name string, fallback int) int {
	value := strings.TrimSpace(os.Getenv(name))
	if value == "" {
		return fallback
	}
	parsed, err := strconv.Atoi(value)
	if err != nil {
		return fallback
	}
	return parsed
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

func splitByDoublePipe(value string) []string {
	parts := strings.Split(value, "||")
	result := make([]string, 0, len(parts))
	for _, part := range parts {
		part = strings.TrimSpace(part)
		if part != "" {
			result = append(result, part)
		}
	}
	return result
}
