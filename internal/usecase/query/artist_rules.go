package query

import (
	"context"
	"database/sql"
	_ "embed"
	"encoding/json"
	"fmt"

	"github.com/zanyoats/kukicha/internal/app"
)

//go:embed sql/artist_rules.sql
var artistRulesSQL string

type ArtistRule struct {
	AlbumKey        string
	MappedArtists   []string
	OriginalArtists []string
	AlbumID         int64
	AlbumTitle      string
	HasRule         bool
}

func ArtistRules(ctx context.Context, db *sql.DB, artistID int64) ([]ArtistRule, error) {
	rows, err := db.QueryContext(ctx, artistRulesSQL, sql.Named("artist_id", artistID), sql.Named("unknown_artist", app.UnknownItem))
	if err != nil {
		return nil, fmt.Errorf("query artist rules: %w", err)
	}
	defer rows.Close()

	var rules []ArtistRule
	for rows.Next() {
		var rule ArtistRule
		var artists, originals string
		if err := rows.Scan(&rule.AlbumKey, &artists, &rule.AlbumID, &rule.AlbumTitle, &rule.HasRule, &originals); err != nil {
			return nil, fmt.Errorf("scan artist rule: %w", err)
		}
		if err := json.Unmarshal([]byte(artists), &rule.MappedArtists); err != nil {
			return nil, fmt.Errorf("decode artist rule %q: %w", rule.AlbumKey, err)
		}
		if err := json.Unmarshal([]byte(originals), &rule.OriginalArtists); err != nil {
			return nil, fmt.Errorf("decode original artists %q: %w", rule.AlbumKey, err)
		}
		rules = append(rules, rule)
	}
	return rules, rows.Err()
}
