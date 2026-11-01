package query

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
)

type UnrecognizedTrack struct {
	ConfigID     string
	UploadedPath string
	Album        string
	Artists      []string
	Status       string
}

func UnrecognizedTrackCount(ctx context.Context, db *sql.DB) (int, error) {
	var count int
	err := db.QueryRowContext(ctx, "SELECT COUNT(*) FROM unrecognized_tracks").Scan(&count)
	return count, err
}

func UnrecognizedTracks(ctx context.Context, db *sql.DB) ([]UnrecognizedTrack, error) {
	rows, err := db.QueryContext(ctx, `SELECT config_id, uploaded_path, COALESCE(album, ''),
        COALESCE(artists, '[]'), status FROM unrecognized_tracks
        ORDER BY config_id COLLATE BINARY, path_dir COLLATE BINARY, uploaded_path COLLATE BINARY`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var tracks []UnrecognizedTrack
	for rows.Next() {
		var track UnrecognizedTrack
		var artists string
		if err := rows.Scan(&track.ConfigID, &track.UploadedPath, &track.Album, &artists, &track.Status); err != nil {
			return nil, err
		}
		if err := json.Unmarshal([]byte(artists), &track.Artists); err != nil {
			return nil, fmt.Errorf("decode unrecognized track artists: %w", err)
		}
		tracks = append(tracks, track)
	}
	return tracks, rows.Err()
}
