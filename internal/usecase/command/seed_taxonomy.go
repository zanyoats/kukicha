package command

import (
	"context"
	"database/sql"
	_ "embed"
	"fmt"
	"log/slog"

	"github.com/zanyoats/kukicha/internal/app"
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
	var tx *sql.Tx
	var err error

	if tx, err = conn.BeginTx(ctx, nil); err != nil {
		return err
	}

	// TODO we don't want to do this every startup
	_, err = tx.ExecContext(ctx, resetTaxonomySQL)

	if err != nil {
		_ = tx.Rollback()
		return fmt.Errorf("reset taxonomy tables: %w", err)
	}

	insertTaxonomy, err := tx.PrepareContext(ctx, insertTaxonomySQL)
	if err != nil {
		_ = tx.Rollback()
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
			_ = tx.Rollback()
			return fmt.Errorf("insert taxonomy row %d: %w", rowNum+1, err)
		}
	}

	if err = insertTaxonomy.Close(); err != nil {
		_ = tx.Rollback()
		return fmt.Errorf("close taxonomy insert: %w", err)
	}
	insertTaxonomyClosed = true

	if _, err = tx.ExecContext(
		ctx,
		populateTaxonomyLookupTablesSQL,
		sql.Named("unknown_item", unknownItem),
	); err != nil {
		_ = tx.Rollback()
		return fmt.Errorf("populate taxonomy lookup tables: %w", err)
	}

	if err := tx.Commit(); err != nil {
		return err
	}

	slog.InfoContext(ctx, "seeded library taxonomy")

	return nil
}
