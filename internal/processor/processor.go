package processor

import (
	"context"
	"database/sql"
	"errors"
	"log/slog"
	"sync/atomic"
	"time"
)

type Processor struct {
	db           *sql.DB
	logger       *slog.Logger
	pollInterval time.Duration
	started      atomic.Bool
}

var ErrStopping = errors.New("job processor is stopping")

func New(
	db *sql.DB,
	logger *slog.Logger,
	pollInterval time.Duration,
) (*Processor, error) {
	if db == nil {
		return nil, errors.New("processor requires a database")
	}
	if pollInterval <= 0 {
		return nil, errors.New("processor poll interval must be positive")
	}
	if logger == nil {
		logger = slog.Default()
	}

	return &Processor{
		db:           db,
		logger:       logger,
		pollInterval: pollInterval,
	}, nil
}

func (p *Processor) Run(ctx context.Context) error {
	// CompareAndSwap makes the check and update a single atomic operation,
	// so only one caller succeeds.
	if !p.started.CompareAndSwap(false, true) {
		return errors.New("job processor Run has already been called")
	}

	for {
		if err := ctx.Err(); err != nil {
			return err
		}

		p.logger.Info("claiming a job ...")

		if err := p.wait(ctx); err != nil {
			return err
		}
	}
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
