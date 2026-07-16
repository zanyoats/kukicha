package command

import (
	"context"
	"database/sql"
)

func SetAppMetadata(ctx context.Context, tx *sql.Tx, key int, value string) error {
	_, err := tx.ExecContext(ctx, `
		INSERT INTO app_metadata (key, value)
		VALUES (?, ?)
		ON CONFLICT(key) DO UPDATE SET value = excluded.value;
	`, key, value)

	return err
}
