package query

import (
	"context"
	"database/sql"
	_ "embed"
	"fmt"
)

//go:embed sql/roots.sql
var rootsSQL string

// Root contains counts for an indexed library root.
type Root struct {
	ConfigID      string
	State         string
	Tracks        int64
	Albums        int64
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
		if err := rows.Scan(&root.ConfigID, &root.State, &root.Tracks, &root.Albums, &root.AlbumSidecars); err != nil {
			return nil, fmt.Errorf("scan root: %w", err)
		}
		roots = append(roots, root)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate roots: %w", err)
	}
	return roots, nil
}
