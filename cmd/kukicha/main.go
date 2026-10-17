package main

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"os"
	"os/signal"
	"syscall"

	_ "github.com/mattn/go-sqlite3"
	"github.com/zanyoats/kukicha/internal/app"
	"github.com/zanyoats/kukicha/internal/jobs"
	proc "github.com/zanyoats/kukicha/internal/processor"
	"github.com/zanyoats/kukicha/internal/scan"
	"github.com/zanyoats/kukicha/internal/usecase/command"
	"github.com/zanyoats/kukicha/internal/usecase/query"
	"github.com/zanyoats/kukicha/internal/web"
)

func main() {
	if err := run(); err != nil {
		slog.Error("kukicha stopped with error", "error", err)
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

	renderer, err := web.NewPageRenderer(conf.Settings.UI.ToastMinDuration.Duration)
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

	recovered, err := command.RecoverJobs(ctx, db)
	if err != nil {
		return err
	}

	if recovered > 0 {
		slog.Info(
			"recovered interrupted jobs",
			"count", recovered,
		)
	}

	notifications := new(app.JobNotifications)
	processor, err := proc.New(
		db,
		registry(db, conf, notifications),
		command.NewJobLogger(db, slog.Default()),
		notifications,
		conf.Settings.Jobs.PollInterval.Duration,
		conf.Settings.Jobs.FinalizeTimeout.Duration,
	)
	if err != nil {
		return err
	}

	rootsMatch, err := query.RootsMatchConfig(ctx, db, conf.Roots)
	if err != nil {
		return err
	}
	if !rootsMatch {
		id, err := processor.Submit(ctx, proc.JobSubmission{Type: "sync", Payload: []byte(`{}`)})
		if err != nil && !errors.Is(err, command.ErrJobAlreadyPending) {
			return fmt.Errorf("enqueue sync for changed roots: %w", err)
		}
		if err == nil {
			slog.Info("configured roots changed; queued library sync", "job_id", id)
		} else {
			slog.Info("configured roots changed; using pending library sync")
		}
	}

	ctx, stop := signal.NotifyContext(
		context.Background(), os.Interrupt, syscall.SIGTERM,
	)
	defer stop()

	server := &http.Server{
		// alert long-running SSE handlers if parent `ctx` is interrupted,
		// otherwise server. Shutdown will never succeed on its own.
		BaseContext: func(net.Listener) context.Context { return ctx },
		Addr:        conf.Settings.Server.ServerAddress,
		Handler: web.NewWebRouter(
			conf.Settings.Server.AppBasePath,
			renderer,
			db,
			conf.Settings.UI.PerPage,
			conf.Settings.UI.DefaultAlbumSort,
			processor,
			notifications,
			conf.Roots,
			conf.ConfigFilePath,
			scan.RecognizedAudioExtensions,
		),
		ReadHeaderTimeout: conf.Settings.Server.ReadHeaderTimeout.Duration,
	}

	done := make(chan error, 2)

	go func() {
		slog.Info("starting background job processor")
		err := processor.Run(ctx)
		slog.Info("processor stopped")
		if errors.Is(err, context.Canceled) {
			err = nil
		}
		done <- err
		stop()
	}()
	go func() {
		slog.Info(
			"starting http",
			"url", fmt.Sprintf(
				"http://%s",
				conf.Settings.Server.ServerAddress,
			),
		)
		err := server.ListenAndServe()
		if errors.Is(err, http.ErrServerClosed) {
			err = nil
		}
		done <- err
		stop()
	}()

	// A signal or either service exiting starts shutdown
	<-ctx.Done()
	slog.Info("shutdown requested")
	stop()

	shutdownCtx, cancel := context.WithTimeout(
		context.Background(),
		conf.Settings.Server.ShutdownTimeout.Duration,
	)
	defer cancel()

	shutdownErr := server.Shutdown(shutdownCtx)
	if shutdownErr != nil {
		shutdownErr = errors.Join(shutdownErr, server.Close())
	}
	slog.Info("http stopped")

	// Wait for both sevices to end
	processorOrHTTPErr := <-done
	otherErr := <-done

	return errors.Join(shutdownErr, processorOrHTTPErr, otherErr)
}

func registry(db *sql.DB, conf *app.AppConfig, notifications jobs.NotificationPublisher) proc.Registry {
	return proc.Registry{
		"sync": proc.Decode(func(struct{}) (proc.Job, error) {
			return &jobs.Sync{DB: db, Conf: conf, Notifications: notifications}, nil
		}),
		"reset": proc.Decode(func(struct{}) (proc.Job, error) {
			return &jobs.Reset{Sync: jobs.Sync{DB: db, Conf: conf, Notifications: notifications}}, nil
		}),
		"scan": proc.Decode(func(struct{}) (proc.Job, error) { return &jobs.Scan{DB: db, Conf: conf}, nil }),
	}
}
