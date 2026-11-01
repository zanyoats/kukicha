package command

import (
	"context"
	"database/sql"
	_ "embed"
	"fmt"

	"github.com/zanyoats/kukicha/internal/app"
)

//go:embed sql/reset_resources.sql
var resetResourcesSQL string

// ResetLibrary clears persisted library state and generation markers while
// keeping taxonomy, other app metadata, and the running reset job.
func ResetLibrary(ctx context.Context, conn *sql.Conn, jobID int64) error {
	tx, err := conn.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()

	var active bool
	if err := tx.QueryRowContext(ctx, `
		SELECT EXISTS (SELECT 1 FROM jobs
		WHERE id = ? AND type = 'reset' AND state = 'running')`, jobID).Scan(&active); err != nil {
		return err
	}
	if !active {
		return fmt.Errorf("reset requires a running reset job: %d", jobID)
	}
	if err := resetLibrary(ctx, tx); err != nil {
		return err
	}
	if _, err := tx.ExecContext(ctx, resetResourcesSQL,
		sql.Named("job_id", jobID),
		sql.Named("generated_at_key", app.LibraryGeneratedAt),
		sql.Named("generated_job_id_key", app.LibraryGeneratedJobId),
		sql.Named("radio_membership_version_key", app.RadioMembershipVersion),
	); err != nil {
		return fmt.Errorf("reset library resources: %w", err)
	}
	return tx.Commit()
}
