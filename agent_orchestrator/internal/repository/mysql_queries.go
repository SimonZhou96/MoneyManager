package repository

import (
	"context"
	"database/sql"
)

type rowScanner interface {
	Scan(dest ...any) error
}

func (r *MySQLRepository) listPendingScreeningJobs(ctx context.Context, limit int) ([]Job, error) {
	rows, err := r.db.QueryContext(ctx, `
		SELECT DISTINCT j.job_id, j.user_id, j.markets, j.timeframe, j.status,
		       j.options_json, CAST(j.created_at AS CHAR)
		FROM web_screening_jobs j
		JOIN screening_run_locks l ON l.job_id=j.job_id
		WHERE l.status IN ('queued','expired')
		  AND j.status IN ('queued','running')
		ORDER BY j.created_at ASC
		LIMIT ?`,
		limit,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	return scanWebJobs(rows, JobTypeScreening)
}

func (r *MySQLRepository) listPendingCustomListJobs(ctx context.Context, limit int) ([]Job, error) {
	rows, err := r.db.QueryContext(ctx, `
		SELECT j.job_id, j.user_id, j.markets, j.timeframe, j.status,
		       j.options_json, CAST(j.created_at AS CHAR)
		FROM web_screening_jobs j
		WHERE j.status IN ('queued','expired')
		  AND JSON_UNQUOTE(JSON_EXTRACT(j.options_json, '$.job_kind')) = 'custom_list'
		ORDER BY j.created_at ASC
		LIMIT ?`,
		limit,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	return scanWebJobs(rows, JobTypeScreening)
}

func (r *MySQLRepository) listPendingSingleStockJobs(ctx context.Context, limit int) ([]Job, error) {
	rows, err := r.db.QueryContext(ctx, `
		SELECT run_id, user_id, market, code, normalized_code, timeframe,
		       chain_key, status, CAST(created_at AS CHAR)
		FROM single_stock_runs
		WHERE status IN ('queued','expired')
		ORDER BY created_at ASC
		LIMIT ?`,
		limit,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	jobs := make([]Job, 0)
	for rows.Next() {
		job, err := scanSingleStockJob(rows)
		if err != nil {
			return nil, err
		}
		jobs = append(jobs, job)
	}
	return jobs, rows.Err()
}

func getWebScreeningJob(ctx context.Context, tx *sql.Tx, jobID string) (*Job, error) {
	row := tx.QueryRowContext(ctx, `
		SELECT job_id, user_id, markets, timeframe, status, options_json, CAST(created_at AS CHAR)
		FROM web_screening_jobs
		WHERE job_id=?
		LIMIT 1`,
		jobID,
	)
	job, err := scanWebJob(row, JobTypeScreening)
	if err == sql.ErrNoRows {
		return nil, nil
	}
	return &job, err
}

func getSingleStockRun(ctx context.Context, tx *sql.Tx, runID string) (*Job, error) {
	row := tx.QueryRowContext(ctx, `
		SELECT run_id, user_id, market, code, normalized_code, timeframe,
		       chain_key, status, CAST(created_at AS CHAR)
		FROM single_stock_runs
		WHERE run_id=?
		LIMIT 1`,
		runID,
	)
	job, err := scanSingleStockJob(row)
	if err == sql.ErrNoRows {
		return nil, nil
	}
	return &job, err
}

func scanWebJobs(rows *sql.Rows, jobType string) ([]Job, error) {
	jobs := make([]Job, 0)
	for rows.Next() {
		job, err := scanWebJob(rows, jobType)
		if err != nil {
			return nil, err
		}
		jobs = append(jobs, job)
	}
	return jobs, rows.Err()
}

func scanWebJob(scanner rowScanner, jobType string) (Job, error) {
	var (
		jobID     string
		userID    sql.NullInt64
		markets   sql.NullString
		timeframe sql.NullString
		status    sql.NullString
		options   sql.NullString
		createdAt sql.NullString
	)
	if err := scanner.Scan(&jobID, &userID, &markets, &timeframe, &status, &options, &createdAt); err != nil {
		return Job{}, err
	}
	decodedOptions := decodeMap(options)
	return Job{
		JobID:     jobID,
		JobType:   jobType,
		UserID:    uint64Ptr(userID),
		Markets:   decodeStringSlice(markets),
		Timeframe: timeframe.String,
		Status:    status.String,
		Options:   decodedOptions,
		ChainKey:  stringValue(decodedOptions, "chain_key"),
		ChainName: stringValue(decodedOptions, "chain_name"),
		CreatedAt: createdAt.String,
	}, nil
}

func scanSingleStockJob(scanner rowScanner) (Job, error) {
	var (
		runID          string
		userID         sql.NullInt64
		market         sql.NullString
		code           sql.NullString
		normalizedCode sql.NullString
		timeframe      sql.NullString
		chainKey       sql.NullString
		status         sql.NullString
		createdAt      sql.NullString
	)
	if err := scanner.Scan(&runID, &userID, &market, &code, &normalizedCode, &timeframe, &chainKey, &status, &createdAt); err != nil {
		return Job{}, err
	}
	return Job{
		JobID:          runID,
		RunID:          runID,
		JobType:        JobTypeSingleStock,
		UserID:         uint64Ptr(userID),
		Market:         market.String,
		Markets:        []string{market.String},
		Code:           code.String,
		NormalizedCode: normalizedCode.String,
		Timeframe:      timeframe.String,
		ChainKey:       chainKey.String,
		Status:         status.String,
		CreatedAt:      createdAt.String,
	}, nil
}

func execRows(ctx context.Context, tx *sql.Tx, query string, args ...any) (int64, error) {
	result, err := tx.ExecContext(ctx, query, args...)
	if err != nil {
		return 0, err
	}
	return result.RowsAffected()
}

func commitJob(tx *sql.Tx, job *Job) (*Job, error) {
	if err := tx.Commit(); err != nil {
		return nil, err
	}
	return job, nil
}
