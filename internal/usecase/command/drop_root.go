package command

import (
	"context"
	"database/sql"
	"errors"
)

var ErrRootDropBuildRunning = errors.New("Cannot drop a root while a build is in progress. Try again after it finishes.")
var ErrRootNotLost = errors.New("This root is no longer lost or has already been marked for removal. Refresh the page to see its current state.")

// MarkLostRootForRemoval never queues a build. Its first statement is a write,
// serializing the state change with ClaimJob before either operation proceeds.
func MarkLostRootForRemoval(ctx context.Context, db *sql.DB, configID string) error {
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	result, err := tx.ExecContext(ctx, `
		UPDATE library_roots SET state = 'pending_removal'
		WHERE config_id = ? AND state = 'lost'
		AND NOT EXISTS (SELECT 1 FROM jobs WHERE type = 'build' AND state = 'running')`, configID)
	if err != nil {
		return err
	}
	count, err := result.RowsAffected()
	if err != nil {
		return err
	}
	if count == 0 {
		var running bool
		if err := tx.QueryRowContext(ctx, "SELECT EXISTS (SELECT 1 FROM jobs WHERE type = 'build' AND state = 'running')").Scan(&running); err != nil {
			return err
		}
		if running {
			return ErrRootDropBuildRunning
		}
		return ErrRootNotLost
	}
	return tx.Commit()
}
