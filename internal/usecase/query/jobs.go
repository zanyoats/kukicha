package query

import (
	"context"
	"database/sql"
	_ "embed"
	"encoding/json"
	"fmt"
	"math"
)

//go:embed sql/job.sql
var jobSQL string

type Job struct {
	ID                int64
	Type              string
	Payload           json.RawMessage
	State             string
	Attempts          int64
	CreatedAt         string
	StartedAt         sql.NullString
	FinishedAt        sql.NullString
	LastError         sql.NullString
	CancelRequestedAt sql.NullString
}

type JobReader interface {
	QueryRowContext(context.Context, string, ...any) *sql.Row
}

// Missing ID will wrap sql.ErrNoRows.
func JobByID(ctx context.Context, db JobReader, id int64) (Job, error) {
	job, err := scanJob(db.QueryRowContext(ctx, jobSQL, sql.Named("job_id", id)))
	if err != nil {
		return Job{}, fmt.Errorf("read job %d: %w", id, err)
	}

	return job, nil
}

func scanJob(row interface{ Scan(...any) error }) (Job, error) {
	var job Job
	var payload string
	err := row.Scan(&job.ID, &job.Type, &payload, &job.State, &job.Attempts,
		&job.CreatedAt, &job.StartedAt, &job.FinishedAt, &job.LastError, &job.CancelRequestedAt)
	job.Payload = json.RawMessage(payload)
	return job, err
}

//go:embed sql/jobs.sql
var jobsSQL string

type JobBatch struct {
	Entries    []Job
	NextBefore int64
}

func Jobs(ctx context.Context, db *sql.DB, before int64, perPage int) (JobBatch, error) {
	if perPage < 1 || perPage == math.MaxInt {
		return JobBatch{}, fmt.Errorf("invalid job batch size: %d", perPage)
	}
	rows, err := db.QueryContext(ctx, jobsSQL, sql.Named("before", before), sql.Named("limit", perPage+1))
	if err != nil {
		return JobBatch{}, fmt.Errorf("query jobs: %w", err)
	}
	defer rows.Close()
	batch := JobBatch{Entries: make([]Job, 0)}
	for rows.Next() {
		job, err := scanJob(rows)
		if err != nil {
			return JobBatch{}, fmt.Errorf("scan job: %w", err)
		}
		if len(batch.Entries) == perPage {
			batch.NextBefore = batch.Entries[len(batch.Entries)-1].ID
			break
		}
		batch.Entries = append(batch.Entries, job)
	}
	if err := rows.Err(); err != nil {
		return JobBatch{}, fmt.Errorf("iterate jobs: %w", err)
	}
	return batch, nil
}
