package command

import (
	"context"
	"database/sql"
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"slices"

	"github.com/zanyoats/kukicha/internal/app"
)

//go:embed sql/prepare_scan_results.sql
var prepareScanResultsSQL string

//go:embed sql/library_counts.sql
var libraryCountsSQL string

//go:embed sql/insert_scanned_file.sql
var insertScannedFileSQL string

//go:embed sql/insert_scanned_artwork.sql
var insertScannedArtworkSQL string

//go:embed sql/insert_scanned_artwork_cover.sql
var insertScannedArtworkCoverSQL string

//go:embed sql/insert_scanned_sidecar_cover.sql
var insertScannedSidecarCoverSQL string

//go:embed sql/insert_scanned_sidecar_other.sql
var insertScannedSidecarOtherSQL string

//go:embed sql/find_or_insert_mapping.sql
var findOrInsertMappingSQL string

//go:embed sql/reset_library.sql
var resetLibrarySQL string

//go:embed sql/build_library_roots.sql
var buildLibraryRootsSQL string

//go:embed sql/build_library_albums.sql
var buildLibraryAlbumsSQL string

//go:embed sql/build_library_album_artists.sql
var buildLibraryAlbumArtistsSQL string

//go:embed sql/build_library_tracks.sql
var buildLibraryTracksSQL string

//go:embed sql/build_library_track_embedded_artwork.sql
var buildLibraryTrackEmbeddedArtworkSQL string

//go:embed sql/build_library_track_genres_and_styles.sql
var buildLibraryTrackGenresAndStylesSQL string

//go:embed sql/genre_resolution_results.sql
var genreResolutionResultsSQL string

//go:embed sql/build_library_track_sidecars.sql
var buildLibraryTrackSidecarsSQL string

//go:embed sql/insert_library_track_artwork.sql
var insertLibraryTrackArtworkSQL string

//go:embed sql/current_timestamp.sql
var currentTimestampSQL string

func PrepareScanResults(ctx context.Context, conn *sql.Conn) error {
	_, err := conn.ExecContext(ctx, prepareScanResultsSQL)

	return err
}

type GenreResolutionResult struct {
	TrackId    int
	Path       string
	Status     string
	LogMessage string
}

type BuildLibraryResult struct {
	LibraryRoots                    int
	LibraryAlbums                   int
	LibraryTracks                   int
	LibraryPlaylists                int
	LibraryTrackSidecars            int
	LibraryTrackTitleUnknowns       int
	LibraryTrackAlbumUnknowns       int
	LibraryTrackAlbumArtistUnknowns int
	LibraryTrackScanErrors          int
	GenreResolutionResult           []GenreResolutionResult
}

func BuildLibraryFromScan(
	ctx context.Context,
	conn *sql.Conn,
	recognizedAudioExtensions map[string]bool,
	recognizedPlaylistExtensions map[string]bool,
	recognizedSidecarExtensions map[string]bool,
	unknownItem string,
	secondaryGenreSplitTokens map[string]bool,
) (BuildLibraryResult, error) {
	var result BuildLibraryResult
	var err error

	var tx *sql.Tx

	if tx, err = conn.BeginTx(ctx, nil); err != nil {
		return result, err
	}

	if err = resetLibrary(ctx, tx); err != nil {
		_ = tx.Rollback()
		return result, err
	}

	if err = buildLibraryRoots(ctx, tx); err != nil {
		_ = tx.Rollback()
		return result, err
	}

	if err = buildLibraryAlbums(ctx, tx); err != nil {
		_ = tx.Rollback()
		return result, err
	}

	if err = buildLibraryAlbumArtists(ctx, tx); err != nil {
		_ = tx.Rollback()
		return result, err
	}

	if err = buildLibraryTracks(ctx, tx); err != nil {
		_ = tx.Rollback()
		return result, err
	}

	if err = buildLibraryTrackEmbeddedArtwork(ctx, tx); err != nil {
		_ = tx.Rollback()
		return result, err
	}

	if result.GenreResolutionResult, err = buildLibraryTrackGenresAndStyles(
		ctx, tx,
		unknownItem,
		secondaryGenreSplitTokens,
	); err != nil {
		_ = tx.Rollback()
		return result, err
	}

	if err = buildLibraryTrackSidecars(ctx, tx); err != nil {
		_ = tx.Rollback()
		return result, err
	}

	if err = buildLibraryTrackArtwork(ctx, tx); err != nil {
		_ = tx.Rollback()
		return result, err
	}

	if err = setBuildLibraryAppMetadata(
		ctx, tx,
		recognizedAudioExtensions,
		recognizedPlaylistExtensions,
		recognizedSidecarExtensions,
	); err != nil {
		_ = tx.Rollback()
		return result, err
	}

	err = tx.QueryRowContext(ctx, libraryCountsSQL).Scan(
		&result.LibraryRoots,
		&result.LibraryAlbums,
		&result.LibraryTracks,
	)
	if err != nil {
		_ = tx.Rollback()
		return result, err
	}

	return result, tx.Commit()
}

type ITunesArtworkLookup func(
	context.Context,
	app.ITunesLookupKind,
	string,
) (app.ITunesLookupResult, bool, error)

func InsertScannedFile(
	ctx context.Context,
	conn *sql.Conn,
	r *app.ScannedFile,
	itunesLookup ITunesArtworkLookup,
) error {
	switch r.FileType {
	case app.FileTypeAudio:
		return insertScannedTrack(ctx, conn, r, itunesLookup)

	case app.FileTypeSidecarCover:
		return insertScannedSidecarCover(ctx, conn, r)

	case app.FileTypeSidecarOther:
		return insertScannedSidecarOther(ctx, conn, r)

	case app.FileTypePlaylist:
		return nil

	default:
		return fmt.Errorf("unsupported scanned file type %d", r.FileType)
	}
}

func insertScannedTrack(
	ctx context.Context,
	conn *sql.Conn,
	r *app.ScannedFile,
	itunesLookup ITunesArtworkLookup,
) error {
	if r.Metadata == nil || r.NormalizedMetadata == nil {
		return errors.New("scanned audio track is missing metadata")
	}

	b, err := json.Marshal(r.Metadata)
	if err != nil {
		return err
	}
	metadata := string(b)

	b, err = json.Marshal(r.NormalizedMetadata)
	if err != nil {
		return err
	}
	normalizedMetadata := string(b)

	_, err = conn.ExecContext(
		ctx,
		insertScannedFileSQL,
		sql.Named("root_pos", r.RootPos),
		sql.Named("root_path", r.RootPath),
		sql.Named("album_key", r.NormalizedMetadata.AlbumKey),
		sql.Named("path_dir", filepath.Dir(r.Path)),
		sql.Named("stem", r.Stem),
		sql.Named("ext", r.Ext),
		sql.Named("path", r.Path),
		sql.Named("file_size", r.FileSize),
		sql.Named("file_mtime_unix_nano", r.FileModTime.UnixNano()),
		sql.Named("track_metadata", metadata),
		sql.Named("normalized_metadata", normalizedMetadata),
		sql.Named("scan_error", r.ScanError),
	)
	if err != nil {
		return err
	}

	for artworkPos, artwork := range r.Artwork {
		_, err = conn.ExecContext(
			ctx,
			insertScannedArtworkSQL,
			sql.Named("root_pos", r.RootPos),
			sql.Named("path", r.Path),
			sql.Named("artwork_pos", artworkPos),
			sql.Named("picture_type", artwork.PictureType),
			sql.Named("artwork_content_type", artwork.ArtworkContentType),
			sql.Named("artwork_content_size", artwork.ArtworkContentSize),
			sql.Named("artwork_bytes", artwork.ArtworkBytes),
		)
		if err != nil {
			return err
		}

		thumbnailCacheName := ""
		if artwork.PictureType == "front_cover" || artwork.PictureType == "other" {
			thumbnailCacheName, err = cacheArtworkForScan(
				ctx,
				conn,
				fmt.Sprintf("embedded artwork %q position %d", r.Path, artworkPos),
				artwork.ArtworkBytes,
			)
			if err != nil {
				return err
			}
		}

		_, err = conn.ExecContext(
			ctx,
			insertScannedArtworkCoverSQL,
			sql.Named("root_pos", r.RootPos),
			sql.Named("path", r.Path),
			sql.Named("artwork_pos", artworkPos),
			sql.Named("picture_type", artwork.PictureType),
			sql.Named(
				"thumbnail_cache_name",
				nullableThumbnailCacheName(thumbnailCacheName),
			),
		)
		if err != nil {
			return err
		}
	}

	return cacheScannedFileITunesArtwork(ctx, conn, r, itunesLookup)
}

func insertScannedSidecarCover(
	ctx context.Context,
	conn *sql.Conn,
	r *app.ScannedFile,
) error {
	thumbnailCacheName := ""
	if r.ScanError == "" {
		artworkBytes, err := os.ReadFile(filepath.Join(r.RootPath, r.Path))
		if err != nil {
			if ctx.Err() != nil {
				return ctx.Err()
			}
			log.Printf("Cannot read sidecar artwork %q: %v", r.Path, err)
		} else {
			thumbnailCacheName, err = cacheArtworkForScan(
				ctx,
				conn,
				fmt.Sprintf("sidecar artwork %q", r.Path),
				artworkBytes,
			)
			if err != nil {
				return err
			}
		}
	} else {
		log.Printf("Cannot read sidecar artwork %q: %s", r.Path, r.ScanError)
	}

	_, err := conn.ExecContext(
		ctx,
		insertScannedSidecarCoverSQL,
		sql.Named("root_pos", r.RootPos),
		sql.Named("path_dir", filepath.Dir(r.Path)),
		sql.Named("stem", r.Stem),
		sql.Named("ext", r.Ext),
		sql.Named("path", r.Path),
		sql.Named("file_size", r.FileSize),
		sql.Named("file_mtime_unix_nano", r.FileModTime.UnixNano()),
		sql.Named(
			"thumbnail_cache_name",
			nullableThumbnailCacheName(thumbnailCacheName),
		),
	)
	if err != nil {
		return fmt.Errorf("insert scanned sidecar cover %q: %w", r.Path, err)
	}

	return nil
}

func insertScannedSidecarOther(
	ctx context.Context,
	conn *sql.Conn,
	r *app.ScannedFile,
) error {
	_, err := conn.ExecContext(
		ctx,
		insertScannedSidecarOtherSQL,
		sql.Named("root_pos", r.RootPos),
		sql.Named("path_dir", filepath.Dir(r.Path)),
		sql.Named("stem", r.Stem),
		sql.Named("ext", r.Ext),
		sql.Named("path", r.Path),
		sql.Named("file_size", r.FileSize),
		sql.Named("file_mtime_unix_nano", r.FileModTime.UnixNano()),
	)
	if err != nil {
		return fmt.Errorf("insert scanned sidecar %q: %w", r.Path, err)
	}

	return nil
}

func cacheArtworkForScan(
	ctx context.Context,
	conn *sql.Conn,
	description string,
	artworkBytes []byte,
) (string, error) {
	name, err := cacheArtworkThumbnails(ctx, conn, artworkBytes)
	if errors.Is(err, errInvalidArtwork) {
		log.Printf("Cannot cache %s: %v", description, err)
		return "", nil
	}
	if err != nil {
		return "", fmt.Errorf("cache %s: %w", description, err)
	}

	return name, nil
}

func nullableThumbnailCacheName(name string) any {
	if name == "" {
		return nil
	}
	return name
}

func cacheScannedFileITunesArtwork(
	ctx context.Context,
	conn *sql.Conn,
	r *app.ScannedFile,
	itunesLookup ITunesArtworkLookup,
) error {
	if itunesLookup == nil || r.Metadata == nil {
		return nil
	}

	candidates := [...]struct {
		kind app.ITunesLookupKind
		id   int64
	}{
		{app.ITunesLookupAlbum, r.Metadata.ItunesStoreAlbumId},
		{app.ITunesLookupTrack, r.Metadata.ItunesStoreTrackId},
	}

	for _, candidate := range candidates {
		if candidate.id < 1 {
			continue
		}

		lookupID := fmt.Sprintf("%d", candidate.id)
		result, _, err := itunesLookup(ctx, candidate.kind, lookupID)
		if err != nil {
			if ctx.Err() != nil {
				return ctx.Err()
			}
			if errors.Is(err, ErrITunesArtworkCache) {
				return err
			}
			log.Printf(
				"iTunes artwork lookup failed for %s %s: %v",
				candidate.kind,
				lookupID,
				err,
			)
			continue
		}

		switch result.ResultKind {
		case app.ITunesResultHit:
			thumbnailCacheName, err := cacheArtworkForScan(
				ctx,
				conn,
				fmt.Sprintf("iTunes %s artwork %s", candidate.kind, lookupID),
				result.Bytes,
			)
			if err != nil {
				return err
			}
			if thumbnailCacheName == "" {
				continue
			}

			if err := LinkCachedITunesArtworkThumbnail(
				ctx,
				conn,
				candidate.kind,
				lookupID,
				thumbnailCacheName,
			); err != nil {
				return err
			}
			return nil
		case app.ITunesResultMissing:
			continue
		default:
			log.Printf(
				"iTunes artwork lookup for %s %s returned unsupported result kind %q",
				candidate.kind,
				lookupID,
				result.ResultKind,
			)
		}
	}

	return nil
}

func FindOrInsertMapping(
	ctx context.Context,
	conn *sql.Conn,
	albumKey string,
	mapping []string,
) ([]string, error) {
	b, err := json.Marshal(mapping)
	if err != nil {
		return nil, err
	}

	var mappingJSON string

	err = conn.QueryRowContext(
		ctx,
		findOrInsertMappingSQL,
		sql.Named("album_key", albumKey),
		sql.Named("mapped_artists", string(b)),
	).Scan(&mappingJSON)

	if err != nil {
		return nil, err
	}

	if err := json.Unmarshal([]byte(mappingJSON), &mapping); err != nil {
		return nil, err
	}

	return mapping, nil
}

// -----------------------------------------------------------------------------
// helpers
// -----------------------------------------------------------------------------

func resetLibrary(ctx context.Context, tx *sql.Tx) error {
	if _, err := tx.ExecContext(ctx, resetLibrarySQL); err != nil {
		return fmt.Errorf("reset library tables: %w", err)
	}

	return nil
}

func buildLibraryRoots(ctx context.Context, tx *sql.Tx) error {
	if _, err := tx.ExecContext(ctx, buildLibraryRootsSQL); err != nil {
		return fmt.Errorf("build library roots: %w", err)
	}

	return nil
}

func buildLibraryAlbums(ctx context.Context, tx *sql.Tx) error {
	if _, err := tx.ExecContext(ctx, buildLibraryAlbumsSQL); err != nil {
		return fmt.Errorf("build library albums: %w", err)
	}

	return nil
}

func buildLibraryAlbumArtists(ctx context.Context, tx *sql.Tx) error {
	if _, err := tx.ExecContext(ctx, buildLibraryAlbumArtistsSQL); err != nil {
		return fmt.Errorf("build library album artists: %w", err)
	}

	return nil
}

func buildLibraryTracks(ctx context.Context, tx *sql.Tx) error {
	if _, err := tx.ExecContext(ctx, buildLibraryTracksSQL); err != nil {
		return fmt.Errorf("build library tracks: %w", err)
	}

	return nil
}

func buildLibraryTrackEmbeddedArtwork(ctx context.Context, tx *sql.Tx) error {
	if _, err := tx.ExecContext(ctx, buildLibraryTrackEmbeddedArtworkSQL); err != nil {
		return fmt.Errorf("build library track embedded artwork: %w", err)
	}

	return nil
}

func buildLibraryTrackGenresAndStyles(
	ctx context.Context,
	tx *sql.Tx,
	unknownItem string,
	secondaryGenreSplitTokens map[string]bool,
) (result []GenreResolutionResult, err error) {
	splitDelimiters := make([]string, 0, len(secondaryGenreSplitTokens))
	for delimiter, enabled := range secondaryGenreSplitTokens {
		if enabled {
			splitDelimiters = append(splitDelimiters, delimiter)
		}
	}
	slices.Sort(splitDelimiters)

	splitDelimitersJSON, err := json.Marshal(splitDelimiters)
	if err != nil {
		return nil, fmt.Errorf("JSON encoding error for secondary genre split tokens: %w", err)
	}

	if _, err = tx.ExecContext(
		ctx,
		buildLibraryTrackGenresAndStylesSQL,
		sql.Named("split_delimiters", string(splitDelimitersJSON)),
		sql.Named("unknown_item", unknownItem),
	); err != nil {
		err = fmt.Errorf(
			"build library track genres and styles: %w",
			err,
		)
		return
	}

	var rows *sql.Rows

	if rows, err = tx.QueryContext(ctx, genreResolutionResultsSQL); err != nil {
		return
	}
	defer rows.Close()

	for rows.Next() {
		var item GenreResolutionResult

		if err = rows.Scan(
			&item.TrackId,
			&item.Path,
			&item.Status,
			&item.LogMessage,
		); err != nil {
			return
		}

		result = append(result, item)
	}

	if err = rows.Err(); err != nil {
		return
	}

	return
}

func buildLibraryTrackSidecars(ctx context.Context, tx *sql.Tx) error {
	if _, err := tx.ExecContext(ctx, buildLibraryTrackSidecarsSQL); err != nil {
		return fmt.Errorf("build library track sidecars: %w", err)
	}

	return nil
}

func buildLibraryTrackArtwork(ctx context.Context, tx *sql.Tx) error {
	if _, err := tx.ExecContext(
		ctx,
		insertLibraryTrackArtworkSQL,
		sql.Named("artwork_width_small", AlbumArtwork64),
		sql.Named("artwork_width_large", AlbumArtwork256),
	); err != nil {
		return fmt.Errorf("build library track artwork: %w", err)
	}

	return nil
}

func setBuildLibraryAppMetadata(
	ctx context.Context,
	tx *sql.Tx,
	recognizedAudioExtensions map[string]bool,
	recognizedPlaylistExtensions map[string]bool,
	recognizedSidecarExtensions map[string]bool,
) error {
	{
		var currentTimestamp string
		if err := tx.QueryRowContext(ctx, currentTimestampSQL).Scan(&currentTimestamp); err != nil {
			return err
		}

		if err := SetAppMetadata(ctx, tx, app.LibraryGeneratedAt, currentTimestamp); err != nil {
			return fmt.Errorf("setting app metadata key %d: %w", int(app.LibraryGeneratedAt), err)
		}
	}

	payload := struct {
		AudioExtensions    map[string]bool `json:"audio_extensions"`
		PlaylistExtensions map[string]bool `json:"playlist_extensions"`
		SidecarExtensions  map[string]bool `json:"sidecar_extensions"`
	}{
		AudioExtensions:    recognizedAudioExtensions,
		PlaylistExtensions: recognizedPlaylistExtensions,
		SidecarExtensions:  recognizedSidecarExtensions,
	}
	if b, err := json.MarshalIndent(payload, "", "  "); err != nil {
		return err
	} else if err = SetAppMetadata(ctx, tx, app.LibrarySupportedExtensionsJson, string(b)); err != nil {
		return fmt.Errorf("setting app metadata key %d: %w", int(app.LibrarySupportedExtensionsJson), err)
	}

	return nil
}
