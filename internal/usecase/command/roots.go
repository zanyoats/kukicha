package command

import (
	"context"
	"database/sql"
	"fmt"
	"log/slog"

	"github.com/zanyoats/kukicha/internal/app"
)

// ReconcileRoots runs before job processing. New roots are validated and initialized; existing
// configured roots must still carry their marker. Missing roots retain their data.
// Unchanged roots leave radio membership and its version untouched.
func ReconcileRoots(ctx context.Context, db *sql.DB, configured []app.Root, validateNew, checkMarker func(context.Context, app.Root) error) error {
	rows, err := db.QueryContext(ctx, "SELECT config_id, state FROM library_roots")
	if err != nil {
		return err
	}
	stored := make(map[string]string)
	for rows.Next() {
		var id, state string
		if err := rows.Scan(&id, &state); err != nil {
			rows.Close()
			return err
		}
		stored[id] = state
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return err
	}
	type rootChange struct {
		id, state string
		insert    bool
	}
	var changes []rootChange
	present := make(map[string]bool, len(configured))
	// Marker checks may involve remote storage; do them before opening a write transaction.
	for _, root := range configured {
		id := root.ConfigID()
		present[id] = true
		previous, exists := stored[id]
		if previous == "pending_removal" {
			return fmt.Errorf("root %q is pending removal; remove it from the configuration before restarting", id)
		}
		state := "available"
		if exists {
			if err := checkMarker(ctx, root); err != nil {
				if ctx.Err() != nil {
					return ctx.Err()
				}
				state = "disowned"
				slog.WarnContext(ctx, "root marker check failed", "root", root.ConfigID(), "error", err)
			}
		}
		if !exists {
			if err := validateNew(ctx, root); err != nil {
				return fmt.Errorf("validate new root %q: %w", id, err)
			}
		}
		if !exists || previous != state {
			changes = append(changes, rootChange{id: id, state: state, insert: !exists})
		}
	}
	for id, state := range stored {
		if !present[id] && state != "lost" && state != "pending_removal" {
			changes = append(changes, rootChange{id: id, state: "lost"})
		}
	}
	if len(changes) == 0 {
		if err := ctx.Err(); err != nil {
			return err
		}
		slog.InfoContext(ctx, "root reconciliation complete: no changes")
		return nil
	}
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	for _, change := range changes {
		if change.insert {
			if _, err := tx.ExecContext(ctx, "INSERT INTO library_root_ids(config_id) VALUES (?) ON CONFLICT(config_id) DO NOTHING", change.id); err != nil {
				return err
			}
			if _, err := tx.ExecContext(ctx, `INSERT INTO library_roots(id, config_id, state)
				SELECT id, config_id, ? FROM library_root_ids WHERE config_id = ?`, change.state, change.id); err != nil {
				return fmt.Errorf("insert root %q: %w", change.id, err)
			}
		} else if _, err := tx.ExecContext(ctx, "UPDATE library_roots SET state = ? WHERE config_id = ?", change.state, change.id); err != nil {
			return fmt.Errorf("update root %q: %w", change.id, err)
		}
	}
	rescan := false
	for _, change := range changes {
		if !change.insert {
			rescan = true
			break
		}
	}
	var jobID int64
	if rescan {
		// Invalidate stale players and queue a fresh rescan in the same transaction.
		// A pending add or recovered, already-published build must not suppress it.
		if _, err := tx.ExecContext(ctx, "DELETE FROM app_metadata WHERE key = ?", app.RadioMembershipVersion); err != nil {
			return err
		}
		result, err := tx.ExecContext(ctx, `INSERT INTO jobs(type, payload) VALUES ('build', '{"mode":"rescan"}')`)
		if err != nil {
			return fmt.Errorf("queue library rescan: %w", err)
		}
		jobID, err = result.LastInsertId()
		if err != nil {
			return err
		}
	}
	if err := tx.Commit(); err != nil {
		return err
	}
	// Only report persisted changes after the transaction commits.
	for _, change := range changes {
		if change.insert {
			slog.InfoContext(ctx, "root inserted", "root", change.id, "state", change.state)
		} else {
			slog.InfoContext(ctx, "root state changed", "root", change.id, "previous_state", stored[change.id], "state", change.state)
		}
	}
	if rescan {
		slog.InfoContext(ctx, "library rescan queued", "job_id", jobID)
	}
	return nil
}
