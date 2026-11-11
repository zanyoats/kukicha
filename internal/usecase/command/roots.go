package command

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"path/filepath"
	"strings"

	"github.com/zanyoats/kukicha/internal/app"
	"github.com/zanyoats/kukicha/internal/usecase/query"
)

var ErrRootNotFound = errors.New("Root not found.")

var ErrRootChangeBuildRunning = errors.New("Cannot change roots while a build is in progress. Try again after it finishes.")

// CreateRoot validates empty storage before persisting a new logical root.
func CreateRoot(ctx context.Context, db *sql.DB, baseDir string, input app.RootConfig, validateNew func(context.Context, app.Root) error) error {
	root, err := app.NormalizeRoot(baseDir, input)
	if err != nil {
		return err
	}
	if err := validateNew(ctx, root); err != nil {
		return err
	}
	return saveRoot(ctx, db, root, true)
}

// UpdateRoot replaces storage settings without changing the logical ID.
func UpdateRoot(ctx context.Context, db *sql.DB, baseDir, id string, input app.RootStorageConfig) error {
	root, err := app.NormalizeRoot(baseDir, app.RootConfig{ID: id, RootStorageConfig: input})
	if err != nil {
		return err
	}
	// Look up the exact supplied ID, rather than silently trimming it on updates.
	if root.ConfigID() != id {
		return ErrRootNotFound
	}
	return saveRoot(ctx, db, root, false)
}

// saveRoot serializes persistence with other root changes and job claims.
// Storage validation runs first so remote requests do not hold the write lock.
func saveRoot(ctx context.Context, db *sql.DB, root app.Root, create bool) error {
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	// Acquire the SQLite write lock before reading state or checking overlaps.
	if _, err := tx.ExecContext(ctx, "UPDATE library_roots SET state = state WHERE 0"); err != nil {
		return err
	}
	if !create {
		var state string
		err := tx.QueryRowContext(ctx, "SELECT state FROM library_roots WHERE config_id = ?", root.ConfigID()).Scan(&state)
		if errors.Is(err, sql.ErrNoRows) {
			return ErrRootNotFound
		}
		if err != nil {
			return err
		}
		if state != "available" {
			return ErrRootNotAvailable
		}
	}
	var running bool
	if err := tx.QueryRowContext(ctx, "SELECT EXISTS (SELECT 1 FROM jobs WHERE type = 'build' AND state = 'running')").Scan(&running); err != nil {
		return err
	}
	if running {
		return ErrRootChangeBuildRunning
	}
	stored, err := query.StoredRoots(ctx, tx, true)
	if err != nil {
		return err
	}
	for _, other := range stored {
		if other.ConfigID() == root.ConfigID() {
			if create {
				return errors.New("a root with this ID already exists")
			}
			continue
		}
		if rootsOverlap(root, other) {
			return fmt.Errorf("storage overlaps root %q", other.ConfigID())
		}
	}
	data, err := json.Marshal(query.RootSettings(root))
	if err != nil {
		return err
	}
	if create {
		if _, err := tx.ExecContext(ctx, "INSERT INTO library_root_ids(config_id) VALUES (?) ON CONFLICT(config_id) DO NOTHING", root.ConfigID()); err != nil {
			return err
		}
		_, err = tx.ExecContext(ctx, "INSERT INTO library_roots(id, config_id, settings) SELECT id, config_id, ? FROM library_root_ids WHERE config_id = ?", string(data), root.ConfigID())
	} else {
		_, err = tx.ExecContext(ctx, "UPDATE library_roots SET settings = ? WHERE config_id = ?", string(data), root.ConfigID())
	}
	if err != nil {
		return err
	}
	return tx.Commit()
}

func rootsOverlap(a, b app.Root) bool {
	switch a := a.(type) {
	case *app.LocalRoot:
		b, ok := b.(*app.LocalRoot)
		if !ok {
			return false
		}
		inside := func(parent, child string) bool {
			rel, err := filepath.Rel(parent, child)
			return err == nil && rel != ".." && !strings.HasPrefix(rel, ".."+string(filepath.Separator))
		}
		return inside(a.Path, b.Path) || inside(b.Path, a.Path)
	case *app.RemoteRoot:
		b, ok := b.(*app.RemoteRoot)
		return ok && a.EndpointURL == b.EndpointURL && a.Bucket == b.Bucket && (strings.HasPrefix(a.Prefix, b.Prefix) || strings.HasPrefix(b.Prefix, a.Prefix))
	}
	return false
}
