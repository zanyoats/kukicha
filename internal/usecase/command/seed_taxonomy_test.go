package command

import (
	"context"
	"database/sql"
	"encoding/hex"
	"testing"

	"github.com/zanyoats/kukicha/internal/app"
)

func TestSeedTaxonomyUsesCatalogChecksum(t *testing.T) {
	ctx := context.Background()
	db := openLibraryIDsTestDB(t)
	if err := Migrate(ctx, db); err != nil {
		t.Fatalf("migrate test database: %v", err)
	}

	catalog, err := app.LoadTaxonomy()
	if err != nil {
		t.Fatalf("load taxonomy: %v", err)
	}
	if err := SeedTaxonomy(ctx, db, catalog, "Unknown"); err != nil {
		t.Fatalf("seed taxonomy without stored checksum: %v", err)
	}

	wantChecksum := hex.EncodeToString(catalog.Checksum)
	if got := storedTaxonomyChecksum(t, db); got != wantChecksum {
		t.Errorf("stored taxonomy checksum = %q, want %q", got, wantChecksum)
	}

	if _, err := db.ExecContext(ctx, `DELETE FROM taxonomy_styles`); err != nil {
		t.Fatalf("remove seeded taxonomy styles: %v", err)
	}
	if err := SeedTaxonomy(ctx, db, catalog, "Unknown"); err != nil {
		t.Fatalf("seed taxonomy with matching checksum: %v", err)
	}
	if got := countRows(t, db, `SELECT COUNT(*) FROM taxonomy_styles`); got != 0 {
		t.Errorf("taxonomy styles after matching checksum = %d, want 0", got)
	}

	if _, err := db.ExecContext(
		ctx,
		`UPDATE app_metadata SET value = 'stale' WHERE key = ?`,
		app.TaxonomyChecksum,
	); err != nil {
		t.Fatalf("make taxonomy checksum stale: %v", err)
	}
	if err := SeedTaxonomy(ctx, db, catalog, "Unknown"); err != nil {
		t.Fatalf("seed taxonomy with stale checksum: %v", err)
	}
	if got := countRows(t, db, `SELECT COUNT(*) FROM taxonomy_styles`); got == 0 {
		t.Fatal("taxonomy styles were not restored for stale checksum")
	}
	if got := storedTaxonomyChecksum(t, db); got != wantChecksum {
		t.Errorf("restored taxonomy checksum = %q, want %q", got, wantChecksum)
	}
}

func storedTaxonomyChecksum(t *testing.T, db *sql.DB) string {
	t.Helper()

	var checksum string
	if err := db.QueryRow(
		`SELECT value FROM app_metadata WHERE key = ?`,
		app.TaxonomyChecksum,
	).Scan(&checksum); err != nil {
		t.Fatalf("query stored taxonomy checksum: %v", err)
	}
	return checksum
}
