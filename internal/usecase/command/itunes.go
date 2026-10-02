package command

import (
	"context"
	"database/sql"
	_ "embed"
	"errors"
	"fmt"
	"time"

	"github.com/zanyoats/kukicha/internal/app"
)

//go:embed sql/store_itunes_lookup_image.sql
var storeITunesLookupImageSQL string

//go:embed sql/link_itunes_lookup_thumbnail.sql
var linkITunesLookupThumbnailSQL string

type itunesArtworkCacheWriter interface {
	ExecContext(
		context.Context,
		string,
		...any,
	) (sql.Result, error)
}

func StoreCachedITunesArtwork(
	ctx context.Context,
	db itunesArtworkCacheWriter,
	result app.ITunesLookupResult,
) error {
	artworkBytes := result.Bytes

	switch result.ResultKind {
	case app.ITunesResultHit:
		if len(artworkBytes) == 0 {
			return errors.New("cannot cache an empty iTunes artwork hit")
		}

	case app.ITunesResultMissing:
		if len(artworkBytes) != 0 {
			return errors.New(
				"cannot cache missing iTunes artwork with image data",
			)
		}
		// A nil []byte is stored as SQL NULL by database/sql, but the cache
		// intentionally represents a missing result as an empty, non-null BLOB.
		artworkBytes = []byte{}

	default:
		return fmt.Errorf(
			"bug: unsupported iTunes artwork result kind %q",
			result.ResultKind,
		)
	}

	_, err := db.ExecContext(
		ctx,
		storeITunesLookupImageSQL,
		sql.Named(
			"cache_key",
			app.ITunesLookupCacheKey(result.LookupKind, result.LookupID),
		),
		sql.Named("lookup_kind", string(result.LookupKind)),
		sql.Named("lookup_id", result.LookupID),
		sql.Named("result_kind", string(result.ResultKind)),
		sql.Named(
			"fetched_at",
			time.Now().UTC().Truncate(time.Second).Format(time.RFC3339),
		),
		sql.Named("lookup_url", result.LookupURL),
		sql.Named("artwork_url", result.ArtworkURL),
		sql.Named("artwork_content_type", result.ContentType),
		sql.Named("artwork_content_size", len(artworkBytes)),
		sql.Named("artwork_bytes", artworkBytes),
		sql.Named("thumbnail_cache_name", nil),
	)
	if err != nil {
		return fmt.Errorf("store cached iTunes artwork: %w", err)
	}

	return nil
}

func LinkCachedITunesArtworkThumbnail(
	ctx context.Context,
	db itunesArtworkCacheWriter,
	kind app.ITunesLookupKind,
	lookupID string,
	thumbnailCacheName string,
) error {
	if thumbnailCacheName == "" {
		return errors.New("cannot link an empty thumbnail cache name")
	}

	result, err := db.ExecContext(
		ctx,
		linkITunesLookupThumbnailSQL,
		sql.Named("cache_key", app.ITunesLookupCacheKey(kind, lookupID)),
		sql.Named("thumbnail_cache_name", thumbnailCacheName),
	)
	if err != nil {
		return fmt.Errorf("link cached iTunes artwork thumbnail: %w", err)
	}

	rowsAffected, err := result.RowsAffected()
	if err != nil {
		return fmt.Errorf("count linked iTunes artwork thumbnails: %w", err)
	}
	if rowsAffected != 1 {
		return fmt.Errorf(
			"link cached iTunes artwork thumbnail: expected 1 row, updated %d",
			rowsAffected,
		)
	}

	return nil
}
