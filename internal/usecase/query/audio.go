package query

import (
	"context"
	"database/sql"
	_ "embed"
	"errors"
	"fmt"
)

var ErrTrackNotFound = errors.New("track not found")

//go:embed sql/track_audio.sql
var trackAudioSQL string

// TrackAudio locates audio using a root ID resolved against the current config.
type TrackAudio struct {
	ConfigID     string
	RelativePath string
	Extension    string
}

func TrackAudioByID(
	ctx context.Context,
	db *sql.DB,
	trackID int64,
) (TrackAudio, error) {
	var audio TrackAudio

	err := db.QueryRowContext(
		ctx,
		trackAudioSQL,
		sql.Named("track_id", trackID),
	).Scan(
		&audio.ConfigID,
		&audio.RelativePath,
		&audio.Extension,
	)

	if errors.Is(err, sql.ErrNoRows) {
		return TrackAudio{}, fmt.Errorf(
			"query track audio %d: %w",
			trackID,
			ErrTrackNotFound,
		)
	}
	if err != nil {
		return TrackAudio{}, fmt.Errorf(
			"query track audio %d: %w",
			trackID,
			err,
		)
	}

	return audio, nil
}
