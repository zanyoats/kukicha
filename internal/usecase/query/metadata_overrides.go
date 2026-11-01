package query

import (
	"context"
	"database/sql"
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"

	"github.com/zanyoats/kukicha/internal/app"
)

func CachedMetadataEntity(ctx context.Context, conn *sql.Conn, source app.MetadataSource) (app.MetadataOverride, bool, error) {
	var result app.MetadataOverride
	var raw string
	err := conn.QueryRowContext(ctx, `SELECT result_json FROM metadata_entity_cache
  WHERE provider=? AND entity_type=? AND entity_id=?`, source.Provider, source.EntityType, source.EntityID).Scan(&raw)
	if errors.Is(err, sql.ErrNoRows) {
		return result, false, nil
	}
	if err != nil {
		return result, false, err
	}
	if err := json.Unmarshal([]byte(raw), &result); err != nil {
		return result, false, err
	}
	result.MetadataSource = source
	return result, true, nil
}

//go:embed sql/metadata_override_albums.sql
var metadataOverrideAlbumsSQL string

type MetadataOverrideAlbum struct {
	AlbumKey    string
	OverrideURL string
	ID          int64
	Title       string
}

// MetadataOverrideAlbums returns all available albums for the selected artist.
func MetadataOverrideAlbums(ctx context.Context, db *sql.DB, artistID int64) ([]MetadataOverrideAlbum, error) {
	rows, err := db.QueryContext(ctx, metadataOverrideAlbumsSQL, sql.Named("artist_id", artistID))
	if err != nil {
		return nil, fmt.Errorf("query metadata override albums: %w", err)
	}
	defer rows.Close()
	var albums []MetadataOverrideAlbum
	for rows.Next() {
		var album MetadataOverrideAlbum
		if err := rows.Scan(&album.ID, &album.Title, &album.AlbumKey, &album.OverrideURL); err != nil {
			return nil, fmt.Errorf("scan metadata override album: %w", err)
		}
		albums = append(albums, album)
	}
	return albums, rows.Err()
}
