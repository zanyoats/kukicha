package query

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"

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
