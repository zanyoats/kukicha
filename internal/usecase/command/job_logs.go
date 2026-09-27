package command

import (
	"context"
	"database/sql"
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"maps"
	"time"

	"github.com/zanyoats/kukicha/internal/app"
)

//go:embed sql/insert_library_job_log.sql
var insertLibraryJobLogSQL string

type JobLogDB interface {
	ExecContext(context.Context, string, ...any) (sql.Result, error)
}

type jobLogDBKey struct{}

// WithJobLogDB routes logging through the job's connection or transaction.
// A transaction must stay alive until all logging using this context completes.
func WithJobLogDB(ctx context.Context, db JobLogDB) context.Context {
	return context.WithValue(ctx, jobLogDBKey{}, db)
}

// NewJobLogger uses output's level filter for both destinations, persisting
// enabled records carrying a job ID and forwarding them to output.
// Capture slog.Default() as output after configuring the application's logging.
func NewJobLogger(db JobLogDB, output *slog.Logger) *slog.Logger {
	if output == nil {
		output = slog.Default()
	}
	return slog.New(&jobLogHandler{db: db, output: output.Handler(), attributes: make(map[string]any)})
}

type jobLogHandler struct {
	db         JobLogDB
	output     slog.Handler
	attributes map[string]any
}

func (h *jobLogHandler) Enabled(ctx context.Context, level slog.Level) bool {
	return h.output.Enabled(ctx, level)
}

func (h *jobLogHandler) Handle(ctx context.Context, record slog.Record) error {
	if !h.Enabled(ctx, record.Level) {
		return nil
	}
	outputErr := h.output.Handle(ctx, record.Clone())
	jobID, ok := app.JobIDFromContext(ctx)
	if !ok {
		return outputErr
	}
	err := h.insert(ctx, jobID, record)
	if err != nil {
		// slog methods discard handler errors. Report storage failures to the original
		// output handler, never back through this database handler.
		failure := slog.NewRecord(time.Now(), slog.LevelError, "cannot record job log", 0)
		failure.Add("job_id", jobID, "error", err)
		if h.output.Enabled(ctx, failure.Level) {
			_ = h.output.Handle(ctx, failure)
		}
	}
	return errors.Join(outputErr, err)
}

func (h *jobLogHandler) insert(ctx context.Context, jobID int64, record slog.Record) error {
	// Copy attributes from logger.With, then add this message's attributes.
	// Later values replace earlier values with the same key.
	data := maps.Clone(h.attributes)
	record.Attrs(func(attr slog.Attr) bool {
		addJobLogAttr(data, attr)
		return true
	})
	// Preserve valid attributes; stringify only values JSON cannot encode.
	for key, value := range data {
		if _, err := json.Marshal(value); err != nil {
			data[key] = fmt.Sprint(value)
		}
	}

	encoded, err := json.Marshal(data)
	if err != nil {
		return fmt.Errorf("encode library job log data: %w", err)
	}
	level := "info"
	if record.Level >= slog.LevelError {
		level = "error"
	} else if record.Level >= slog.LevelWarn {
		level = "warn"
	} else if record.Level < slog.LevelInfo {
		level = "debug"
	}
	db := h.db
	if scoped, ok := ctx.Value(jobLogDBKey{}).(JobLogDB); ok {
		db = scoped
	}
	if db == nil {
		return errors.New("write library job log: missing database")
	}
	_, err = db.ExecContext(ctx, insertLibraryJobLogSQL,
		sql.Named("job_id", jobID), sql.Named("level", level),
		sql.Named("message", record.Message),
		sql.Named("timestamp", record.Time.UTC().Format("2006-01-02 15:04:05")),
		sql.Named("data", string(encoded)))
	if err != nil {
		return fmt.Errorf("write library job log: %w", err)
	}
	return nil
}

func (h *jobLogHandler) WithAttrs(attrs []slog.Attr) slog.Handler {
	clone := *h
	clone.output = h.output.WithAttrs(attrs)
	clone.attributes = maps.Clone(h.attributes)
	for _, attr := range attrs {
		addJobLogAttr(clone.attributes, attr)
	}
	return &clone
}

// WithGroup is required by slog.Handler, but this logger supports only flat
// attributes. Group names are ignored.
func (h *jobLogHandler) WithGroup(string) slog.Handler {
	return h
}

// addJobLogAttr skips unsupported groups and stores errors as readable strings.
func addJobLogAttr(data map[string]any, attr slog.Attr) {
	attr.Value = attr.Value.Resolve()
	if attr.Equal(slog.Attr{}) || attr.Value.Kind() == slog.KindGroup {
		return
	}
	value := attr.Value.Any()
	if err, ok := value.(error); ok {
		value = err.Error()
	}
	data[attr.Key] = value
}
