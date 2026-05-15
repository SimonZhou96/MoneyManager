package mcp

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

const (
	StatusCompleted = "completed"
	StatusSkipped   = "skipped"
)

var defaultAllowedTools = []string{
	"combined_analysis",
	"coin_analysis",
	"financial_news",
	"market_sentiment",
	"multi_timeframe_analysis",
	"yahoo_price",
}

type Config struct {
	Command      string
	Args         []string
	AllowedTools []string
	Timeout      time.Duration
}

type ToolRequest struct {
	Tool      string         `json:"tool"`
	Arguments map[string]any `json:"arguments,omitempty"`
}

type ToolResponse struct {
	Tool    string         `json:"tool"`
	Status  string         `json:"status"`
	Data    map[string]any `json:"data,omitempty"`
	Warning string         `json:"warning,omitempty"`
}

type TradingViewClient struct {
	command string
	args    []string
	allowed map[string]struct{}
	timeout time.Duration
}

func NewTradingViewClient(config Config) *TradingViewClient {
	command := strings.TrimSpace(config.Command)
	if command == "" {
		command = strings.TrimSpace(os.Getenv("TRADINGVIEW_MCP_CMD"))
	}
	allowedTools := config.AllowedTools
	if len(allowedTools) == 0 {
		allowedTools = defaultAllowedTools
	}
	allowed := make(map[string]struct{}, len(allowedTools))
	for _, tool := range allowedTools {
		tool = strings.TrimSpace(tool)
		if tool != "" {
			allowed[tool] = struct{}{}
		}
	}
	timeout := config.Timeout
	if timeout <= 0 {
		timeout = 5 * time.Second
	}
	return &TradingViewClient{
		command: command,
		args:    append([]string(nil), config.Args...),
		allowed: allowed,
		timeout: timeout,
	}
}

func (c *TradingViewClient) Call(ctx context.Context, request ToolRequest) (ToolResponse, error) {
	response := ToolResponse{Tool: request.Tool}
	if c == nil {
		response.Status = StatusSkipped
		response.Warning = "tradingview mcp client is not configured"
		return response, nil
	}
	if _, ok := c.allowed[request.Tool]; !ok {
		return response, fmt.Errorf("tradingview mcp tool %q is not allowed", request.Tool)
	}
	if c.command == "" {
		response.Status = StatusSkipped
		response.Warning = "TRADINGVIEW_MCP_CMD is not configured"
		return response, nil
	}
	path, err := exec.LookPath(c.command)
	if err != nil {
		response.Status = StatusSkipped
		response.Warning = fmt.Sprintf("tradingview mcp command unavailable: %v", err)
		return response, nil
	}

	payload, err := json.Marshal(request)
	if err != nil {
		return response, err
	}
	callCtx, cancel := context.WithTimeout(ctx, c.timeout)
	defer cancel()

	cmd := exec.CommandContext(callCtx, path, c.args...)
	cmd.Stdin = bytes.NewReader(payload)
	output, err := cmd.Output()
	if callCtx.Err() != nil {
		response.Status = StatusSkipped
		response.Warning = fmt.Sprintf("tradingview mcp call timed out after %s", c.timeout)
		return response, nil
	}
	if err != nil {
		response.Status = StatusSkipped
		response.Warning = fmt.Sprintf("tradingview mcp call failed: %v", err)
		return response, nil
	}
	response.Status = StatusCompleted
	response.Data = decodeToolOutput(output)
	return response, nil
}

func decodeToolOutput(output []byte) map[string]any {
	output = bytes.TrimSpace(output)
	if len(output) == 0 {
		return nil
	}
	var decoded map[string]any
	if err := json.Unmarshal(output, &decoded); err == nil {
		return decoded
	}
	return map[string]any{"raw_output": string(output)}
}
