package command

import (
	"context"
	"database/sql"
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"

	"github.com/zanyoats/kukicha/internal/app"
	"github.com/zanyoats/kukicha/internal/metadata"
	"github.com/zanyoats/kukicha/internal/usecase/query"
)

//go:embed sql/prepare_scan_results.sql
var prepareScanResultsSQL string

//go:embed sql/prepare_scan_results_from_library.sql
var prepareScanResultsFromLibrarySQL string

//go:embed sql/stage_unrecognized_scan_tracks.sql
var stageUnrecognizedScanTracksSQL string

// PrepareScanResultsFromLibrary stages raw tracks and a snapshot of album overrides.
// Normalization runs after any new files have also been staged.
func PrepareScanResultsFromLibrary(ctx context.Context, conn *sql.Conn, conf *app.AppConfig) error {
	if err := prepareScanResults(ctx, conn, conf.Settings.Server.Database.CacheSizeKiB); err != nil {
		return err
	}
	if err := copyLibraryToScan(ctx, conn, conf); err != nil {
		return err
	}
	if _, err := conn.ExecContext(ctx, stageUnrecognizedScanTracksSQL); err != nil {
		return fmt.Errorf("stage ready unrecognized tracks: %w", err)
	}
	return stageAlbumScanOverrides(ctx, conn, conf)
}

// NormalizeScanTracks inserts one normalized row per staged track using SQL.
// Raw tags, provider values, and album identity remain unchanged.
// Saved artist mappings are validated by UpdateArtistRule when written.
func NormalizeScanTracks(ctx context.Context, conn *sql.Conn, conf *app.AppConfig) error {
	delimiters, err := json.Marshal(conf.Settings.Scan.PrimaryGenreSplitTokens)
	if err != nil {
		return err
	}
	aliases, err := json.Marshal(conf.Settings.Scan.GenreStyleAliases)
	if err != nil {
		return err
	}
	_, err = conn.ExecContext(ctx, withGenreSplitter(normalizeScanTracksSQL),
		sql.Named("split_delimiters", string(delimiters)),
		sql.Named("aliases", string(aliases)),
		sql.Named("unicode_whitespace", normalizationWhitespace),
		sql.Named("unknown_item", app.UnknownItem))
	return err
}

func prepareScanResults(
	ctx context.Context,
	conn *sql.Conn,
	cacheSizeKiB int64,
) error {
	if cacheSizeKiB < 1 {
		return errors.New("temporary database cache size must be positive")
	}

	if _, err := conn.ExecContext(ctx, "PRAGMA temp_store = FILE;"); err != nil {
		return fmt.Errorf("configure temporary database storage: %w", err)
	}

	cacheSizePragma := fmt.Sprintf(
		"PRAGMA temp.cache_size = -%d;",
		cacheSizeKiB,
	)
	if _, err := conn.ExecContext(ctx, cacheSizePragma); err != nil {
		return fmt.Errorf("configure temporary database cache: %w", err)
	}

	_, err := conn.ExecContext(ctx, prepareScanResultsSQL)

	return err
}

// copyLibraryToScan copies saved resources, including unavailable roots, into scan tables.
// Roots pending removal and their resources are excluded.
func copyLibraryToScan(ctx context.Context, conn *sql.Conn, conf *app.AppConfig) error {
	rootIDs := make([]string, 0, len(conf.Roots))
	for _, root := range conf.Roots {
		rootIDs = append(rootIDs, root.ConfigID())
	}
	encodedRoots, err := json.Marshal(rootIDs)
	if err != nil {
		return err
	}
	if _, err := conn.ExecContext(ctx, prepareScanResultsFromLibrarySQL,
		sql.Named("root_config_ids", string(encodedRoots)),
	); err != nil {
		return fmt.Errorf("stage current library: %w", err)
	}
	return nil
}

// stageAlbumScanOverrides processes each distinct source once, keeping only one result
// in memory. Sources were snapshotted before this loop; failed sources stay absent
// from scan_album_overrides, allowing ordinary metadata and artwork to apply.
func stageAlbumScanOverrides(ctx context.Context, conn *sql.Conn, conf *app.AppConfig) error {
	configs := map[string]app.ProviderLookupConfig{
		"discogs":     conf.Settings.Scan.Discogs,
		"musicbrainz": conf.Settings.Scan.MusicBrainz,
	}
	clients := map[string]*metadata.Client{
		"discogs":     metadata.NewClient(configs["discogs"], conf.Settings.Scan.LocalArtworkMaxBytes),
		"musicbrainz": metadata.NewClient(configs["musicbrainz"], conf.Settings.Scan.LocalArtworkMaxBytes),
	}
	var afterProvider, afterType, afterID string
	for {
		if err := ctx.Err(); err != nil {
			return err
		}
		var source app.MetadataSource
		var albumKey string
		err := conn.QueryRowContext(ctx, `
   SELECT provider, entity_type, entity_id, MIN(source_url), MIN(album_key)
   FROM scan_album_override_sources
   WHERE (provider, entity_type, entity_id) > (?, ?, ?)
   GROUP BY provider, entity_type, entity_id
   ORDER BY provider, entity_type, entity_id LIMIT 1`, afterProvider, afterType, afterID).
			Scan(&source.Provider, &source.EntityType, &source.EntityID, &source.SourceURL, &albumKey)
		if errors.Is(err, sql.ErrNoRows) {
			return nil
		}
		if err != nil {
			return err
		}
		afterProvider, afterType, afterID = source.Provider, source.EntityType, source.EntityID
		client := clients[source.Provider]
		enabled := configs[source.Provider].Enabled
		value, cached, err := query.CachedMetadataEntity(ctx, conn, source)
		if err != nil {
			return err
		}
		if !cached {
			if !enabled {
				continue
			}
			value, err = client.Fetch(ctx, source)
			if err != nil {
				if ctx.Err() != nil {
					return ctx.Err()
				}
				app.JobLogger(ctx).ErrorContext(ctx, "album metadata override fetch failed; using fallbacks", "album_key", albumKey, "source_url", source.SourceURL, "error", err)
				continue
			}
			if err := StoreMetadataEntity(ctx, conn, value); err != nil {
				return err
			}
		}
		// Repair a missing thumbnail pair without fetching metadata again.
		if value.ThumbnailCacheName != "" {
			var count int
			if err := conn.QueryRowContext(ctx, `SELECT count(*) FROM thumbnail_cache WHERE name=? AND width_px IN (64,256)`, value.ThumbnailCacheName).Scan(&count); err != nil {
				return err
			}
			if count != 2 {
				value.ThumbnailCacheName = ""
				value.ArtworkComplete = false
			}
		}
		if !value.ArtworkComplete && enabled {
			data, missing, artworkErr := client.Artwork(ctx, value.ArtworkURL)
			if artworkErr == nil && !missing {
				value.ThumbnailCacheName, artworkErr = CacheArtworkThumbnails(ctx, conn, data, "external album cover", "", source.SourceURL)
				if artworkErr == nil && value.ThumbnailCacheName == "" {
					artworkErr = fmt.Errorf("cover could not be resized")
				}
			}
			if ctx.Err() != nil {
				return ctx.Err()
			}
			if artworkErr != nil {
				app.JobLogger(ctx).ErrorContext(ctx, "album override artwork failed; using ordinary artwork", "album_key", albumKey, "source_url", source.SourceURL, "error", artworkErr)
			} else {
				value.ArtworkComplete = true
			}
			if err := StoreMetadataEntity(ctx, conn, value); err != nil {
				return err
			}
		}
		genres, err := json.Marshal(value.Genres)
		if err != nil {
			return err
		}
		if _, err := conn.ExecContext(ctx, `
   INSERT INTO scan_album_overrides (album_key, provider, source_url, genres_json, thumbnail_cache_name)
   SELECT album_key, provider, source_url, ?, NULLIF(?, '')
   FROM scan_album_override_sources WHERE provider = ? AND entity_type = ? AND entity_id = ?`,
			string(genres), value.ThumbnailCacheName, source.Provider, source.EntityType, source.EntityID,
		); err != nil {
			return err
		}
	}
}
