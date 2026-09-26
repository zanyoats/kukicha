package app

import "context"

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
