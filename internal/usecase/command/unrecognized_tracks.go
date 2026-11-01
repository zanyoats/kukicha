package command

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"strings"

	"github.com/zanyoats/kukicha/internal/app"
)

var ErrInvalidTrackIdentity = errors.New("Enter an album and at least one artist, without unknown placeholders. Each value must be on a single line.")
var ErrUnrecognizedTrackNotFound = errors.New("This track is no longer awaiting identification. Refresh the list; it may already have been rebuilt.")

func UpdateUnrecognizedTrack(ctx context.Context, db *sql.DB, uploadedPath, album string, artists []string) error {
	album = strings.TrimSpace(album)
	if album == "" || album == app.UnknownItem || strings.ContainsAny(album, "\r\n\x00") {
		return ErrInvalidTrackIdentity
	}
	cleaned, err := cleanAndValidateArtistMapping(uploadedPath, artists)
	if err != nil {
		return ErrInvalidTrackIdentity
	}
	for _, artist := range cleaned {
		if artist == app.UnknownItem || strings.ContainsRune(artist, '\x00') {
			return ErrInvalidTrackIdentity
		}
	}
	encoded, err := json.Marshal(cleaned)
	if err != nil {
		return err
	}
	result, err := db.ExecContext(ctx, `UPDATE unrecognized_tracks SET album = ?, artists = ?
        WHERE uploaded_path = ?`, album, string(encoded), uploadedPath)
	if err != nil {
		return err
	}
	count, err := result.RowsAffected()
	if err != nil {
		return err
	}
	if count == 0 {
		return ErrUnrecognizedTrackNotFound
	}
	return nil
}
