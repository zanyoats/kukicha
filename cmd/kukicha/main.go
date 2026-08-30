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

	slog.Info("library sync complete")

	for _, genreResolution := range result.GenreResolutionResult {
		slog.Warn(
			genreResolution.LogMessage,
			"path", genreResolution.Path,
			"status", genreResolution.Status,
		)
	}

	server := &http.Server{
		Addr: conf.Settings.Server.ServerAddress,
		Handler: web.NewWebRouter(
			conf.Settings.Server.AppBasePath,
			renderer,
			db,
		),
		ReadHeaderTimeout: conf.Settings.Server.ReadHeaderTimeout.Duration,
	}

	slog.Info(
		"web server listening",
		"url", fmt.Sprintf(
			"http://%s%s",
			conf.Settings.Server.ServerAddress,
			conf.Settings.Server.AppBasePath,
		),
	)

	if err = server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		return err
	}

	return nil
}
