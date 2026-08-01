package query

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
)

var ErrAlbumArtworkNotFound = errors.New("album artwork not found")

type AlbumArtworkSize string

const (
	AlbumArtwork64  AlbumArtworkSize = "64"
	AlbumArtwork256 AlbumArtworkSize = "256"
)

type AlbumArtwork struct {
	ContentType string
	Bytes       []byte
}

func AlbumArtworkByID(
	ctx context.Context,
	db *sql.DB,
	albumID int64,
	size AlbumArtworkSize,
) (AlbumArtwork, error) {
	width, err := albumArtworkWidth(size)
	if err != nil {
		return AlbumArtwork{}, err
	}

	tx, err := db.BeginTx(ctx, &sql.TxOptions{ReadOnly: true})
	if err != nil {
		return AlbumArtwork{}, fmt.Errorf(
			"begin album artwork query: %w",
			err,
		)
	}
	defer tx.Rollback()

	var albumExists bool

	if err := tx.QueryRowContext(ctx, `
		SELECT EXISTS (
			SELECT 1
			FROM library_albums
			WHERE id = ?
		);
	`, albumID).Scan(&albumExists); err != nil {
		return AlbumArtwork{}, fmt.Errorf(
			"check album %d for artwork: %w",
			albumID,
			err,
		)
	}

	if !albumExists {
		return AlbumArtwork{}, fmt.Errorf(
			"query album artwork %d: %w",
			albumID,
			ErrAlbumNotFound,
		)
	}

	var (
		artwork     AlbumArtwork
		contentType sql.NullString
	)

	err = tx.QueryRowContext(ctx, `
		SELECT
			artwork.artwork_content_type,
			artwork.artwork_bytes
		FROM library_tracks AS tracks
		JOIN library_track_artwork AS artwork
			ON artwork.track_id = tracks.id
		WHERE tracks.album_id = ?
			AND artwork.width_px = ?
			AND artwork.artwork_content_size > 0
		ORDER BY
			COALESCE(tracks.disc_number, 1),
			CASE
				WHEN tracks.track_number IS NULL THEN 1
				ELSE 0
			END,
			tracks.track_number,
			tracks.path COLLATE BINARY,
			tracks.id,
			artwork.id
		LIMIT 1;
	`, albumID, width).Scan(
		&contentType,
		&artwork.Bytes,
	)

	if errors.Is(err, sql.ErrNoRows) {
		return AlbumArtwork{}, fmt.Errorf(
			"query album %d artwork at width %d: %w",
			albumID,
			width,
			ErrAlbumArtworkNotFound,
		)
	}
	if err != nil {
		return AlbumArtwork{}, fmt.Errorf(
			"query album %d artwork at width %d: %w",
			albumID,
			width,
			err,
		)
	}

	artwork.ContentType = contentType.String

	if err := tx.Commit(); err != nil {
		return AlbumArtwork{}, fmt.Errorf(
			"commit album artwork query: %w",
			err,
		)
	}

	return artwork, nil
}

func albumArtworkWidth(size AlbumArtworkSize) (int, error) {
	switch size {
	case AlbumArtwork64:
		return 64, nil

	case AlbumArtwork256:
		return 256, nil

	default:
		return 0, fmt.Errorf(
			"unsupported album artwork size %q",
			size,
		)
	}
}
