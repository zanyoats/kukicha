package command

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"

	"github.com/zanyoats/kukicha/internal/app"
	"github.com/zanyoats/kukicha/internal/metadata"
)

var ErrOverrideAlbumNotFound = errors.New("album not found")

// DeleteAlbumMetadataOverride removes the configured source. The next build
// restores ordinary metadata and artwork; cached entity results remain reusable.
func DeleteAlbumMetadataOverride(ctx context.Context, db *sql.DB, albumKey string) error {
	_, err := db.ExecContext(ctx, "DELETE FROM album_metadata_overrides WHERE album_key = ?", albumKey)
	return err
}

// SetAlbumMetadataOverride validates before writing, preserving an existing choice on failure.
func SetAlbumMetadataOverride(ctx context.Context, db *sql.DB, albumKey, sourceURL string) error {
	source, err := metadata.ParseSource(sourceURL)
	if err != nil {
		return err
	}
	result, err := db.ExecContext(ctx, `INSERT INTO album_metadata_overrides
  (album_key, provider, entity_type, entity_id, source_url)
  SELECT album_key, ?, ?, ?, ? FROM library_album_ids WHERE album_key = ?
  ON CONFLICT(album_key) DO UPDATE SET provider=excluded.provider,
  entity_type=excluded.entity_type, entity_id=excluded.entity_id, source_url=excluded.source_url`,
		source.Provider, source.EntityType, source.EntityID, source.SourceURL, albumKey)
	if err != nil {
		return err
	}
	count, err := result.RowsAffected()
	if err == nil && count == 0 {
		return ErrOverrideAlbumNotFound
	}
	return err
}

func StoreMetadataEntity(ctx context.Context, conn *sql.Conn, value app.MetadataOverride) error {
	data, err := json.Marshal(value)
	if err != nil {
		return err
	}
	_, err = conn.ExecContext(ctx, `INSERT INTO metadata_entity_cache(provider, entity_type, entity_id, result_json)
  VALUES (?, ?, ?, ?) ON CONFLICT(provider, entity_type, entity_id)
  DO UPDATE SET result_json=excluded.result_json`, value.Provider, value.EntityType, value.EntityID, string(data))
	return err
}
