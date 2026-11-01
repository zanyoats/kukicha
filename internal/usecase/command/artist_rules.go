package command

import (
	"context"
	"database/sql"
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
)

var ErrInvalidArtistMapping = errors.New("Each artist must be a string on a single line.")
var ErrEmptyArtistMapping = errors.New("Enter at least one artist.")
var ErrArtistRuleNotFound = errors.New("Album not found.")

//go:embed sql/update_artist_rule.sql
var updateArtistRuleSQL string

func UpdateArtistRule(
	ctx context.Context,
	db *sql.DB,
	albumKey string,
	mapping []string,
) error {
	validated, err := cleanAndValidateArtistMapping(albumKey, mapping)
	if err != nil {
		return err
	}
	encoded, err := json.Marshal(validated)
	if err != nil {
		return fmt.Errorf("encode artist rule: %w", err)
	}
	result, err := db.ExecContext(ctx, updateArtistRuleSQL,
		sql.Named("album_key", albumKey), sql.Named("mapped_artists", string(encoded)))
	if err != nil {
		return fmt.Errorf("update artist rule: %w", err)
	}
	count, err := result.RowsAffected()
	if err != nil {
		return fmt.Errorf("count updated artist rules: %w", err)
	}
	if count == 0 {
		return ErrArtistRuleNotFound
	}
	return nil
}

// DeleteArtistRule restores the original album artists on the next rebuild.
func DeleteArtistRule(ctx context.Context, db *sql.DB, albumKey string) error {
	_, err := db.ExecContext(ctx, "DELETE FROM album_artist_mappings WHERE album_key = ?", albumKey)
	if err != nil {
		return fmt.Errorf("delete artist rule: %w", err)
	}
	return nil
}

func cleanAndValidateArtistMapping(albumKey string, mapping []string) ([]string, error) {
	result := make([]string, 0, len(mapping))

	for _, value := range mapping {
		if strings.ContainsAny(value, "\r\n") {
			return nil, fmt.Errorf(
				"album %q has invalid album artist value %q: %w",
				albumKey, value, ErrInvalidArtistMapping,
			)
		}
		if trimmed := strings.TrimSpace(value); trimmed != "" {
			result = append(result, trimmed)
		}
	}

	if len(result) == 0 {
		return nil, fmt.Errorf(
			"album %q: %w", albumKey, ErrEmptyArtistMapping,
		)
	}

	return result, nil
}
