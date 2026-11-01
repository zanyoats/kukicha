package command

import (
	"context"
	"database/sql"
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
	"path/filepath"

	"github.com/zanyoats/kukicha/internal/app"
	"github.com/zanyoats/kukicha/internal/usecase/query"
)

var ErrEmptyJobsQueue = errors.New("empty jobs queue")
var ErrJobAlreadyPending = errors.New("A job of this type is already queued or running.")
var ErrJobNotCancelable = errors.New("Job can no longer be canceled because it has already ended")

//go:embed sql/insert_job.sql
var insertJobSQL string

//go:embed sql/claim_job.sql
var claimJobSQL string

//go:embed sql/end_job.sql
var endJobSQL string

//go:embed sql/request_cancel_job.sql
var requestCancelJobSQL string

//go:embed sql/recover_jobs.sql
var recoverJobsSQL string

type JobExecutor interface {
	ExecContext(context.Context, string, ...any) (sql.Result, error)
}

type JobEnder interface {
	QueryRowContext(context.Context, string, ...any) *sql.Row
}

type JobSubmission struct {
	Type    string
	Payload json.RawMessage
}

func InsertJob(
	ctx context.Context,
	db JobExecutor,
	job JobSubmission,
) (int64, error) {
	if err := ValidateJobSubmission(job); err != nil {
		return 0, err
	}

	result, err := db.ExecContext(
		ctx,
		insertJobSQL,
		sql.Named("type", job.Type),
		sql.Named("payload", string(job.Payload)),
	)
	if err != nil {
		return 0, fmt.Errorf("insert job: %w", err)
	}

	n, err := result.RowsAffected()
	if err != nil {
		return 0, fmt.Errorf("count inserted jobs: %w", err)
	}
	if n == 0 {
		return 0, ErrJobAlreadyPending
	}

	return result.LastInsertId()
}

func ClaimJob(ctx context.Context, db *sql.DB) (query.Job, error) {
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return query.Job{}, err
	}
	defer tx.Rollback()

	var id int64
	err = tx.QueryRowContext(ctx, claimJobSQL).Scan(&id)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return query.Job{}, ErrEmptyJobsQueue
		}
		return query.Job{}, fmt.Errorf("claim job: %w", err)
	}

	job, err := query.JobByID(ctx, tx, id)
	if err != nil {
		return query.Job{}, err
	}

	if err := tx.Commit(); err != nil {
		return query.Job{}, fmt.Errorf("commit job claim: %w", err)
	}

	return job, nil
}

func RequestJobCancellation(
	ctx context.Context,
	db *sql.DB,
	id int64,
) (query.Job, error) {
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return query.Job{}, err
	}
	defer tx.Rollback()

	result, err := tx.ExecContext(
		ctx, requestCancelJobSQL,
		sql.Named("job_id", id),
	)
	if err != nil {
		return query.Job{}, fmt.Errorf("request job cancellation: %w", err)
	}
	n, err := result.RowsAffected()
	if err != nil {
		return query.Job{}, err
	}

	job, err := query.JobByID(ctx, tx, id)
	if err != nil {
		return query.Job{}, err
	}

	if n != 1 && job.State != "canceled" {
		return query.Job{}, ErrJobNotCancelable
	}

	if err := tx.Commit(); err != nil {
		return query.Job{}, fmt.Errorf("commit job cancellation: %w", err)
	}

	return job, nil
}

func EndJob(
	ctx context.Context,
	db JobEnder,
	id, attempt int64,
	interrupted bool,
	runErr error,
) (string, error) {
	var failure sql.NullString
	if runErr != nil {
		failure = sql.NullString{
			String: runErr.Error(),
			Valid:  true,
		}
	}

	var state string
	err := db.QueryRowContext(
		ctx,
		endJobSQL,
		sql.Named("job_id", id),
		sql.Named("attempt", attempt),
		sql.Named("interrupted", interrupted),
		sql.Named("failure", failure),
	).Scan(&state)

	if err != nil {
		return "", fmt.Errorf("end job: %w", err)
	}

	return state, nil
}

// RecoverJobs resolves executions interrupted by a previous process.
// Call only at startup, before job processing begins, with one app instance.
func RecoverJobs(ctx context.Context, db JobExecutor) (int64, error) {
	result, err := db.ExecContext(ctx, recoverJobsSQL)
	if err != nil {
		return 0, fmt.Errorf("recover interrupted jobs: %w", err)
	}

	count, err := result.RowsAffected()
	if err != nil {
		return 0, fmt.Errorf("count recovered jobs: %w", err)
	}

	return count, nil
}

func ValidateJobSubmission(job JobSubmission) error {
	if !SupportedJobType(job.Type) {
		return fmt.Errorf("Unsupported job type %q", job.Type)
	}

	if !json.Valid(job.Payload) {
		return errors.New("Job payload must contain valid JSON")
	}

	if job.Type == "build" {
		var payload app.BuildJobPayload
		if err := json.Unmarshal(job.Payload, &payload); err != nil {
			return fmt.Errorf("invalid build job payload: %w", err)
		}
		switch payload.Mode {
		case app.BuildRescan:
			if payload.SourceType != "" || payload.Source != nil || payload.TempPath != "" || payload.RootID != "" || payload.RootLocation != "" {
				return errors.New("rescan must not include add source or destination fields")
			}
			return nil
		case app.BuildAdd:
		default:
			return errors.New("build mode must be add or rescan")
		}
		if payload.RootID == "" || payload.RootLocation == "" {
			return errors.New("build payload requires a destination root ID and original location")
		}
		switch payload.SourceType {
		case app.BuildSourceFiles:
			if !filepath.IsAbs(payload.TempPath) || filepath.Clean(payload.TempPath) != payload.TempPath {
				return errors.New("build payload requires a clean absolute temp directory path")
			}
			if payload.Source != nil {
				return errors.New("files mode must not include a sync source")
			}
		case app.BuildSourceSync:
			if payload.Source == nil || (payload.Source.Folder == nil) == (payload.Source.Remote == nil) {
				return errors.New("sync mode requires exactly one folder or remote source")
			}
			if remote := payload.Source.Remote; remote != nil {
				if err := remote.Normalize(); err != nil {
					return err
				}
			}
			if folder := payload.Source.Folder; folder != nil && (!filepath.IsAbs(folder.Path) || filepath.Clean(folder.Path) != folder.Path) {
				return errors.New("sync folder must be a clean absolute path")
			}
		default:
			return errors.New("add source type must be files or sync")
		}
	}
	return nil
}

func SupportedJobType(jobType string) bool {
	switch jobType {
	case "build":
		return true
	default:
		return false
	}
}
