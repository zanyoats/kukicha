package processor

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"sync"
	"sync/atomic"
	"time"

	"github.com/zanyoats/kukicha/internal/app"
	"github.com/zanyoats/kukicha/internal/usecase/command"
	"github.com/zanyoats/kukicha/internal/usecase/query"
)

type Processor struct {
	db              *sql.DB
	registry        Registry
	logger          *slog.Logger
	pollInterval    time.Duration
	finalizeTimeout time.Duration
	started         atomic.Bool

	activeMu     sync.Mutex
	activeID     int64
	activeCancel context.CancelFunc
}

type Job interface {
	Run(context.Context) error
}

type Factory func(json.RawMessage) (Job, error)

type Registry map[string]Factory

type JobSubmission struct {
	Type    string
	Payload json.RawMessage
}

var ErrStopping = errors.New("job processor is stopping")

// Decode creates a factory for each job type.
//
// A factory takes a job's payload (from db column `jobs.payload`)
// and attempts to decode into a type T, which represents values that are
// the job's input.
//
// Use struct{} as T for jobs accepting only an empty JSON object.
func Decode[T any](build func(T) (Job, error)) Factory {
	return func(payload json.RawMessage) (Job, error) {
		if bytes.Equal(bytes.TrimSpace(payload), []byte("null")) {
			return nil, errors.New("job payload must not be null")
		}

		decoder := json.NewDecoder(bytes.NewReader(payload))
		decoder.DisallowUnknownFields()

		var value T
		if err := decoder.Decode(&value); err != nil {
			return nil, fmt.Errorf("decode job payload: %w", err)
		}

		var trailing any
		if err := decoder.Decode(&trailing); !errors.Is(err, io.EOF) {
			if err == nil {
				return nil, errors.New("job payload contains trailing JSON")
			}
			return nil, fmt.Errorf("decode trailing job payload: %w", err)
		}

		job, err := build(value)
		if err != nil {
			return nil, err
		}
		if job == nil {
			return nil, errors.New("job factory returned nil")
		}
		return job, nil
	}
}

func New(
	db *sql.DB,
	registry Registry,
	logger *slog.Logger,
	pollInterval time.Duration,
	finalizeTimeout time.Duration,
) (*Processor, error) {
	if db == nil {
		return nil, errors.New("processor requires a database")
	}
	if pollInterval <= 0 {
		return nil, errors.New("poll interval must be positive")
	}
	if finalizeTimeout <= 0 {
		return nil, errors.New("finalize timeout must be positive")
	}
	if logger == nil {
		logger = slog.Default()
	}

	factories := make(Registry, len(registry))
	for jobType, factory := range registry {
		if !command.SupportedJobType(jobType) {
			return nil, fmt.Errorf("unsupported job type %q", jobType)
		}
		if factory == nil {
			return nil, fmt.Errorf("nil factory for job type %q", jobType)
		}
		factories[jobType] = factory
	}

	return &Processor{
		db:              db,
		registry:        factories,
		logger:          logger,
		pollInterval:    pollInterval,
		finalizeTimeout: finalizeTimeout,
	}, nil
}

func (p *Processor) Run(ctx context.Context) error {
	// CompareAndSwap makes the check and update a single atomic operation,
	// so only one processor runs (per instance).
	if !p.started.CompareAndSwap(false, true) {
		return errors.New("job processor Run has already been called")
	}

	for {
		if err := ctx.Err(); err != nil {
			return err
		}

		record, err := command.ClaimJob(ctx, p.db)
		if err != nil {
			if errors.Is(err, command.ErrEmptyJobsQueue) {
				if err := p.wait(ctx); err != nil {
					return err
				}
				continue
			}

			return fmt.Errorf("claim next job: %w", err)
		}

		if record.State != "running" {
			return fmt.Errorf(
				"run job id=%d state: wanted %q, got %q",
				record.ID, "running", record.State,
			)
		}

		job, runErr := p.construct(record)
		if runErr == nil {
			runErr = p.execute(ctx, record, job)
		}

		if err := p.end(ctx, record, runErr); err != nil {
			return err
		}
	}
}

func (p *Processor) Submit(
	ctx context.Context,
	submission JobSubmission,
) (int64, error) {
	id, err := command.InsertJob(ctx, p.db, command.JobSubmission(submission))
	if err != nil {
		return 0, err
	}

	p.logger.Info(
		"job processor: submission",
		"job_id", id,
		"type", submission.Type,
	)

	return id, nil
}

func (p *Processor) Cancel(ctx context.Context, id int64) (query.Job, error) {
	record, err := command.RequestJobCancellation(ctx, p.db, id)
	if err != nil {
		return query.Job{}, err
	}
	// If the job is queued, we can just cancel it. Otherwise
	// when in running state, we have a active cancel context we
	// can use to cancel the job, but only if the job ids match.
	if record.State == "running" {
		p.activeMu.Lock()
		if p.activeID == id {
			if p.activeCancel == nil {
				return query.Job{}, fmt.Errorf(
					"bug: job id=%d type=%q has not active cancel",
					record.ID, record.Type,
				)
			}
			p.activeCancel()
		}
		p.activeMu.Unlock()
	}

	p.logger.Info(
		"job processor: request cancellation",
		"job_id", record.ID,
		"type", record.Type,
		"state", record.State,
	)

	return record, nil
}

func (p *Processor) wait(ctx context.Context) error {
	timer := time.NewTimer(p.pollInterval)
	defer timer.Stop()

	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}

func (p *Processor) construct(record query.Job) (Job, error) {
	var job Job
	factory, ok := p.registry[record.Type]
	if !ok {
		return nil, fmt.Errorf(
			"construct job id=%d has no factory for registered type %q",
			record.ID, record.Type,
		)
	}
	if !json.Valid(record.Payload) {
		return nil, fmt.Errorf(
			"construct job id=%d payload is invalid JSON",
			record.ID,
		)
	}

	job, err := factory(record.Payload)
	if err != nil {
		return nil, fmt.Errorf(
			"construct job id=%d: %w",
			record.ID, err,
		)
	}
	if job == nil {
		return nil, fmt.Errorf(
			"construct job id=%d: %w",
			record.ID, errors.New("job factory returned nil"),
		)
	}

	return job, nil
}

func (p *Processor) execute(
	parent context.Context,
	record query.Job,
	job Job,
) error {
	runCtx, cancel := context.WithCancel(parent)

	p.activeMu.Lock()
	p.activeID = record.ID
	p.activeCancel = cancel
	p.activeMu.Unlock()

	defer func() {
		p.activeMu.Lock()
		p.activeID = 0
		p.activeCancel = nil
		p.activeMu.Unlock()
		cancel()
	}()

	// Begin Edge Case
	//
	// Cancellation may have been requested after the job was claimed
	// but before activeCancel was registered. Re-read the job to catch
	// requests that could not cancel the running context and return canceled
	// explicitly in that case.
	current, err := query.JobByID(parent, p.db, record.ID)
	if err != nil {
		return fmt.Errorf("check job cancellation: %w", err)
	}
	// Honor the fact the job was requested to be canceled by the
	// fact it has a non-nil cancel_requested_at value.
	if current.CancelRequestedAt.Valid {
		return context.Canceled
	}
	// End Edge Case

	p.logger.Info(
		"job processor: starting",
		"job_id", record.ID,
		"type", record.Type,
		"attempt", record.Attempts,
	)

	return job.Run(app.WithJobID(runCtx, record.ID))
}

// end records the job's final state in this order:
//  1. User requested cancellation       -> canceled.
//  2. Parent context ended and the
//     job returned context.Canceled or
//     context.DeadlineExceeded          -> requeued.
//  3. Job returned another error        -> failed.
//  4. Job returned nil, even if parent
//     was interrupted                   -> succeeded.
func (p *Processor) end(
	parent context.Context,
	record query.Job,
	runErr error,
) error {
	ctx, cancel := context.WithTimeout(
		context.WithoutCancel(parent),
		p.finalizeTimeout,
	)
	defer cancel()

	interrupted := parent.Err() != nil &&
		(errors.Is(runErr, context.Canceled) ||
			errors.Is(runErr, context.DeadlineExceeded))

	finalState, err := command.EndJob(
		ctx,
		p.db,
		record.ID,
		record.Attempts,
		interrupted,
		runErr,
	)
	if err != nil {
		return fmt.Errorf("end job id=%d: %w", record.ID, err)
	}
	p.logger.Info(
		"job processor: completed",
		"job_id", record.ID,
		"state", finalState,
		"interrupted", interrupted,
		"runErr", runErr,
	)
	return nil
}
