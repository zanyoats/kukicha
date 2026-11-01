package command

import (
	"context"
	"database/sql"
	_ "embed"
	"errors"
)

var ErrInvalidScrobble = errors.New("invalid scrobble")
var ErrScrobbleTrackNotFound = errors.New("scrobble track not found")

//go:embed sql/record_scrobble.sql
var recordScrobbleSQL string

//go:embed sql/record_album_played_at.sql
var recordAlbumPlayedAtSQL string

//go:embed sql/record_now_playing.sql
var recordNowPlayingSQL string

//go:embed sql/scrobble_track_exists.sql
var scrobbleTrackExistsSQL string

// ScrobbleRequest uses a decimal string ID and a Unix millisecond start time.
// Submission is a pointer to distinguish false from an omitted field.
type ScrobbleRequest struct {
	ID         int64 `json:"id,string"`
	Time       int64 `json:"time"`
	Submission *bool `json:"submission"`
}

func RecordScrobble(ctx context.Context, db *sql.DB, input ScrobbleRequest) error {
	if input.ID <= 0 || input.Time <= 0 || input.Submission == nil {
		return ErrInvalidScrobble
	}
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	var exists bool
	// Accept retries even if the track has since disappeared from the library.
	err = tx.QueryRowContext(ctx, scrobbleTrackExistsSQL,
		input.ID, input.ID, input.Time, input.ID, input.Time).Scan(&exists)
	if err != nil {
		return err
	}
	if !exists {
		return ErrScrobbleTrackNotFound
	}
	if *input.Submission {
		if _, err = tx.ExecContext(ctx, recordScrobbleSQL, input.ID, input.Time); err != nil {
			return err
		}
		if _, err = tx.ExecContext(ctx, recordAlbumPlayedAtSQL,
			sql.Named("track_id", input.ID), sql.Named("played_at", input.Time)); err != nil {
			return err
		}
	}
	if _, err = tx.ExecContext(ctx, recordNowPlayingSQL, input.ID, input.Time, *input.Submission); err != nil {
		return err
	}
	return tx.Commit()
}
