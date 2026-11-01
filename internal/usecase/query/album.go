package query

import (
	"context"
	"database/sql"
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"strings"

	"github.com/zanyoats/kukicha/internal/app"
)

var ErrAlbumNotFound = errors.New("album not found")

//go:embed sql/albums.sql
var albumsSQL string

//go:embed sql/album_filter_names.sql
var albumFilterNamesSQL string

//go:embed sql/album.sql
var albumSQL string

//go:embed sql/album_artists.sql
var albumArtistsSQL string

//go:embed sql/album_tracks.sql
var albumTracksSQL string

//go:embed sql/album_genres.sql
var albumGenresSQL string

//go:embed sql/album_styles.sql
var albumStylesSQL string

// AlbumCategory is a genre or style associated with an album.
type AlbumCategory struct {
	ID   int64
	Name string
}

// AlbumArtist identifies an artist associated with an album.
type AlbumArtist struct {
	ID   int64  `json:"id"`
	Name string `json:"name"`
}

// AlbumSummary is the read model used to render an album in a collection.
type AlbumSummary struct {
	AlbumKey     string
	OverrideURL  string
	ID           int64
	Title        string
	Artists      []AlbumArtist
	SmallArtwork ArtworkDimensions
	LargeArtwork ArtworkDimensions
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
	Artists             []AlbumArtist
	Genres              []AlbumCategory
	Styles              []AlbumCategory
	Tracks              []AlbumTrack
	SmallArtwork        ArtworkDimensions
	LargeArtwork        ArtworkDimensions
}

// AlbumTrack is a track in an album read model.
type AlbumTrack struct {
	ID              int64
	RootConfigID    string
	Title           string
	Artists         []string
	DurationSeconds int64
	DiscNumber      *int
	TrackNumber     *int
}

// AlbumFilters restricts albums by ID and matching relationships. Zero means no filter.
// Supplied filters are combined with AND.
type AlbumFilters struct {
	AlbumID  int64
	ArtistID int64
	GenreID  int64
	StyleID  int64
}

// AlbumFilterNames contains the names of selected filters. Missing IDs have nil names.
type AlbumFilterNames struct {
	Artist *string
	Genre  *string
	Style  *string
}

// NamesForAlbumFilters resolves filter names independently of matching albums.
func NamesForAlbumFilters(ctx context.Context, db *sql.DB, filters AlbumFilters) (AlbumFilterNames, error) {
	var names AlbumFilterNames
	if filters == (AlbumFilters{}) {
		return names, nil
	}
	err := db.QueryRowContext(ctx, albumFilterNamesSQL,
		sql.Named("artist_id", filters.ArtistID),
		sql.Named("genre_id", filters.GenreID),
		sql.Named("style_id", filters.StyleID),
	).Scan(&names.Artist, &names.Genre, &names.Style)
	if err != nil {
		return AlbumFilterNames{}, fmt.Errorf("query album filter names: %w", err)
	}
	return names, nil
}

// AlbumPage contains the requested albums and whether another page follows.
type AlbumPage struct {
	Albums  []AlbumSummary
	HasNext bool
}

// Albums selects a page before loading artwork and complete artist lists.
func Albums(
	ctx context.Context,
	db *sql.DB,
	filters AlbumFilters,
	sort app.AlbumSort,
	perPage int,
	offset int,
) (AlbumPage, error) {
	if perPage < 1 || perPage == math.MaxInt {
		return AlbumPage{}, fmt.Errorf("invalid album page size: %d", perPage)
	}
	if offset < 0 {
		return AlbumPage{}, errors.New("album offset must be non-negative")
	}

	// Only trusted ordering clauses are substituted; request values are bound.
	var order string
	switch sort {
	case app.AlbumSortRecentlyAdded:
		order = "albums.added_at DESC, albums.id DESC"
	case app.AlbumSortAlbum:
		order = "albums.album COLLATE NOCASE, albums.id"
	case app.AlbumSortArtistAlbum:
		order = "albums.first_artist COLLATE NOCASE, albums.album COLLATE NOCASE, albums.id"
	default:
		return AlbumPage{}, fmt.Errorf("bug: unsupported album sort %q", sort)
	}
	smallWidth, err := albumArtworkWidth(AlbumArtwork64)
	if err != nil {
		return AlbumPage{}, err
	}
	largeWidth, err := albumArtworkWidth(AlbumArtwork256)
	if err != nil {
		return AlbumPage{}, err
	}
	rows, err := db.QueryContext(ctx, strings.ReplaceAll(albumsSQL, "{{order}}", order),
		sql.Named("artist_id", filters.ArtistID),
		sql.Named("genre_id", filters.GenreID),
		sql.Named("style_id", filters.StyleID),
		sql.Named("per_page", perPage),
		sql.Named("candidate_limit", perPage+1),
		sql.Named("offset", offset),
		sql.Named("artwork_width_small", smallWidth),
		sql.Named("artwork_width_large", largeWidth),
	)
	if err != nil {
		return AlbumPage{}, fmt.Errorf("query albums: %w", err)
	}
	defer rows.Close()

	page := AlbumPage{Albums: make([]AlbumSummary, 0)}
	for rows.Next() {
		var album AlbumSummary
		var artistsJSON []byte
		if err := rows.Scan(
			&album.ID,
			&album.Title,
			&album.AlbumKey,
			&album.OverrideURL,
			&page.HasNext,
			&album.SmallArtwork.Width,
			&album.SmallArtwork.Height,
			&album.LargeArtwork.Width,
			&album.LargeArtwork.Height,
			&artistsJSON,
		); err != nil {
			return AlbumPage{}, fmt.Errorf("scan album summary: %w", err)
		}
		if err := json.Unmarshal(artistsJSON, &album.Artists); err != nil {
			return AlbumPage{}, fmt.Errorf("decode artists for album %d: %w", album.ID, err)
		}
		page.Albums = append(page.Albums, album)
	}
	if err := rows.Err(); err != nil {
		return AlbumPage{}, fmt.Errorf("iterate album summaries: %w", err)
	}
	return page, nil
}

// AlbumByFilters loads the selected album and its ordered tracks in a consistent
// read transaction. AlbumID is required; all supplied filters must match.
func AlbumByFilters(
	ctx context.Context,
	db *sql.DB,
	filters AlbumFilters,
) (Album, error) {
	tx, err := db.BeginTx(ctx, &sql.TxOptions{ReadOnly: true})
	if err != nil {
		return Album{}, fmt.Errorf("begin album query: %w", err)
	}
	defer tx.Rollback()

	album, err := queryAlbum(ctx, tx, filters)
	if err != nil {
		return Album{}, err
	}

	album.SmallArtwork, err = queryAlbumArtworkDimensions(ctx, tx, filters.AlbumID, AlbumArtwork64)
	if err != nil {
		return Album{}, err
	}
	album.LargeArtwork, err = queryAlbumArtworkDimensions(ctx, tx, filters.AlbumID, AlbumArtwork256)
	if err != nil {
		return Album{}, err
	}

	album.Artists, err = queryAlbumArtists(ctx, tx, filters.AlbumID)
	if err != nil {
		return Album{}, err
	}

	album.Genres, err = queryAlbumCategories(ctx, tx, filters.AlbumID, albumGenresSQL, "genres")
	if err != nil {
		return Album{}, err
	}

	album.Styles, err = queryAlbumCategories(ctx, tx, filters.AlbumID, albumStylesSQL, "styles")
	if err != nil {
		return Album{}, err
	}

	album.Tracks, err = queryAlbumTracks(ctx, tx, filters.AlbumID)
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
	filters AlbumFilters,
) (Album, error) {
	album := Album{
		Artists: make([]AlbumArtist, 0),
		Tracks:  make([]AlbumTrack, 0),
	}

	err := tx.QueryRowContext(
		ctx,
		albumSQL,
		sql.Named("album_id", filters.AlbumID),
		sql.Named("artist_id", filters.ArtistID),
		sql.Named("genre_id", filters.GenreID),
		sql.Named("style_id", filters.StyleID),
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
	)
	if errors.Is(err, sql.ErrNoRows) {
		return Album{}, fmt.Errorf(
			"query album %d: %w",
			filters.AlbumID,
			ErrAlbumNotFound,
		)
	}
	if err != nil {
		return Album{}, fmt.Errorf(
			"query album %d: %w",
			filters.AlbumID,
			err,
		)
	}

	return album, nil
}

func queryAlbumArtists(
	ctx context.Context,
	tx *sql.Tx,
	albumID int64,
) ([]AlbumArtist, error) {
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

	artists := make([]AlbumArtist, 0)

	for rows.Next() {
		var artist AlbumArtist

		if err := rows.Scan(&artist.ID, &artist.Name); err != nil {
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

func queryAlbumCategories(
	ctx context.Context,
	tx *sql.Tx,
	albumID int64,
	statement string,
	kind string,
) ([]AlbumCategory, error) {
	rows, err := tx.QueryContext(ctx, statement, sql.Named("album_id", albumID))
	if err != nil {
		return nil, fmt.Errorf("query %s for album %d: %w", kind, albumID, err)
	}
	defer rows.Close()

	items := make([]AlbumCategory, 0)
	for rows.Next() {
		var item AlbumCategory
		if err := rows.Scan(&item.ID, &item.Name); err != nil {
			return nil, fmt.Errorf("scan %s for album %d: %w", kind, albumID, err)
		}
		items = append(items, item)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate %s for album %d: %w", kind, albumID, err)
	}
	return items, nil
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
			&track.RootConfigID,
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
