package main

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"

	_ "github.com/mattn/go-sqlite3"
	"github.com/zanyoats/kukicha/internal/app"
	proc "github.com/zanyoats/kukicha/internal/processor"
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

	db.SetMaxOpenConns(2)
	db.SetMaxIdleConns(2)

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

	processor, err := proc.New(
		db, slog.Default(), conf.Settings.Jobs.PollInterval.Duration,
	)
	if err != nil {
		return err
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

	runCtx, stop := signal.NotifyContext(
		context.Background(), os.Interrupt, syscall.SIGTERM,
	)
	defer stop()

	done := make(chan error, 2)
	go func() {
		err := processor.Run(runCtx)
		slog.Info("processor stopped")
		done <- err
	}()
	go func() { done <- server.ListenAndServe() }()

	var serviceErr error
	remaining := 2

	select {
	case <-runCtx.Done():
	case serviceErr = <-done:
		remaining--
	}

	// stop will cancel the processor, for example if the http server errored early
	slog.Info("processor shutdown requested")
	stop()

	shutdownCtx, cancel := context.WithTimeout(
		context.Background(), conf.Settings.Server.ShutdownTimeout.Duration,
	)
	defer cancel()

	slog.Info("http server shutting down")
	err = server.Shutdown(shutdownCtx)
	if err != nil {
		serviceErr = errors.Join(serviceErr, err)
		_ = server.Close()
	}
	slog.Info("http server stopped", "error", err)

	for ; remaining > 0; remaining-- {
		err := <-done
		if err != nil &&
			!errors.Is(err, context.Canceled) &&
			!errors.Is(err, http.ErrServerClosed) {
			serviceErr = errors.Join(serviceErr, err)
		}
	}

	if errors.Is(serviceErr, context.Canceled) ||
		errors.Is(serviceErr, http.ErrServerClosed) {
		return nil
	}

	return serviceErr
}
