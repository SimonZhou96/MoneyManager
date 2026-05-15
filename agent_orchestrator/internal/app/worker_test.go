package app

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/SimonZhou96/MoneyManager/agent_orchestrator/internal/repository"
	"github.com/SimonZhou96/MoneyManager/agent_orchestrator/internal/workflow"
)

type fakeJobRepository struct {
	pending     []repository.Job
	claimed     *repository.Job
	heartbeats  int
	completions []repository.JobCompletion
}

func (f *fakeJobRepository) ListPendingJobs(context.Context, int, time.Duration) ([]repository.Job, error) {
	return f.pending, nil
}

func (f *fakeJobRepository) ClaimJob(context.Context, string, string) (*repository.Job, error) {
	return f.claimed, nil
}

func (f *fakeJobRepository) HeartbeatJob(context.Context, string, string, map[string]any) error {
	f.heartbeats++
	return nil
}

func (f *fakeJobRepository) CompleteJob(_ context.Context, completion repository.JobCompletion) error {
	f.completions = append(f.completions, completion)
	return nil
}

type fakeWorkflowRunner struct {
	err error
}

func (f fakeWorkflowRunner) Run(_ context.Context, state workflow.State) (workflow.State, error) {
	if f.err != nil {
		return state, f.err
	}
	state.Trace = append(state.Trace, "execute_rules", "collect_evidence")
	state.EvidenceStatus = "completed"
	state.MarketResults = []workflow.MarketResult{{Market: "HK", TaskID: "task-1", PassedCount: 2}}
	return state, nil
}

func TestWorkerRunOnceClaimsRunsAndCompletesJob(t *testing.T) {
	job := repository.Job{JobID: "job-1", JobType: "screening", Markets: []string{"HK"}, Timeframe: "1d"}
	repo := &fakeJobRepository{pending: []repository.Job{job}, claimed: &job}
	worker := NewWorker(Config{AgentID: "agent-1", PollInterval: time.Second}, repo, fakeWorkflowRunner{})

	processed, err := worker.RunOnce(context.Background())
	if err != nil {
		t.Fatalf("RunOnce: %v", err)
	}
	if processed != 1 {
		t.Fatalf("expected 1 processed job, got %d", processed)
	}
	if repo.heartbeats == 0 {
		t.Fatal("expected heartbeat before workflow run")
	}
	if len(repo.completions) != 1 {
		t.Fatalf("expected completion, got %#v", repo.completions)
	}
	completion := repo.completions[0]
	if completion.Status != repository.JobStatusCompleted {
		t.Fatalf("expected completed, got %#v", completion)
	}
	if len(completion.TaskIDs) != 1 || completion.TaskIDs[0] != "task-1" {
		t.Fatalf("unexpected task ids: %#v", completion.TaskIDs)
	}
}

func TestWorkerRunOnceCompletesFailedWhenWorkflowFails(t *testing.T) {
	job := repository.Job{JobID: "job-1", JobType: "screening", Markets: []string{"HK"}, Timeframe: "1d"}
	repo := &fakeJobRepository{pending: []repository.Job{job}, claimed: &job}
	worker := NewWorker(Config{AgentID: "agent-1", PollInterval: time.Second}, repo, fakeWorkflowRunner{err: errors.New("rules failed")})

	processed, err := worker.RunOnce(context.Background())
	if err != nil {
		t.Fatalf("RunOnce should keep polling after job failure: %v", err)
	}
	if processed != 1 {
		t.Fatalf("expected 1 processed job, got %d", processed)
	}
	if len(repo.completions) != 1 {
		t.Fatalf("expected completion, got %#v", repo.completions)
	}
	completion := repo.completions[0]
	if completion.Status != repository.JobStatusFailed || completion.ErrorMessage == "" {
		t.Fatalf("expected failed completion with error, got %#v", completion)
	}
}
