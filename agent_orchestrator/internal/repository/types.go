package repository

import (
	"context"
	"time"
)

const (
	JobTypeScreening    = "screening"
	JobTypeSingleStock  = "single_stock"
	JobStatusQueued     = "queued"
	JobStatusRunning    = "running"
	JobStatusExpired    = "expired"
	JobStatusCompleted  = "completed"
	JobStatusFailed     = "failed"
	defaultPollJobLimit = 5
)

type Job struct {
	JobID          string         `json:"job_id"`
	RunID          string         `json:"run_id,omitempty"`
	JobType        string         `json:"job_type"`
	UserID         *uint64        `json:"user_id,omitempty"`
	Markets        []string       `json:"markets,omitempty"`
	Market         string         `json:"market,omitempty"`
	Code           string         `json:"code,omitempty"`
	NormalizedCode string         `json:"normalized_code,omitempty"`
	Timeframe      string         `json:"timeframe,omitempty"`
	Status         string         `json:"status,omitempty"`
	Options        map[string]any `json:"options,omitempty"`
	ChainKey       string         `json:"chain_key,omitempty"`
	ChainName      string         `json:"chain_name,omitempty"`
	CreatedAt      string         `json:"created_at,omitempty"`
}

type JobCompletion struct {
	JobID        string         `json:"job_id"`
	Status       string         `json:"status"`
	TaskIDs      []string       `json:"task_ids,omitempty"`
	Summary      map[string]any `json:"summary,omitempty"`
	ErrorMessage string         `json:"error_message,omitempty"`
}

type JobRepository interface {
	ListPendingJobs(ctx context.Context, limit int, staleAfter time.Duration) ([]Job, error)
	ClaimJob(ctx context.Context, jobID string, agentID string) (*Job, error)
	HeartbeatJob(ctx context.Context, jobID string, agentID string, progress map[string]any) error
	CompleteJob(ctx context.Context, completion JobCompletion) error
}

func NormalizeLimit(limit int) int {
	if limit <= 0 {
		return defaultPollJobLimit
	}
	if limit > 20 {
		return 20
	}
	return limit
}
