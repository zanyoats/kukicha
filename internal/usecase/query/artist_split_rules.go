package query

import (
	"context"
	"database/sql"
	_ "embed"
	"encoding/json"
	"fmt"
)

//go:embed sql/artist_split_rules.sql
var artistSplitRulesSQL string

type ArtistSplitRule struct {
	AlbumKey      string
	MappedArtists []string
	AlbumID       int64
	AlbumTitle    string
}

func ArtistSplitRules(ctx context.Context, db *sql.DB) ([]ArtistSplitRule, error) {
	rows, err := db.QueryContext(ctx, artistSplitRulesSQL)
	if err != nil {
		return nil, fmt.Errorf("query artist split rules: %w", err)
	}
	defer rows.Close()

	var rules []ArtistSplitRule
	for rows.Next() {
		var rule ArtistSplitRule
		var artists string
		if err := rows.Scan(&rule.AlbumKey, &artists, &rule.AlbumID, &rule.AlbumTitle); err != nil {
			return nil, fmt.Errorf("scan artist split rule: %w", err)
		}
		if err := json.Unmarshal([]byte(artists), &rule.MappedArtists); err != nil {
			return nil, fmt.Errorf("decode artist split rule %q: %w", rule.AlbumKey, err)
		}
		rules = append(rules, rule)
	}
	return rules, rows.Err()
}
