package jobs

import (
	"context"
)

type Scan struct{}

func (j *Scan) Run(ctx context.Context) error {
	return nil
}
