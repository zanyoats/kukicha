package command

import (
	"context"
	"database/sql"
	_ "embed"
	"errors"
	"fmt"
)

//go:embed sql/insert_library_sync_log.sql
var insertLibrarySyncLogSQL string

//go:embed sql/sync_log_counts.sql
var syncLogCountsSQL string

// ErrSyncLogWrite identifies failures that must not be logged recursively.
var ErrSyncLogWrite = errors.New("write library sync log")

type SyncLogStats struct {
	ErrorCount int
	WarnCount  int
	InfoCount  int
}

// InsertLibrarySyncLog accepts a connection or transaction. Paths are relative to rootPath.
func InsertLibrarySyncLog(ctx context.Context, db interface {
	ExecContext(context.Context, string, ...any) (sql.Result, error)
}, level, message, rootPath, path string) error {
	if path == "" {
		path = "."
	}
	_, err := db.ExecContext(ctx, insertLibrarySyncLogSQL,
		sql.Named("level", level), sql.Named("message", message),
		sql.Named("root_path", rootPath), sql.Named("path", path))
	if err != nil {
		return fmt.Errorf("%w: %w", ErrSyncLogWrite, err)
	}
	return nil
}
