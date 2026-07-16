package command

import (
	"context"
	"database/sql"
	_ "embed"
	"fmt"
	"log"

	"github.com/zanyoats/kukicha/internal/app"
)

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
	_, err = tx.ExecContext(ctx, `
		DELETE FROM taxonomy;
		DELETE FROM taxonomy_styles;
		DELETE FROM taxonomy_genres;
	`)

	if err != nil {
		_ = tx.Rollback()
		return fmt.Errorf("reset taxonomy tables: %w", err)
	}

	insertTaxonomy, err := tx.PrepareContext(ctx, `
		INSERT INTO taxonomy (
			id,
			kind,
			name,
			parent,
			source,
			alias,
			status,
			count
		)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?);
	`)
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
			rowNum,
			record.Kind,
			record.Name,
			record.Parent,
			record.Source,
			record.SourceTerm,
			record.Status,
			record.Count,
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

	if _, err = tx.ExecContext(ctx, `
		INSERT OR IGNORE INTO taxonomy_genres (genre)
		SELECT name
		FROM taxonomy
		WHERE kind = 'genre'
		ORDER BY id;

		-- Insert our special unknown token so fk constraints don't fire
		INSERT OR IGNORE INTO taxonomy_genres (genre)
		VALUES (?); 

		INSERT OR IGNORE INTO taxonomy_styles (style, parent_genre)
		SELECT name, parent
		FROM taxonomy
		WHERE kind = 'style'
		ORDER BY id;
	`, unknownItem); err != nil {
		_ = tx.Rollback()
		return fmt.Errorf("populate taxonomy lookup tables: %w", err)
	}

	if err := tx.Commit(); err != nil {
		return err
	}

	log.Printf("Seeded library taxonomy")

	return nil
}
