package jobs

import (
	"context"
)

type Sync struct{}

func (j *Sync) Run(ctx context.Context) error {
	return nil
}
