package query

import (
	"context"
	"database/sql"
	_ "embed"
	"encoding/json"
	"fmt"
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
	var job Job
	var payload string

	err := db.QueryRowContext(
		ctx,
		jobSQL,
		sql.Named("job_id", id),
	).Scan(
		&job.ID,
		&job.Type,
		&payload,
		&job.State,
		&job.Attempts,
		&job.CreatedAt,
		&job.StartedAt,
		&job.FinishedAt,
		&job.LastError,
		&job.CancelRequestedAt,
	)
	if err != nil {
		return Job{}, fmt.Errorf("read job %d: %w", id, err)
	}

	job.Payload = json.RawMessage(payload)
	return job, nil
}
