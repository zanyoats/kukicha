package query

import (
	"context"
	"database/sql"
	_ "embed"
	"errors"
	"fmt"

	"github.com/zanyoats/kukicha/internal/app"
)

var ErrAppMetadataNotFound = errors.New("app metadata not found")

//go:embed sql/app_metadata.sql
var appMetadataSQL string

func AppMetadata(
	ctx context.Context,
	db interface {
		QueryRowContext(context.Context, string, ...any) *sql.Row
	},
	key app.AppMetadataKey,
) (string, error) {
	var value string

	err := db.QueryRowContext(
		ctx,
		appMetadataSQL,
		sql.Named("key", key),
	).Scan(&value)

	if errors.Is(err, sql.ErrNoRows) {
		return "", fmt.Errorf(
			"query app metadata key %d: %w",
			key,
			ErrAppMetadataNotFound,
		)
	}
	if err != nil {
		return "", fmt.Errorf("query app metadata key %d: %w", key, err)
	}

	return value, nil
}
