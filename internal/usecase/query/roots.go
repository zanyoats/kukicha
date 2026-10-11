package query

import (
	"context"
	"database/sql"
	_ "embed"
	"fmt"

	"github.com/zanyoats/kukicha/internal/app"
)

//go:embed sql/roots.sql
var rootsSQL string

//go:embed sql/root_config_ids.sql
var rootConfigIDsSQL string

// RootsMatchConfig compares indexed and configured root IDs, ignoring order.
func RootsMatchConfig(ctx context.Context, db *sql.DB, configured []app.Root) (bool, error) {
	rows, err := db.QueryContext(ctx, rootConfigIDsSQL)
	if err != nil {
		return false, fmt.Errorf("query indexed root IDs: %w", err)
	}
	defer rows.Close()

	remaining := make(map[string]struct{}, len(configured))
	for _, root := range configured {
		remaining[root.ConfigID()] = struct{}{}
	}
	matches := true
	for rows.Next() {
		var id string
		if err := rows.Scan(&id); err != nil {
			return false, fmt.Errorf("read indexed root ID: %w", err)
		}
		if _, ok := remaining[id]; !ok {
			matches = false
		}
		delete(remaining, id)
	}
	if err := rows.Err(); err != nil {
		return false, fmt.Errorf("iterate indexed root IDs: %w", err)
	}
	return matches && len(remaining) == 0, nil
}

// Root contains counts for an indexed library root.
type Root struct {
	ConfigID      string
	Tracks        int64
	Albums        int64
	Playlists     int64
	AlbumSidecars int64
}

func Roots(ctx context.Context, db *sql.DB) ([]Root, error) {
	rows, err := db.QueryContext(ctx, rootsSQL)
	if err != nil {
		return nil, fmt.Errorf("query roots: %w", err)
	}
	defer rows.Close()

	roots := make([]Root, 0)
	for rows.Next() {
		var root Root
		if err := rows.Scan(&root.ConfigID, &root.Tracks, &root.Albums, &root.Playlists, &root.AlbumSidecars); err != nil {
			return nil, fmt.Errorf("scan root: %w", err)
		}
		roots = append(roots, root)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate roots: %w", err)
	}
	return roots, nil
}
