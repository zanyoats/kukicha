package jobs

import "context"

// Reset clears all library data and cached resources before running a full sync.
// The embedded Sync owns the shared job dependencies and rebuild pipeline.
type Reset struct {
	Sync
}

func (j *Reset) Run(ctx context.Context) error {
	return j.Sync.run(ctx, true)
}
