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
	"path/filepath"
	"syscall"

	_ "github.com/mattn/go-sqlite3"
	"github.com/zanyoats/kukicha/internal/app"
	"github.com/zanyoats/kukicha/internal/auth"
	"github.com/zanyoats/kukicha/internal/buildinfo"
	"github.com/zanyoats/kukicha/internal/jobs"
	proc "github.com/zanyoats/kukicha/internal/processor"
	"github.com/zanyoats/kukicha/internal/scan"
	"github.com/zanyoats/kukicha/internal/upload"
	"github.com/zanyoats/kukicha/internal/usecase/command"
	"github.com/zanyoats/kukicha/internal/web"
)

func main() {
	if len(os.Args) > 1 {
		printHelp()
		return
	}
	if err := run(); err != nil {
		slog.Error("kukicha stopped with error", "error", err)
		os.Exit(1)
	}
}

func printHelp() {
	fmt.Printf("Welcome to Kukicha (%s)!\n\n", buildinfo.Version())
	fmt.Println("To start the app, run kukicha without any flags.\nUse -h, --help, or any argument to show this message.")
	fmt.Printf(`
Required environment variables
  KUKICHA_PASSPHRASE   Choose a passphrase to sign in to Kukicha (up to %d bytes).
  KUKICHA_JWT_SECRET   A private key Kukicha uses to sign login sessions.
                       Generate it once with: openssl rand -base64 32
                       Keep the same key across restarts to preserve sessions.

Set both variables in the environment that starts Kukicha. For example, in a shell:
  export KUKICHA_PASSPHRASE='replace with your own passphrase'
  export KUKICHA_JWT_SECRET='paste the generated key here'
`, auth.MaxPassphraseBytes)

	dir, err := os.UserConfigDir()
	if err != nil {
		fmt.Printf("Could not find your app directory: %v\n", err)
		return
	}
	appDir := filepath.Join(dir, app.AppName)
	configPath := filepath.Join(appDir, app.ConfigFileName)
	fmt.Printf("\nYour app directory is %s\n", appDir)
	switch _, err := os.Stat(configPath); {
	case errors.Is(err, os.ErrNotExist):
		fmt.Printf("You can create your config file at %s\n", configPath)
	case err != nil:
		fmt.Printf("Could not check your config file: %v\n", err)
	default:
		fmt.Printf("Your config file is at %s\n", configPath)
		dbPath, err := app.ConfiguredDatabasePath(appDir, configPath)
		if err != nil {
			fmt.Printf("Could not determine the database location: %v\n", err)
			return
		}
		fmt.Printf("Database location: %s\n", dbPath)
	}
}

func run() error {
	key, err := auth.ParseJWTSecret(os.Getenv("KUKICHA_JWT_SECRET"))
	if err != nil {
		return fmt.Errorf("%w (run kukicha --help for setup instructions)", err)
	}

	catalog, err := app.LoadTaxonomy()
	if err != nil {
		return err
	}

	conf, err := app.LoadAppConfig(catalog)
	if err != nil {
		return err
	}
	credentials, err := auth.New(os.Getenv("KUKICHA_PASSPHRASE"), key, conf.Settings.Server.SessionLifetime.Duration)
	if err != nil {
		return fmt.Errorf("%w (run kukicha --help for setup instructions)", err)
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

	// Check once at startup: these options are fixed when SQLite is compiled.
	var icu, fts5, omitJSON bool
	if err := db.QueryRowContext(ctx, `SELECT
		sqlite_compileoption_used('ENABLE_ICU'),
		sqlite_compileoption_used('ENABLE_FTS5'),
		sqlite_compileoption_used('OMIT_JSON')`).Scan(&icu, &fts5, &omitJSON); err != nil {
		return err
	}
	if !icu {
		return errors.New("metadata normalization requires SQLite ICU support; build with sqlite_icu")
	}
	if !fts5 {
		return errors.New("SQLite FTS5 support is required; build with sqlite_fts5")
	}
	if omitJSON {
		return errors.New("SQLite JSON support is required; build without SQLITE_OMIT_JSON")
	}

	if _, err = db.ExecContext(ctx, `PRAGMA journal_mode = WAL`); err != nil {
		return err
	}

	if err = command.Migrate(ctx, db); err != nil {
		return err
	}

	if err = command.SeedTaxonomy(ctx, db, catalog, app.UnknownItem); err != nil {
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

	validateNewRoot := func(ctx context.Context, root app.Root) error {
		return upload.ValidateNew(ctx, root, conf.Settings.Scan.IgnorePrefixes, conf.Settings.Scan.IgnoreFiles)
	}
	if err := command.ReconcileRoots(ctx, db, conf.Roots, validateNewRoot, upload.CheckRootMarker); err != nil {
		return fmt.Errorf("reconcile configured roots: %w", err)
	}

	uploads := &upload.Service{DB: db, Roots: conf.Roots}
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
			conf,
			conf.Settings.Server.AppBasePath,
			renderer,
			db,
			conf.Settings.UI.PerPage,
			conf.Settings.Radio.MinTracks,
			conf.Settings.UI.DefaultAlbumSort,
			processor,
			notifications,
			conf.Roots,
			conf.ConfigFilePath,
			scan.RecognizedAudioExtensions,
			credentials,
			uploads,
			conf.Settings.Upload.MaxRequestBytes,
		),
		ReadHeaderTimeout: conf.Settings.Server.ReadHeaderTimeout.Duration,
		IdleTimeout:       conf.Settings.Server.IdleTimeout.Duration,
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
			"version", buildinfo.Version(),
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
		"build": proc.Decode(func(app.BuildJobPayload) (proc.Job, error) {
			return &jobs.Build{DB: db, Conf: conf, Notifications: notifications}, nil
		}),
	}
}
