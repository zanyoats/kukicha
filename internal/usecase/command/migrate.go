package command

import (
	"context"
	"database/sql"
	_ "embed"
	"fmt"
	"log/slog"
	"strings"

	"github.com/zanyoats/kukicha/internal/app"
)

//go:embed sql/migration_001.sql
var migration001SQL string

//go:embed sql/get_user_version.sql
var getUserVersionSQL string

//go:embed sql/set_user_version.sql
var setUserVersionSQL string

type migration struct {
	version int
	comment string
	sql     string
}

var migrations = []migration{
	{
		version: 1,
		comment: "creating initial data model",
		// SQLite schema definitions cannot bind parameters. Escape the constant
		// as a SQL string literal before substituting it into the initial schema.
		sql: strings.ReplaceAll(migration001SQL, "{{unknown_item}}", "'"+strings.ReplaceAll(app.UnknownItem, "'", "''")+"'"),
	},
}

func Migrate(ctx context.Context, writerDb *sql.DB) error {
	conn, err := writerDb.Conn(ctx)
	if err != nil {
		return err
	}
	defer conn.Close()

	var version int
	if err := conn.QueryRowContext(ctx, getUserVersionSQL).Scan(&version); err != nil {
		return err
	}

	for _, migration := range migrations {
		if migration.version <= version {
			continue
		}

		if migration.version != version+1 {
			return fmt.Errorf("missing migration from version %d to %d", version, migration.version)
		}

		tx, err := conn.BeginTx(ctx, nil)
		if err != nil {
			return err
		}

		if _, err := tx.ExecContext(ctx, migration.sql); err != nil {
			tx.Rollback()
			return fmt.Errorf("apply migration %d: %w", migration.version, err)
		}

		if _, err := tx.ExecContext(ctx, fmt.Sprintf(setUserVersionSQL, migration.version)); err != nil {
			tx.Rollback()
			return fmt.Errorf("set user_version %d: %w", migration.version, err)
		}

		if err := tx.Commit(); err != nil {
			return err
		}

		slog.InfoContext(
			ctx,
			"applied database migration",
			"version", migration.version,
			"description", migration.comment,
		)

		version = migration.version
	}

	return nil
}
