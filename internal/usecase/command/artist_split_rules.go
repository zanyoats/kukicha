package command

import (
	"context"
	"database/sql"
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
)

var ErrArtistSplitRuleNotFound = errors.New("artist split rule not found")

//go:embed sql/update_artist_split_rule.sql
var updateArtistSplitRuleSQL string

func UpdateArtistSplitRule(ctx context.Context, db *sql.DB, albumKey string, artists []string) error {
	encoded, err := json.Marshal(artists)
	if err != nil {
		return fmt.Errorf("encode artist split rule: %w", err)
	}
	result, err := db.ExecContext(ctx, updateArtistSplitRuleSQL,
		sql.Named("album_key", albumKey), sql.Named("mapped_artists", string(encoded)))
	if err != nil {
		return fmt.Errorf("update artist split rule: %w", err)
	}
	count, err := result.RowsAffected()
	if err != nil {
		return fmt.Errorf("count updated artist split rules: %w", err)
	}
	if count == 0 {
		return ErrArtistSplitRuleNotFound
	}
	return nil
}
