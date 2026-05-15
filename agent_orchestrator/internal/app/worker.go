package app

import (
	"context"
	"fmt"
	"time"

	"github.com/SimonZhou96/MoneyManager/agent_orchestrator/internal/repository"
	"github.com/SimonZhou96/MoneyManager/agent_orchestrator/internal/workflow"
)

type Config struct {
	AgentID      string
	PollInterval time.Duration
	JobLimit     int
	StaleAfter   time.Duration
}

type WorkflowRunner interface {
	Run(context.Context, workflow.State) (workflow.State, error)
}

type Worker struct {
	config Config
	repo   repository.JobRepository
	runner WorkflowRunner
}

func NewWorker(config Config, repo repository.JobRepository, runner WorkflowRunner) *Worker {
	if config.AgentID == "" {
		config.AgentID = "go-agent"
	}
	if config.PollInterval <= 0 {
		config.PollInterval = 30 * time.Second
	}
	if config.JobLimit <= 0 {
		config.JobLimit = 5
	}
	if config.StaleAfter <= 0 {
		config.StaleAfter = 30 * time.Minute
	}
	return &Worker{config: config, repo: repo, runner: runner}
}

func (w *Worker) Run(ctx context.Context) error {
	if err := w.validate(); err != nil {
		return err
	}
	ticker := time.NewTicker(w.config.PollInterval)
	defer ticker.Stop()
	for {
		if _, err := w.RunOnce(ctx); err != nil {
			return err
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-ticker.C:
		}
	}
}

func (w *Worker) RunOnce(ctx context.Context) (int, error) {
	if err := w.validate(); err != nil {
		return 0, err
	}
	jobs, err := w.repo.ListPendingJobs(ctx, w.config.JobLimit, w.config.StaleAfter)
	if err != nil {
		return 0, err
	}
	processed := 0
	for _, job := range jobs {
		claimed, err := w.repo.ClaimJob(ctx, job.JobID, w.config.AgentID)
		if err != nil {
			continue
		}
		if claimed == nil {
			continue
		}
		if err := w.processClaimedJob(ctx, *claimed); err != nil {
			return processed, err
		}
		processed++
	}
	return processed, nil
}

func (w *Worker) processClaimedJob(ctx context.Context, job repository.Job) error {
	_ = w.repo.HeartbeatJob(ctx, job.JobID, w.config.AgentID, map[string]any{
		"stage":  "workflow",
		"job_id": job.JobID,
	})
	state, err := w.runner.Run(ctx, stateFromJob(job, w.config.AgentID))
	if err != nil {
		return w.repo.CompleteJob(ctx, repository.JobCompletion{
			JobID:        job.JobID,
			Status:       repository.JobStatusFailed,
			Summary:      failureSummary(job, err),
			ErrorMessage: err.Error(),
		})
	}
	return w.repo.CompleteJob(ctx, completionFromState(state))
}

func (w *Worker) validate() error {
	if w == nil {
		return fmt.Errorf("worker is not initialized")
	}
	if w.repo == nil {
		return fmt.Errorf("worker repository is not configured")
	}
	if w.runner == nil {
		return fmt.Errorf("workflow runner is not configured")
	}
	return nil
}

func stateFromJob(job repository.Job, agentID string) workflow.State {
	markets := append([]string(nil), job.Markets...)
	if len(markets) == 0 && job.Market != "" {
		markets = []string{job.Market}
	}
	return workflow.State{
		JobID:          job.JobID,
		JobType:        job.JobType,
		Markets:        markets,
		Code:           job.Code,
		NormalizedCode: job.NormalizedCode,
		Timeframe:      job.Timeframe,
		ChainKey:       job.ChainKey,
		Options:        job.Options,
		StartedByAgent: agentID,
	}
}

func completionFromState(state workflow.State) repository.JobCompletion {
	taskIDs := make([]string, 0, len(state.MarketResults))
	marketStatuses := make(map[string]any, len(state.MarketResults))
	for _, result := range state.MarketResults {
		if result.TaskID != "" {
			taskIDs = append(taskIDs, result.TaskID)
		}
		status := repository.JobStatusCompleted
		if result.Status == repository.JobStatusFailed || result.Warning != "" {
			status = repository.JobStatusFailed
		}
		marketStatuses[result.Market] = map[string]any{
			"status":       status,
			"task_id":      result.TaskID,
			"passed_count": result.PassedCount,
			"total_count":  result.TotalCount,
		}
		if result.Warning != "" {
			marketStatuses[result.Market].(map[string]any)["error_message"] = result.Warning
		}
	}
	summary := map[string]any{
		"trace":               state.Trace,
		"warnings":            state.Warnings,
		"evidence_status":     state.EvidenceStatus,
		"evidence":            state.Evidence,
		"market_results":      state.MarketResults,
		"market_statuses":     marketStatuses,
		"result_upload_scope": "passed_only",
	}
	status := repository.JobStatusCompleted
	if len(state.MarketResults) > 0 && len(taskIDs) == 0 {
		status = repository.JobStatusFailed
	}
	return repository.JobCompletion{
		JobID:   state.JobID,
		Status:  status,
		TaskIDs: taskIDs,
		Summary: summary,
	}
}

func failureSummary(job repository.Job, err error) map[string]any {
	return map[string]any{
		"job_id":   job.JobID,
		"job_type": job.JobType,
		"warnings": []string{err.Error()},
	}
}
