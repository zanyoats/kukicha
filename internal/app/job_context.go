package app

import (
	"context"
	"log/slog"
)

type jobIDContextKey struct{}

// WithJobID carries the stable job ID across execution attempts.
func WithJobID(ctx context.Context, id int64) context.Context {
	return context.WithValue(ctx, jobIDContextKey{}, id)
}

// JobIDFromContext reports whether a valid processor job ID is present.
func JobIDFromContext(ctx context.Context) (int64, bool) {
	id, ok := ctx.Value(jobIDContextKey{}).(int64)
	return id, ok && id > 0
}

type jobLoggerContextKey struct{}

// WithJobLogger makes the processor's logger available to jobs and their helpers.
func WithJobLogger(ctx context.Context, logger *slog.Logger) context.Context {
	return context.WithValue(ctx, jobLoggerContextKey{}, logger)
}

// JobLogger returns the job-scoped logger, or the default outside a processor job.
// Use its context-aware methods to retain job identity and the logging connection.
func JobLogger(ctx context.Context) *slog.Logger {
	if logger, ok := ctx.Value(jobLoggerContextKey{}).(*slog.Logger); ok && logger != nil {
		return logger
	}
	return slog.Default()
}
