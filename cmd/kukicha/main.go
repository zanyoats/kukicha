package main

import (
	"context"
	"database/sql"
	"errors"
	"log"
	"net/http"
	"time"

	_ "github.com/mattn/go-sqlite3"
	"github.com/zanyoats/kukicha/internal/app"
	"github.com/zanyoats/kukicha/internal/scan"
	"github.com/zanyoats/kukicha/internal/usecase/command"
	"github.com/zanyoats/kukicha/internal/web"
)

func main() {
	catalog, err := app.LoadTaxonomy()
	if err != nil {
		log.Fatal(err)
	}

	conf, err := app.LoadAppConfig(catalog)
	if err != nil {
		log.Fatal(err)
	}

	ctx := context.Background()

	db, err := sql.Open("sqlite3", conf.DataSourceName)
	if err != nil {
		log.Fatal(err)
	}
	defer db.Close()

	db.SetMaxOpenConns(1)

	if _, err = db.ExecContext(ctx, `PRAGMA journal_mode = WAL`); err != nil {
		log.Fatal(err)
	}

	if err = command.Migrate(ctx, db); err != nil {
		log.Fatal(err)
	}

	if err = command.SeedTaxonomy(ctx, db, catalog, scan.UnknownItem); err != nil {
		log.Fatal(err)
	}
		fail(err)
	}

	result, err := scan.Sync(ctx, db, conf)
	if err != nil {
		log.Fatal(err)
	}

	log.Printf("Library sync complete")

	for _, genreResolution := range result.GenreResolutionResult {
		log.Printf("%s: %q (%s)",
			genreResolution.LogMessage, genreResolution.Path, genreResolution.Status)
	}

	server := &http.Server{
		Addr: conf.Settings.HTTP.ServerAddress,
		Handler: web.NewHandler(
			conf.Settings.HTTP.AppBasePath,
			db,
		),
		ReadHeaderTimeout: 5 * time.Second,
	}

	log.Printf(
		"Kukicha web server is listening at http://%s%s",
		conf.Settings.HTTP.ServerAddress,
		conf.Settings.HTTP.AppBasePath,
	)

	if err = server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		log.Fatal(err)
	}
}
