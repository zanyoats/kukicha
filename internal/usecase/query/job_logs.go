package query

import (
	"context"
	"database/sql"
	_ "embed"
	"encoding/json"
	"fmt"
	"math"
)

//go:embed sql/job_log_stats.sql
var jobLogStatsSQL string

//go:embed sql/job_logs.sql
var jobLogsSQL string

type JobLogStats struct {
	Errors   int64
	Warnings int64
	Info     int64
	Debug    int64
}

type JobLog struct {
	ID        int64
	Level     string
	Timestamp string
	Message   string
	Data      map[string]any
}

type JobLogBatch struct {
	Entries    []JobLog
	NextBefore int64
}

func JobLogCounts(ctx context.Context, db interface {
	QueryRowContext(context.Context, string, ...any) *sql.Row
}, jobID int64) (JobLogStats, error) {
	var stats JobLogStats
	if err := db.QueryRowContext(
		ctx, jobLogStatsSQL, sql.Named("job_id", jobID),
	).Scan(&stats.Errors, &stats.Warnings, &stats.Info, &stats.Debug); err != nil {
		return stats, fmt.Errorf("query job log counts: %w", err)
	}
	return stats, nil
}

// JobLogs loads one batch, using zero as the cursor for the initial batch.
func JobLogs(ctx context.Context, db *sql.DB, jobID, before int64, perPage int) (JobLogBatch, error) {
	if perPage < 1 || perPage == math.MaxInt {
		return JobLogBatch{}, fmt.Errorf("invalid job log batch size: %d", perPage)
	}
	if before == 0 {
		before = math.MaxInt64
	}
	rows, err := db.QueryContext(ctx, jobLogsSQL,
		sql.Named("job_id", jobID),
		sql.Named("before", before),
		sql.Named("limit", perPage+1),
	)
	if err != nil {
		return JobLogBatch{}, fmt.Errorf("query job logs: %w", err)
	}
	defer rows.Close()

	batch := JobLogBatch{Entries: make([]JobLog, 0)}
	for rows.Next() {
		var entry JobLog
		var data []byte
		if err := rows.Scan(&entry.ID, &entry.Level, &entry.Timestamp, &entry.Message, &data); err != nil {
			return JobLogBatch{}, fmt.Errorf("scan job log: %w", err)
		}
		if err := json.Unmarshal(data, &entry.Data); err != nil {
			return JobLogBatch{}, fmt.Errorf("decode job log data: %w", err)
		}
		if len(batch.Entries) == perPage {
			batch.NextBefore = batch.Entries[len(batch.Entries)-1].ID
			break
		}
		batch.Entries = append(batch.Entries, entry)
	}
	if err := rows.Err(); err != nil {
		return JobLogBatch{}, fmt.Errorf("iterate job logs: %w", err)
	}
	return batch, nil
}
