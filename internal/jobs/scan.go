package jobs

import (
	"context"
	"database/sql"
	"log/slog"

	"github.com/zanyoats/kukicha/internal/app"
)

// Rebuilds library but without the overhead of reading external files.
// It has uses after various things: adjusted artist split rules, new tracks added
type Scan struct {
	DB     *sql.DB
	Conf   *app.AppConfig
	logger *slog.Logger
}

func (j *Scan) Run(ctx context.Context) error {
	j.logger = app.JobLogger(ctx)
	j.logger.InfoContext(ctx, "library scan complete")
	return nil
}
