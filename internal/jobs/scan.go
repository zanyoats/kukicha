package jobs

import (
	"context"
	"database/sql"
	"errors"
	"log/slog"

	"github.com/zanyoats/kukicha/internal/app"
	"github.com/zanyoats/kukicha/internal/scan"
	"github.com/zanyoats/kukicha/internal/usecase/command"
	"github.com/zanyoats/kukicha/internal/usecase/query"
)

// Scan rebuilds the current library from saved metadata, applying updated
// normalization rules without reading external files.
type Scan struct {
	DB     *sql.DB
	Conf   *app.AppConfig
	logger *slog.Logger
}

func (j *Scan) Run(ctx context.Context) error {
	j.logger = app.JobLogger(ctx)
	if _, ok := app.JobIDFromContext(ctx); !ok {
		return errors.New("scan requires a job ID in context")
	}
	matches, err := query.RootsMatchConfig(ctx, j.DB, j.Conf.Roots)
	if err != nil {
		return err
	}
	if !matches {
		return errors.New("library roots differ from configuration; run sync instead")
	}
	conn, err := j.DB.Conn(ctx)
	if err != nil {
		return err
	}
	defer conn.Close()
	ctx = command.WithJobLogDB(ctx, conn)

	if err := command.PrepareScanResultsFromLibrary(ctx, conn, j.Conf, func(file *app.ScannedFile) error {
		return scan.ApplyMetadataNormalizations(ctx, j.Conf, conn, file)
	}); err != nil {
		return err
	}
	result, err := command.BuildLibraryFromScan(
		ctx, conn, command.BuildFromSavedMetadata, scan.UnknownItem,
		j.Conf.Settings.Scan.SecondaryGenreSplitTokens,
		scan.RecognizedAudioExtensions,
		scan.RecognizedPlaylistExtensions,
		scan.RecognizedSidecarExtensions,
	)
	if err != nil {
		return err
	}
	j.logger.InfoContext(ctx, "library scan complete",
		"roots", result.ScanStats.LibraryRoots,
		"albums", result.ScanStats.LibraryAlbums,
		"tracks", result.ScanStats.LibraryTracks,
		"errors", result.LogStats.Errors,
		"warnings", result.LogStats.Warnings,
		"info", result.LogStats.Info,
		"debug", result.LogStats.Debug,
	)
	return nil
}
