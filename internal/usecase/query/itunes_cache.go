package query

import (
	"context"
	"database/sql"
	_ "embed"
	"errors"
	"fmt"

	"github.com/zanyoats/kukicha/internal/app"
)

var ErrInvalidCachedITunesArtwork = errors.New("fetch from itunes cache")

//go:embed sql/load_itunes_lookup_image.sql
var loadITunesLookupImageSQL string

type itunesArtworkCacheReader interface {
	QueryRowContext(
		context.Context,
		string,
		...any,
	) *sql.Row
}

func LoadCachedITunesArtwork(
	ctx context.Context,
	db itunesArtworkCacheReader,
	kind app.ITunesLookupKind,
	lookupID string,
) (app.ITunesLookupResult, bool, error) {
	result := app.ITunesLookupResult{
		LookupKind: kind,
		LookupID:   lookupID,
	}

	var (
		storedKind       string
		storedLookupID   string
		storedResultKind string
		contentSize      int
	)

	err := db.QueryRowContext(
		ctx,
		loadITunesLookupImageSQL,
		sql.Named("cache_key", app.ITunesLookupCacheKey(kind, lookupID)),
	).Scan(
		&storedKind,
		&storedLookupID,
		&storedResultKind,
		&result.LookupURL,
		&result.ArtworkURL,
		&result.ContentType,
		&contentSize,
		&result.Bytes,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return result, false, nil
	}
	if err != nil {
		return result, false, fmt.Errorf(
			"load cached iTunes artwork: %w",
			err,
		)
	}

	if storedKind != string(kind) || storedLookupID != lookupID {
		return result, false, fmt.Errorf(
			"%w: %w",
			ErrInvalidCachedITunesArtwork, errors.New(
				"cached iTunes lookup identity does not match its cache key",
			))
	}

	result.ResultKind = app.ITunesResultKind(storedResultKind)

	if contentSize != len(result.Bytes) {
		return result, false, fmt.Errorf(
			"%w: %w",
			ErrInvalidCachedITunesArtwork, errors.New(
				"cached iTunes artwork size does not match its data",
			))
	}

	switch result.ResultKind {
	case app.ITunesResultHit:
		if len(result.Bytes) == 0 {
			return result, false, fmt.Errorf(
				"%w: %w",
				ErrInvalidCachedITunesArtwork, errors.New(
					"cached iTunes artwork hit is empty",
				))
		}

	case app.ITunesResultMissing:
		if len(result.Bytes) != 0 {
			return result, false, fmt.Errorf(
				"%w: %w",
				ErrInvalidCachedITunesArtwork, errors.New(
					"cached missing iTunes artwork contains image data",
				))
		}

	default:
		return result, false, fmt.Errorf(
			"%w: %w",
			ErrInvalidCachedITunesArtwork,
			fmt.Errorf(
				"unsupported cached iTunes result kind %q", result.ResultKind,
			))
	}

	return result, true, nil
}
