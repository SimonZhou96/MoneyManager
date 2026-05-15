package mcp

import (
	"context"
	"strings"
	"testing"
	"time"
)

func TestTradingViewClientRejectsNonWhitelistedTool(t *testing.T) {
	client := NewTradingViewClient(Config{
		Command:      "missing-tradingview-mcp",
		AllowedTools: []string{"combined_analysis"},
		Timeout:      time.Second,
	})

	_, err := client.Call(context.Background(), ToolRequest{Tool: "shell", Arguments: map[string]any{"cmd": "pwd"}})
	if err == nil || !strings.Contains(err.Error(), "not allowed") {
		t.Fatalf("expected whitelist error, got %v", err)
	}
}

func TestTradingViewClientSkipsWhenCommandUnavailable(t *testing.T) {
	client := NewTradingViewClient(Config{
		Command:      "missing-tradingview-mcp",
		AllowedTools: []string{"combined_analysis"},
		Timeout:      time.Second,
	})

	resp, err := client.Call(context.Background(), ToolRequest{Tool: "combined_analysis", Arguments: map[string]any{"symbol": "AAPL"}})
	if err != nil {
		t.Fatalf("Call should gracefully skip unavailable command: %v", err)
	}
	if resp.Status != StatusSkipped {
		t.Fatalf("expected skipped response, got %#v", resp)
	}
	if resp.Warning == "" {
		t.Fatalf("expected warning for unavailable command")
	}
}
