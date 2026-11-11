package command

import (
	"context"
	"database/sql"
	"errors"

	"github.com/zanyoats/kukicha/internal/app"
)

var ErrRootDropBuildRunning = errors.New("Cannot remove a storage location while a build is in progress. Try again after it finishes.")
var ErrRootRestoreBuildRunning = errors.New("Cannot undo removal while a build is in progress. Try again after it finishes.")
var ErrRootNotPendingRemoval = errors.New("This storage location is not pending removal.")
var ErrRootNotAvailable = errors.New("This storage location is pending removal and cannot be changed.")

// MarkRootForRemoval never queues a build. Its first statement is a write,
// serializing the state change with ClaimJob before either operation proceeds.
func MarkRootForRemoval(ctx context.Context, db *sql.DB, configID string) error {
	return changeRootState(ctx, db, configID, "available", "pending_removal", ErrRootDropBuildRunning, ErrRootNotAvailable)
}

// RestoreRoot cancels removal before a build deletes the storage location.
func RestoreRoot(ctx context.Context, db *sql.DB, configID string) error {
	return changeRootState(ctx, db, configID, "pending_removal", "available", ErrRootRestoreBuildRunning, ErrRootNotPendingRemoval)
}

func changeRootState(ctx context.Context, db *sql.DB, configID, from, to string, runningError, stateError error) error {
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	result, err := tx.ExecContext(ctx, `
		UPDATE library_roots SET state = ?
		WHERE config_id = ? AND state = ?
		AND NOT EXISTS (SELECT 1 FROM jobs WHERE type = 'build' AND state = 'running')`, to, configID, from)
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
			return runningError
		}
		return stateError
	}
	// Radio positions were built from the previous available-track set.
	// Invalidate their cursors until the next rebuild publishes new membership.
	if _, err := tx.ExecContext(ctx, "DELETE FROM app_metadata WHERE key = ?", app.RadioMembershipVersion); err != nil {
		return err
	}
	return tx.Commit()
}
