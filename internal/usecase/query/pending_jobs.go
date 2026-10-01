package query

import (
	"context"
	"database/sql"
	_ "embed"
	"fmt"
)

//go:embed sql/pending_jobs.sql
var pendingJobsSQL string

type PendingJob struct {
	ID              int64  `json:"id"`
	Type            string `json:"type"`
	State           string `json:"state"`
	CancelRequested bool   `json:"cancel_requested"`
}

// PendingJobs returns running jobs first, followed by queued jobs in claim order.
func PendingJobs(ctx context.Context, db interface {
	QueryContext(context.Context, string, ...any) (*sql.Rows, error)
}) ([]PendingJob, error) {
	rows, err := db.QueryContext(ctx, pendingJobsSQL)
	if err != nil {
		return nil, fmt.Errorf("read pending jobs: %w", err)
	}
	defer rows.Close()
	jobs := make([]PendingJob, 0)
	for rows.Next() {
		var job PendingJob
		if err := rows.Scan(&job.ID, &job.Type, &job.State, &job.CancelRequested); err != nil {
			return nil, err
		}
		jobs = append(jobs, job)
	}
	return jobs, rows.Err()
}
