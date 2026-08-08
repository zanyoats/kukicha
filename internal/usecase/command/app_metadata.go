package command

import (
	"context"
	"crypto/rand"
	"database/sql"
	_ "embed"
	"errors"
	"fmt"

	"github.com/zanyoats/kukicha/internal/app"
	"github.com/zanyoats/kukicha/internal/identicon"
	"github.com/zanyoats/kukicha/internal/usecase/query"
)

//go:embed sql/set_app_metadata.sql
var setAppMetadataSQL string

func InitializeInstanceMetadata(
	ctx context.Context,
	db *sql.DB,
) error {
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("begin instance metadata initialization: %w", err)
	}
	defer tx.Rollback()

	instanceID, err := getOrSetAppMetadata(
		ctx,
		tx,
		app.InstanceID,
		rand.Text,
	)
	if err != nil {
		return fmt.Errorf("initialize instance ID: %w", err)
	}

	if _, err := getOrSetAppMetadata(
		ctx,
		tx,
		app.InstanceIdenticon,
		func() string { return identicon.SVG(instanceID) },
	); err != nil {
		return fmt.Errorf("initialize instance identicon: %w", err)
	}

	if err := tx.Commit(); err != nil {
		return fmt.Errorf("commit instance metadata initialization: %w", err)
	}

	return nil
}

func getOrSetAppMetadata(
	ctx context.Context,
	tx *sql.Tx,
	key app.AppMetadataKey,
	value func() string,
) (string, error) {
	if existing, err := query.AppMetadata(ctx, tx, key); err == nil {
		return existing, nil
	} else if !errors.Is(err, query.ErrAppMetadataNotFound) {
		return "", err
	}

	generated := value()
	if err := SetAppMetadata(ctx, tx, key, generated); err != nil {
		return "", err
	}

	return generated, nil
}

func SetAppMetadata(
	ctx context.Context,
	tx *sql.Tx,
	key app.AppMetadataKey,
	value string,
) error {
	if _, err := tx.ExecContext(
		ctx,
		setAppMetadataSQL,
		sql.Named("key", key),
		sql.Named("value", value),
	); err != nil {
		return fmt.Errorf(
			"setting app metadata key %d: %w",
			key, err,
		)
	}

	return nil
}
