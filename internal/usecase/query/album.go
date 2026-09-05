package query

import (
	"context"
	"database/sql"
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
)

var ErrAlbumNotFound = errors.New("album not found")

//go:embed sql/albums.sql
var albumsSQL string

//go:embed sql/album.sql
var albumSQL string

//go:embed sql/album_artists.sql
var albumArtistsSQL string

//go:embed sql/album_tracks.sql
var albumTracksSQL string

// AlbumSummary is the read model used to render an album in a collection.
type AlbumSummary struct {
	ID      int64
	Title   string
	Artists []string
}

// Album is the read model used to render an album and its ordered tracks.
type Album struct {
	ID                  int64
	Title               string
	Format              string
	DurationSeconds     int64
	ReleaseDate         *string
	ReleaseYear         *int
	OriginalReleaseDate *string
	OriginalReleaseYear *int
	ReleaseCountry      *string
	Label               *string
	Composer            *string
	Copyright           *string
	PurchaseDate        *string
	IsCompilation       *bool
	DiscCount           *int
	TrackCount          *int
	Artists             []string
	Tracks              []AlbumTrack
}

// AlbumTrack is a track in an album read model.
type AlbumTrack struct {
	ID              int64
	Title           string
	Artists         []string
	DurationSeconds int64
	DiscNumber      *int
	TrackNumber     *int
}

// Albums loads all albums and their artists in display order.
func Albums(
	ctx context.Context,
	db *sql.DB,
) ([]AlbumSummary, error) {
	rows, err := db.QueryContext(ctx, albumsSQL)
	if err != nil {
		return nil, fmt.Errorf("query albums: %w", err)
	}
	defer rows.Close()

	albums := make([]AlbumSummary, 0)

	for rows.Next() {
		var (
			album       AlbumSummary
			artistsJSON []byte
		)

		if err := rows.Scan(
			&album.ID,
			&album.Title,
			&artistsJSON,
		); err != nil {
			return nil, fmt.Errorf("scan album summary: %w", err)
		}

		if err := json.Unmarshal(
			artistsJSON,
			&album.Artists,
		); err != nil {
			return nil, fmt.Errorf(
				"decode artists for album %d: %w",
				album.ID,
				err,
			)
		}

		albums = append(albums, album)
	}

	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate album summaries: %w", err)
	}

	return albums, nil
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
	for _, track := range album.Tracks {
		album.DurationSeconds += track.DurationSeconds
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

	err := tx.QueryRowContext(
		ctx,
		albumSQL,
		sql.Named("album_id", albumID),
	).Scan(
		&album.ID,
		&album.Title,
		&album.Format,
		&album.ReleaseDate,
		&album.ReleaseYear,
		&album.OriginalReleaseDate,
		&album.OriginalReleaseYear,
		&album.ReleaseCountry,
		&album.Label,
		&album.Composer,
		&album.Copyright,
		&album.PurchaseDate,
		&album.IsCompilation,
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
	rows, err := tx.QueryContext(
		ctx,
		albumArtistsSQL,
		sql.Named("album_id", albumID),
	)
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
	rows, err := tx.QueryContext(
		ctx,
		albumTracksSQL,
		sql.Named("album_id", albumID),
	)
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
			&track.DurationSeconds,
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
