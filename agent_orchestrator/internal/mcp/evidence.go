package mcp

import (
	"context"
	"fmt"

	"github.com/SimonZhou96/MoneyManager/agent_orchestrator/internal/workflow"
)

type ToolCaller interface {
	Call(context.Context, ToolRequest) (ToolResponse, error)
}

type EvidenceService struct {
	client ToolCaller
	tool   string
}

func NewEvidenceService(client ToolCaller, tool string) *EvidenceService {
	if tool == "" {
		tool = "combined_analysis"
	}
	return &EvidenceService{client: client, tool: tool}
}

func (s *EvidenceService) CollectEvidence(ctx context.Context, state workflow.State) (workflow.State, error) {
	state.Trace = append(state.Trace, "collect_evidence")
	if s == nil || s.client == nil {
		state.EvidenceStatus = workflow.EvidenceStatusSkipped
		state.Warnings = append(state.Warnings, "tradingview mcp evidence skipped: client is not configured")
		return state, nil
	}
	if len(state.MarketResults) == 0 {
		state.EvidenceStatus = workflow.EvidenceStatusSkipped
		state.Warnings = append(state.Warnings, "tradingview mcp evidence skipped: no market results")
		return state, nil
	}

	completed := 0
	for _, market := range state.MarketResults {
		resp, err := s.client.Call(ctx, ToolRequest{
			Tool: s.tool,
			Arguments: map[string]any{
				"market":       market.Market,
				"task_id":      market.TaskID,
				"passed_count": market.PassedCount,
			},
		})
		if err != nil {
			return state, err
		}
		summary := workflow.EvidenceSummary{
			Market:  market.Market,
			Tool:    resp.Tool,
			Status:  resp.Status,
			Warning: resp.Warning,
			Data:    resp.Data,
		}
		state.Evidence = append(state.Evidence, summary)
		if resp.Status == StatusCompleted {
			completed++
			continue
		}
		if resp.Warning != "" {
			state.Warnings = append(state.Warnings, fmt.Sprintf("%s: %s", market.Market, resp.Warning))
		}
	}
	if completed > 0 {
		state.EvidenceStatus = workflow.EvidenceStatusCompleted
	} else {
		state.EvidenceStatus = workflow.EvidenceStatusSkipped
	}
	return state, nil
}
