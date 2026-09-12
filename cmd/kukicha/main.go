package main

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"os"

	_ "github.com/mattn/go-sqlite3"
	"github.com/zanyoats/kukicha/internal/app"
	"github.com/zanyoats/kukicha/internal/scan"
	"github.com/zanyoats/kukicha/internal/usecase/command"
	"github.com/zanyoats/kukicha/internal/web"
)

func main() {
	if err := run(); err != nil {
		slog.Error("kukicha stopped", "error", err)
		os.Exit(1)
	}
}

func run() error {
	catalog, err := app.LoadTaxonomy()
	if err != nil {
		return err
	}

	conf, err := app.LoadAppConfig(catalog)
	if err != nil {
		return err
	}
	slog.SetDefault(slog.New(slog.NewTextHandler(
		os.Stderr,
		&slog.HandlerOptions{Level: conf.Settings.Logging.Level.Level},
	)))

	renderer, err := web.NewPageRenderer()
	if err != nil {
		return err
	}

	ctx := context.Background()

	db, err := sql.Open("sqlite3", conf.DataSourceName)
	if err != nil {
		return err
	}
	defer db.Close()

	db.SetMaxOpenConns(1)

	if _, err = db.ExecContext(ctx, `PRAGMA journal_mode = WAL`); err != nil {
		return err
	}

	if err = command.Migrate(ctx, db); err != nil {
		return err
	}

	if err = command.SeedTaxonomy(ctx, db, catalog, scan.UnknownItem); err != nil {
		return err
	}

	if err = command.InitializeInstanceMetadata(
		ctx,
		db,
	); err != nil {
		return err
	}

	result, err := scan.Sync(ctx, db, conf)
	if err != nil {
		return err
	}

	// TextHandler escapes newlines, so print this human-readable summary directly.
	if slog.Default().Enabled(ctx, slog.LevelInfo) {
		fmt.Fprintf(os.Stderr,
			"library sync complete\n"+
				"\tscan summary:\n"+
				"\t\tprocessed_files=%d\n"+
				"\t\troots=%d\n"+
				"\t\talbums=%d\n"+
				"\t\ttracks=%d\n"+
				"\t\tplaylists=%d\n"+
				"\t\tsidecar_files=%d\n"+
				"\t\talbum_sidecars=%d\n"+
				"\t\tunassociated_sidecars=%d\n"+
				"\t\ttracks_with_unknown_titles=%d\n"+
				"\t\talbums_with_unknown_names=%d\n"+
				"\t\talbums_with_unknown_artists=%d\n"+
				"\tlog summary:\n"+
				"\t\terrors=%d\n"+
				"\t\twarnings=%d\n"+
				"\t\tinfo=%d\n",
			result.ScanStats.ProcessedFiles,
			result.ScanStats.LibraryRoots,
			result.ScanStats.LibraryAlbums,
			result.ScanStats.LibraryTracks,
			result.ScanStats.LibraryPlaylists,
			result.ScanStats.SidecarFiles,
			result.ScanStats.LibraryAlbumSidecars,
			result.ScanStats.UnassociatedSidecars,
			result.ScanStats.LibraryTrackTitleUnknowns,
			result.ScanStats.LibraryAlbumUnknowns,
			result.ScanStats.LibraryAlbumArtistUnknowns,
			result.LogStats.ErrorCount,
			result.LogStats.WarnCount,
			result.LogStats.InfoCount,
		)
	}

	server := &http.Server{
		Addr: conf.Settings.Server.ServerAddress,
		Handler: web.NewWebRouter(
			conf.Settings.Server.AppBasePath,
			renderer,
			db,
			conf.Settings.UI.PerPage,
			conf.Settings.UI.DefaultAlbumSort,
		),
		ReadHeaderTimeout: conf.Settings.Server.ReadHeaderTimeout.Duration,
	}

	slog.Info(
		"web server listening",
		"url", fmt.Sprintf(
			"http://%s",
			conf.Settings.Server.ServerAddress,
		),
	)

	if err = server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		return err
	}

	return nil
}
