package command

import (
	"context"
	"database/sql"
	_ "embed"
	"encoding/hex"
	"errors"
	"fmt"
	"log/slog"

	"github.com/zanyoats/kukicha/internal/app"
	"github.com/zanyoats/kukicha/internal/usecase/query"
)

//go:embed sql/reset_taxonomy.sql
var resetTaxonomySQL string

//go:embed sql/insert_taxonomy.sql
var insertTaxonomySQL string

//go:embed sql/populate_taxonomy_lookup_tables.sql
var populateTaxonomyLookupTablesSQL string

func SeedTaxonomy(
	ctx context.Context,
	conn *sql.DB,
	catalog *app.Catalog,
	unknownItem string,
) error {
	tx, err := conn.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()

	checksum := hex.EncodeToString(catalog.Checksum)
	storedChecksum, err := query.AppMetadata(ctx, tx, app.TaxonomyChecksum)
	if err == nil && storedChecksum == checksum {
		return nil
	}
	if err != nil && !errors.Is(err, query.ErrAppMetadataNotFound) {
		return fmt.Errorf("query taxonomy checksum: %w", err)
	}

	_, err = tx.ExecContext(ctx, resetTaxonomySQL)

	if err != nil {
		return fmt.Errorf("reset taxonomy tables: %w", err)
	}

	insertTaxonomy, err := tx.PrepareContext(ctx, insertTaxonomySQL)
	if err != nil {
		return fmt.Errorf("prepare taxonomy insert: %w", err)
	}
	insertTaxonomyClosed := false
	defer func() {
		if !insertTaxonomyClosed {
			_ = insertTaxonomy.Close()
		}
	}()

	for rowNum, record := range catalog.Records() {
		if _, err = insertTaxonomy.ExecContext(
			ctx,
			sql.Named("id", rowNum),
			sql.Named("kind", record.Kind),
			sql.Named("name", record.Name),
			sql.Named("parent", record.Parent),
			sql.Named("source", record.Source),
			sql.Named("alias", record.SourceTerm),
			sql.Named("status", record.Status),
			sql.Named("count", record.Count),
		); err != nil {
			return fmt.Errorf("insert taxonomy row %d: %w", rowNum+1, err)
		}
	}

	if err = insertTaxonomy.Close(); err != nil {
		return fmt.Errorf("close taxonomy insert: %w", err)
	}
	insertTaxonomyClosed = true

	if _, err = tx.ExecContext(
		ctx,
		populateTaxonomyLookupTablesSQL,
		sql.Named("unknown_item", unknownItem),
	); err != nil {
		return fmt.Errorf("populate taxonomy lookup tables: %w", err)
	}

	if err := SetAppMetadata(
		ctx,
		tx,
		app.TaxonomyChecksum,
		checksum,
	); err != nil {
		return fmt.Errorf("set taxonomy checksum: %w", err)
	}

	if err := tx.Commit(); err != nil {
		return err
	}

	slog.InfoContext(ctx, "seeded library taxonomy")

	return nil
}
