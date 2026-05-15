package repository

import (
	"context"
	"database/sql"
	"fmt"
	"time"

	_ "github.com/go-sql-driver/mysql"
)

type MySQLRepository struct {
	db *sql.DB
}

func OpenMySQLRepository(dsn string) (*MySQLRepository, error) {
	if dsn == "" {
		return nil, fmt.Errorf("mysql dsn is required")
	}
	db, err := sql.Open("mysql", dsn)
	if err != nil {
		return nil, err
	}
	return NewMySQLRepository(db), nil
}

func NewMySQLRepository(db *sql.DB) *MySQLRepository {
	return &MySQLRepository{db: db}
}

func (r *MySQLRepository) DB() *sql.DB {
	if r == nil {
		return nil
	}
	return r.db
}

func (r *MySQLRepository) Close() error {
	if r == nil || r.db == nil {
		return nil
	}
	return r.db.Close()
}

func (r *MySQLRepository) ListPendingJobs(ctx context.Context, limit int, staleAfter time.Duration) ([]Job, error) {
	if err := r.requireDB(); err != nil {
		return nil, err
	}
	limit = NormalizeLimit(limit)
	if staleAfter <= 0 {
		staleAfter = 30 * time.Minute
	}
	if _, err := r.expireStaleAgentLocks(ctx, staleAfter); err != nil {
		return nil, err
	}

	jobs, err := r.listPendingScreeningJobs(ctx, limit)
	if err != nil {
		return nil, err
	}
	customJobs, err := r.listPendingCustomListJobs(ctx, limit)
	if err != nil {
		return nil, err
	}
	jobs = append(jobs, customJobs...)
	singleJobs, err := r.listPendingSingleStockJobs(ctx, limit)
	if err != nil {
		return nil, err
	}
	jobs = append(jobs, singleJobs...)
	if len(jobs) > limit {
		jobs = jobs[:limit]
	}
	return jobs, nil
}

func (r *MySQLRepository) ClaimJob(ctx context.Context, jobID string, agentID string) (*Job, error) {
	if err := r.requireDB(); err != nil {
		return nil, err
	}
	tx, err := r.db.BeginTx(ctx, nil)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()

	now := time.Now().UTC()
	claimed, err := execRows(ctx, tx, `
		UPDATE screening_run_locks
		SET status='running', agent_id=?, claimed_at=COALESCE(claimed_at,?),
		    heartbeat_at=?, error_message=NULL
		WHERE job_id=? AND status IN ('queued','expired')`,
		agentID, now, now, jobID,
	)
	if err != nil {
		return nil, err
	}
	if claimed > 0 {
		if _, err := execRows(ctx, tx, `
			UPDATE web_screening_jobs
			SET status='running', agent_id=?, claimed_at=COALESCE(claimed_at,?),
			    heartbeat_at=?, error_message=NULL
			WHERE job_id=?`,
			agentID, now, now, jobID,
		); err != nil {
			return nil, err
		}
		job, err := getWebScreeningJob(ctx, tx, jobID)
		if err != nil {
			return nil, err
		}
		return commitJob(tx, job)
	}

	claimed, err = execRows(ctx, tx, `
		UPDATE web_screening_jobs
		SET status='running', agent_id=?, claimed_at=COALESCE(claimed_at,?),
		    heartbeat_at=?, error_message=NULL
		WHERE job_id=?
		  AND status IN ('queued','expired')
		  AND JSON_UNQUOTE(JSON_EXTRACT(options_json, '$.job_kind')) = 'custom_list'`,
		agentID, now, now, jobID,
	)
	if err != nil {
		return nil, err
	}
	if claimed > 0 {
		job, err := getWebScreeningJob(ctx, tx, jobID)
		if err != nil {
			return nil, err
		}
		return commitJob(tx, job)
	}

	claimed, err = execRows(ctx, tx, `
		UPDATE single_stock_runs
		SET status='running', agent_id=?, claimed_at=COALESCE(claimed_at,?),
		    heartbeat_at=?, error_message=NULL
		WHERE run_id=? AND status IN ('queued','expired')`,
		agentID, now, now, jobID,
	)
	if err != nil {
		return nil, err
	}
	if claimed > 0 {
		job, err := getSingleStockRun(ctx, tx, jobID)
		if err != nil {
			return nil, err
		}
		return commitJob(tx, job)
	}
	if err := tx.Commit(); err != nil {
		return nil, err
	}
	return nil, nil
}

func (r *MySQLRepository) HeartbeatJob(ctx context.Context, jobID string, agentID string, progress map[string]any) error {
	if err := r.requireDB(); err != nil {
		return err
	}
	progressJSON, err := jsonOrNil(progress)
	if err != nil {
		return err
	}
	now := time.Now().UTC()
	if _, err := r.db.ExecContext(ctx, `
		UPDATE screening_run_locks
		SET heartbeat_at=?
		WHERE job_id=? AND agent_id=? AND status='running'`,
		now, jobID, agentID,
	); err != nil {
		return err
	}
	if _, err := r.db.ExecContext(ctx, `
		UPDATE web_screening_jobs
		SET heartbeat_at=?, summary_json=COALESCE(?, summary_json)
		WHERE job_id=? AND agent_id=? AND status='running'`,
		now, progressJSON, jobID, agentID,
	); err != nil {
		return err
	}
	_, err = r.db.ExecContext(ctx, `
		UPDATE single_stock_runs
		SET heartbeat_at=?
		WHERE run_id=? AND agent_id=? AND status='running'`,
		now, jobID, agentID,
	)
	return err
}

func (r *MySQLRepository) CompleteJob(ctx context.Context, completion JobCompletion) error {
	if err := r.requireDB(); err != nil {
		return err
	}
	if completion.Status != JobStatusCompleted {
		completion.Status = JobStatusFailed
	}
	taskIDsJSON, err := jsonOrNil(completion.TaskIDs)
	if err != nil {
		return err
	}
	summaryJSON, err := jsonOrNil(completion.Summary)
	if err != nil {
		return err
	}
	now := time.Now().UTC()
	tx, err := r.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()

	if err := completeRunLocks(ctx, tx, completion, now); err != nil {
		return err
	}
	if _, err := tx.ExecContext(ctx, `
		UPDATE web_screening_jobs
		SET status=?, task_ids=COALESCE(?, task_ids), error_message=?,
		    summary_json=COALESCE(?, summary_json), finished_at=?
		WHERE job_id=?`,
		completion.Status,
		taskIDsJSON,
		nullableString(completion.ErrorMessage),
		summaryJSON,
		now,
		completion.JobID,
	); err != nil {
		return err
	}
	if _, err := tx.ExecContext(ctx, `
		UPDATE single_stock_runs
		SET status=?, error_message=?, warnings_json=COALESCE(?, warnings_json), finished_at=?
		WHERE run_id=?`,
		completion.Status,
		nullableString(completion.ErrorMessage),
		summaryJSON,
		now,
		completion.JobID,
	); err != nil {
		return err
	}
	return tx.Commit()
}

func (r *MySQLRepository) requireDB() error {
	if r == nil || r.db == nil {
		return fmt.Errorf("mysql repository is not initialized")
	}
	return nil
}

func (r *MySQLRepository) expireStaleAgentLocks(ctx context.Context, staleAfter time.Duration) (int64, error) {
	cutoff := time.Now().UTC().Add(-staleAfter)
	result, err := r.db.ExecContext(ctx, `
		UPDATE screening_run_locks
		SET status='expired', error_message='Agent heartbeat timeout'
		WHERE status='running' AND heartbeat_at IS NOT NULL AND heartbeat_at < ?`,
		cutoff,
	)
	if err != nil {
		return 0, err
	}
	count, _ := result.RowsAffected()
	if _, err := r.db.ExecContext(ctx, `
		UPDATE web_screening_jobs
		SET status='queued', error_message='Agent heartbeat timeout'
		WHERE status='running'
		  AND heartbeat_at IS NOT NULL
		  AND heartbeat_at < ?
		  AND job_id IN (SELECT job_id FROM screening_run_locks WHERE status='expired')`,
		cutoff,
	); err != nil {
		return count, err
	}
	if _, err := r.db.ExecContext(ctx, `
		UPDATE single_stock_runs
		SET status='expired', error_message='Agent heartbeat timeout'
		WHERE status='running' AND heartbeat_at IS NOT NULL AND heartbeat_at < ?`,
		cutoff,
	); err != nil {
		return count, err
	}
	return count, nil
}

func completeRunLocks(ctx context.Context, tx *sql.Tx, completion JobCompletion, now time.Time) error {
	marketStatuses, _ := completion.Summary["market_statuses"].(map[string]any)
	if len(marketStatuses) == 0 {
		lockStatus := JobStatusFailed
		if completion.Status == JobStatusCompleted {
			lockStatus = JobStatusCompleted
		}
		_, err := tx.ExecContext(ctx, `
			UPDATE screening_run_locks
			SET status=?, completed_at=?, error_message=?
			WHERE job_id=?`,
			lockStatus, now, nullableString(completion.ErrorMessage), completion.JobID,
		)
		return err
	}
	for market, raw := range marketStatuses {
		item, _ := raw.(map[string]any)
		lockStatus := JobStatusFailed
		if stringValue(item, "status") == JobStatusCompleted {
			lockStatus = JobStatusCompleted
		}
		if _, err := tx.ExecContext(ctx, `
			UPDATE screening_run_locks
			SET status=?, task_id=COALESCE(?, task_id), completed_at=?, error_message=?
			WHERE job_id=? AND market=?`,
			lockStatus,
			nullableString(stringValue(item, "task_id")),
			now,
			nullableString(stringValue(item, "error_message")),
			completion.JobID,
			market,
		); err != nil {
			return err
		}
	}
	return nil
}

func nullableString(value string) any {
	if value == "" {
		return nil
	}
	return value
}
