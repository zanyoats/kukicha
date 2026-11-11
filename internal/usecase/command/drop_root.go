package command

import (
	"context"
	"database/sql"
	"errors"

	"github.com/zanyoats/kukicha/internal/app"
)

var ErrRootDropBuildRunning = errors.New("Cannot drop a root while a build is in progress. Try again after it finishes.")
var ErrRootNotAvailable = errors.New("This root is pending removal and cannot be changed.")

// MarkRootForRemoval never queues a build. Its first statement is a write,
// serializing the state change with ClaimJob before either operation proceeds.
func MarkRootForRemoval(ctx context.Context, db *sql.DB, configID string) error {
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	result, err := tx.ExecContext(ctx, `
		UPDATE library_roots SET state = 'pending_removal'
		WHERE config_id = ? AND state = 'available'
		AND NOT EXISTS (SELECT 1 FROM jobs WHERE type = 'build' AND state = 'running')`, configID)
	if err != nil {
		return err
	}
	count, err := result.RowsAffected()
	if err != nil {
		return err
	}
	if count == 0 {
		var exists bool
		if err := tx.QueryRowContext(ctx, "SELECT EXISTS (SELECT 1 FROM library_roots WHERE config_id = ?)", configID).Scan(&exists); err != nil {
			return err
		}
		if !exists {
			return ErrRootNotFound
		}
		var running bool
		if err := tx.QueryRowContext(ctx, "SELECT EXISTS (SELECT 1 FROM jobs WHERE type = 'build' AND state = 'running')").Scan(&running); err != nil {
			return err
		}
		if running {
			return ErrRootDropBuildRunning
		}
		return ErrRootNotAvailable
	}
	// Radio positions were built from the previous available-track set.
	// Invalidate their cursors until the next rebuild publishes new membership.
	if _, err := tx.ExecContext(ctx, "DELETE FROM app_metadata WHERE key = ?", app.RadioMembershipVersion); err != nil {
		return err
	}
	return tx.Commit()
}
