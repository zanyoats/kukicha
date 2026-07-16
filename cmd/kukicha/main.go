package main

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"log"
	"net/http"
	"os"
	"time"

	_ "github.com/mattn/go-sqlite3"
	"github.com/zanyoats/kukicha/internal/app"
	"github.com/zanyoats/kukicha/internal/scan"
	"github.com/zanyoats/kukicha/internal/usecase/command"
	"github.com/zanyoats/kukicha/internal/web"
)

func main() {
	catalog := app.MustLoadTaxonomy()

	appConfig, err := app.LoadAppConfig(catalog)
	if err != nil {
		fail(err)
	}

	ctx := context.Background()
	dsn := fmt.Sprintf("%s?%s", appConfig.DbPath, appConfig.DbPragmas)

	writerDb, err := sql.Open("sqlite3", dsn)
	if err != nil {
		fail(err)
	}
	defer writerDb.Close()

	writerDb.SetMaxOpenConns(1)

	if _, err = writerDb.ExecContext(ctx, `PRAGMA journal_mode = WAL`); err != nil {
		fail(err)
	}

	if err = command.Migrate(ctx, writerDb); err != nil {
		fail(err)
	}

	if err = command.SeedTaxonomy(ctx, writerDb, catalog, scan.UnknownItem); err != nil {
		fail(err)
	}

	result, err := scan.Sync(ctx, writerDb, appConfig)
	if err != nil {
		fail(err)
	}

	log.Printf("Library sync complete")

	for _, genreResolution := range result.GenreResolutionResult {
		log.Printf("%s: %q (%s)",
			genreResolution.LogMessage, genreResolution.Path, genreResolution.Status)
	}

	server := &http.Server{
		Addr:              appConfig.Settings.HTTP.ServerAddress,
		Handler:           web.NewHandler(appConfig.Settings.HTTP.AppBasePath),
		ReadHeaderTimeout: 5 * time.Second,
	}

	log.Printf(
		"Kukicha web server listening at http://%s%s",
		appConfig.Settings.HTTP.ServerAddress,
		appConfig.Settings.HTTP.AppBasePath,
	)

	if err = server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		fail(err)
	}
}

func fail(err error) {
	fmt.Fprintln(os.Stderr, err)
	os.Exit(1)
}
