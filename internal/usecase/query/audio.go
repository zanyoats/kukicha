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

// TrackAudio contains the database-owned information needed to locate a track's
// audio file. These paths must remain internal and must never be returned to the
// client.
type TrackAudio struct {
	RootPath     string
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
		&audio.RootPath,
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
