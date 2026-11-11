package query

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"

	"github.com/zanyoats/kukicha/internal/app"
)

type RootReader interface {
	QueryContext(context.Context, string, ...any) (*sql.Rows, error)
}

// StoredRoots reads persisted storage settings without accessing the filesystem.
// Pending roots remain available to cleanup and the settings page when requested.
func StoredRoots(ctx context.Context, db RootReader, includePending bool) ([]app.Root, error) {
	rows, err := db.QueryContext(ctx, "SELECT settings FROM library_roots WHERE state = 'available' OR ? ORDER BY config_id COLLATE BINARY", includePending)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var roots []app.Root
	for rows.Next() {
		var data []byte
		if err := rows.Scan(&data); err != nil {
			return nil, err
		}
		var kind struct {
			Type string `json:"type"`
		}
		if err := json.Unmarshal(data, &kind); err != nil {
			return nil, err
		}
		var root app.Root
		switch kind.Type {
		case "local":
			root = new(app.LocalRoot)
		case "remote":
			root = new(app.RemoteRoot)
		default:
			return nil, fmt.Errorf("unknown root type %q", kind.Type)
		}
		if err := json.Unmarshal(data, root); err != nil {
			return nil, err
		}
		roots = append(roots, root)
	}
	return roots, rows.Err()
}

// RootSettings returns the editable, source-specific fields of a normalized root.
func RootSettings(root app.Root) app.RootConfig {
	switch r := root.(type) {
	case *app.LocalRoot:
		return app.RootConfig{ID: r.ID, RootStorageConfig: app.RootStorageConfig{Type: "local", Path: &r.Path}}
	case *app.RemoteRoot:
		return app.RootConfig{ID: r.ID, RootStorageConfig: app.RootStorageConfig{Type: "remote", EndpointURL: &r.EndpointURL, Bucket: &r.Bucket, Prefix: &r.Prefix, Profile: &r.Profile, Region: &r.Region, AddressingStyle: &r.AddressingStyle}}
	default:
		panic("unsupported root type")
	}
}
