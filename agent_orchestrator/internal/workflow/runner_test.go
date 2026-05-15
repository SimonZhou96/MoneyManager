package workflow

import (
	"context"
	"errors"
	"testing"
)

type fakeExecutor struct {
	err error
}

func (f fakeExecutor) ExecuteRules(context.Context, State) (State, error) {
	if f.err != nil {
		return State{}, f.err
	}
	return State{JobID: "job-1", Trace: []string{"execute_rules"}, MarketResults: []MarketResult{{Market: "HK", TaskID: "task-1", PassedCount: 1}}}, nil
}

type fakeEvidence struct {
	err error
}

func (f fakeEvidence) CollectEvidence(context.Context, State) (State, error) {
	if f.err != nil {
		return State{}, f.err
	}
	return State{JobID: "job-1", Trace: []string{"execute_rules", "collect_evidence"}, EvidenceStatus: "completed", MarketResults: []MarketResult{{Market: "HK", TaskID: "task-1", PassedCount: 1}}}, nil
}

func TestRunnerSkipsMCPWhenEvidenceFails(t *testing.T) {
	runner, err := NewRunner(fakeExecutor{}, fakeEvidence{err: errors.New("mcp down")})
	if err != nil {
		t.Fatalf("NewRunner: %v", err)
	}
	state, err := runner.Run(context.Background(), State{JobID: "job-1"})
	if err != nil {
		t.Fatalf("Run returned error: %v", err)
	}
	if state.EvidenceStatus != "skipped" {
		t.Fatalf("expected evidence to be skipped, got %#v", state)
	}
	if len(state.Warnings) != 1 {
		t.Fatalf("expected warning, got %#v", state.Warnings)
	}
}
