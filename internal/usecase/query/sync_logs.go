package query

import (
	"context"
	"database/sql"
	_ "embed"
	"fmt"
	"math"
)

//go:embed sql/sync_log_stats.sql
var syncLogStatsSQL string

//go:embed sql/sync_logs.sql
var syncLogsSQL string

type SyncLogStats struct {
	Errors   int64
	Warnings int64
	Info     int64
}

type SyncLog struct {
	ID        int64
	Level     string
	Timestamp string
	Message   string
	RootPath  string
	Path      string
}

type SyncLogBatch struct {
	Entries    []SyncLog
	NextBefore int64
}

func SyncLogCounts(ctx context.Context, db *sql.DB) (SyncLogStats, error) {
	var stats SyncLogStats
	if err := db.QueryRowContext(ctx, syncLogStatsSQL).Scan(&stats.Errors, &stats.Warnings, &stats.Info); err != nil {
		return stats, fmt.Errorf("query sync log counts: %w", err)
	}
	return stats, nil
}

// SyncLogs loads one batch, using zero as the cursor for the initial batch.
func SyncLogs(ctx context.Context, db *sql.DB, before int64, perPage int) (SyncLogBatch, error) {
	if perPage < 1 || int64(perPage) == math.MaxInt64 {
		return SyncLogBatch{}, fmt.Errorf("invalid sync log batch size: %d", perPage)
	}
	if before == 0 {
		before = math.MaxInt64
	}
	rows, err := db.QueryContext(ctx, syncLogsSQL,
		sql.Named("before", before), sql.Named("limit", int64(perPage)+1))
	if err != nil {
		return SyncLogBatch{}, fmt.Errorf("query sync logs: %w", err)
	}
	defer rows.Close()

	batch := SyncLogBatch{Entries: make([]SyncLog, 0)}
	for rows.Next() {
		var entry SyncLog
		if err := rows.Scan(&entry.ID, &entry.Level, &entry.Timestamp, &entry.Message, &entry.RootPath, &entry.Path); err != nil {
			return SyncLogBatch{}, fmt.Errorf("scan sync log: %w", err)
		}
		if len(batch.Entries) == perPage {
			batch.NextBefore = batch.Entries[len(batch.Entries)-1].ID
			break
		}
		batch.Entries = append(batch.Entries, entry)
	}
	if err := rows.Err(); err != nil {
		return SyncLogBatch{}, fmt.Errorf("iterate sync logs: %w", err)
	}
	return batch, nil
}
