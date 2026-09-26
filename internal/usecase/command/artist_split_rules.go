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
var ErrArtistSplitRuleNotFound = errors.New("Artist split rule not found.")

//go:embed sql/find_or_insert_mapping.sql
var findOrInsertMappingSQL string

//go:embed sql/update_artist_split_rule.sql
var updateArtistSplitRuleSQL string

func FindOrInsertMapping(
	ctx context.Context,
	conn *sql.Conn,
	albumKey string,
	mapping []string,
) ([]string, error) {
	validated, err := cleanAndValidateArtistMapping(albumKey, mapping)
	if err != nil {
		return nil, err
	}

	b, err := json.Marshal(validated)
	if err != nil {
		return nil, err
	}

	var mappingJSON string

	err = conn.QueryRowContext(
		ctx,
		findOrInsertMappingSQL,
		sql.Named("album_key", albumKey),
		sql.Named("mapped_artists", string(b)),
	).Scan(&mappingJSON)

	if err != nil {
		return nil, err
	}

	var result []string
	if err := json.Unmarshal([]byte(mappingJSON), &result); err != nil {
		return nil, err
	}

	return cleanAndValidateArtistMapping(albumKey, result)
}

func UpdateArtistSplitRule(
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
