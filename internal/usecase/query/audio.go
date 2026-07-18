package query

import (
	"context"
	"database/sql"
	"errors"
	"fmt"

	"github.com/zanyoats/kukicha/internal/app"
)

var ErrTrackNotFound = errors.New("track not found")

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

	err := db.QueryRowContext(ctx, `
		SELECT
			roots.path,
			tracks.path,
			tracks.ext
		FROM library_tracks AS tracks
		JOIN library_roots AS roots
			ON roots.id = tracks.root_id
		WHERE tracks.id = ?
			AND tracks.file_type = ?;
	`, trackID, app.FileTypeAudio).Scan(
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
