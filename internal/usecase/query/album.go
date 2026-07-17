package query

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
)

var ErrAlbumNotFound = errors.New("album not found")

// Album is the read model used to render an album and its ordered tracks.
type Album struct {
	ID          int64
	Title       string
	Format      string
	ReleaseDate *string
	ReleaseYear *int
	DiscCount   *int
	TrackCount  *int
	Artists     []string
	Tracks      []AlbumTrack
}

// AlbumTrack is a track in an album read model.
type AlbumTrack struct {
	ID          int64
	Title       string
	Artists     []string
	DiscNumber  *int
	TrackNumber *int
}

// AlbumByID loads an album and its ordered tracks in a consistent read
// transaction.
func AlbumByID(
	ctx context.Context,
	db *sql.DB,
	albumID int64,
) (Album, error) {
	tx, err := db.BeginTx(ctx, &sql.TxOptions{ReadOnly: true})
	if err != nil {
		return Album{}, fmt.Errorf("begin album query: %w", err)
	}
	defer tx.Rollback()

	album, err := queryAlbum(ctx, tx, albumID)
	if err != nil {
		return Album{}, err
	}

	album.Artists, err = queryAlbumArtists(ctx, tx, albumID)
	if err != nil {
		return Album{}, err
	}

	album.Tracks, err = queryAlbumTracks(ctx, tx, albumID)
	if err != nil {
		return Album{}, err
	}

	if err := tx.Commit(); err != nil {
		return Album{}, fmt.Errorf("commit album query: %w", err)
	}

	return album, nil
}

func queryAlbum(
	ctx context.Context,
	tx *sql.Tx,
	albumID int64,
) (Album, error) {
	album := Album{
		Artists: make([]string, 0),
		Tracks:  make([]AlbumTrack, 0),
	}

	err := tx.QueryRowContext(ctx, `
		SELECT
			id,
			album,
			format,
			release_date,
			release_year,
			disc_count,
			track_count
		FROM library_albums
		WHERE id = ?;
	`, albumID).Scan(
		&album.ID,
		&album.Title,
		&album.Format,
		&album.ReleaseDate,
		&album.ReleaseYear,
		&album.DiscCount,
		&album.TrackCount,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return Album{}, fmt.Errorf(
			"query album %d: %w",
			albumID,
			ErrAlbumNotFound,
		)
	}
	if err != nil {
		return Album{}, fmt.Errorf(
			"query album %d: %w",
			albumID,
			err,
		)
	}

	return album, nil
}

func queryAlbumArtists(
	ctx context.Context,
	tx *sql.Tx,
	albumID int64,
) ([]string, error) {
	rows, err := tx.QueryContext(ctx, `
		SELECT artist
		FROM library_album_artists
		WHERE album_id = ?
		ORDER BY artist COLLATE NOCASE, artist_key, id;
	`, albumID)
	if err != nil {
		return nil, fmt.Errorf(
			"query artists for album %d: %w",
			albumID,
			err,
		)
	}
	defer rows.Close()

	artists := make([]string, 0)

	for rows.Next() {
		var artist string

		if err := rows.Scan(&artist); err != nil {
			return nil, fmt.Errorf(
				"scan artist for album %d: %w",
				albumID,
				err,
			)
		}

		artists = append(artists, artist)
	}

	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf(
			"iterate artists for album %d: %w",
			albumID,
			err,
		)
	}

	return artists, nil
}

func queryAlbumTracks(
	ctx context.Context,
	tx *sql.Tx,
	albumID int64,
) ([]AlbumTrack, error) {
	rows, err := tx.QueryContext(ctx, `
		SELECT
			id,
			title,
			artists,
			disc_number,
			track_number
		FROM library_tracks
		WHERE album_id = ?
		ORDER BY
			COALESCE(disc_number, 1),
			CASE WHEN track_number IS NULL THEN 1 ELSE 0 END,
			track_number,
			path COLLATE BINARY,
			id;
	`, albumID)
	if err != nil {
		return nil, fmt.Errorf(
			"query tracks for album %d: %w",
			albumID,
			err,
		)
	}
	defer rows.Close()

	tracks := make([]AlbumTrack, 0)

	for rows.Next() {
		var (
			track       AlbumTrack
			artistsJSON []byte
		)

		if err := rows.Scan(
			&track.ID,
			&track.Title,
			&artistsJSON,
			&track.DiscNumber,
			&track.TrackNumber,
		); err != nil {
			return nil, fmt.Errorf(
				"scan track for album %d: %w",
				albumID,
				err,
			)
		}

		if err := json.Unmarshal(artistsJSON, &track.Artists); err != nil {
			return nil, fmt.Errorf(
				"decode artists for track %d in album %d: %w",
				track.ID,
				albumID,
				err,
			)
		}

		tracks = append(tracks, track)
	}

	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf(
			"iterate tracks for album %d: %w",
			albumID,
			err,
		)
	}

	return tracks, nil
}
