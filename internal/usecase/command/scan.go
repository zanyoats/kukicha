package command

import (
	"bytes"
	"context"
	"database/sql"
	_ "embed"
	"encoding/json"
	"fmt"
	"image"
	_ "image/gif"
	"image/jpeg"
	"image/png"
	"log"
	"os"
	"path/filepath"
	"slices"

	"github.com/zanyoats/kukicha/internal/app"
	"golang.org/x/image/draw"
	_ "golang.org/x/image/webp"
)

//go:embed sql/prepare_scan_results.sql
var prepareScanResultsSQL string

//go:embed sql/library_counts.sql
var libraryCountsSQL string

//go:embed sql/insert_scanned_file.sql
var insertScannedFileSQL string

//go:embed sql/insert_scanned_artwork.sql
var insertScannedArtworkSQL string

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

//go:embed sql/library_track_artwork_sources.sql
var libraryTrackArtworkSourcesSQL string

//go:embed sql/insert_library_track_artwork.sql
var insertLibraryTrackArtworkSQL string

//go:embed sql/library_track_sidecar_artwork.sql
var libraryTrackSidecarArtworkSQL string

//go:embed sql/library_track_embedded_artwork.sql
var libraryTrackEmbeddedArtworkSQL string

//go:embed sql/library_track_itunes_artwork.sql
var libraryTrackITunesArtworkSQL string

//go:embed sql/current_timestamp.sql
var currentTimestampSQL string

const (
	AlbumArtwork64  = 64
	AlbumArtwork256 = 256
)

func PrepareScanResults(ctx context.Context, conn *sql.Conn) error {
	_, err := conn.ExecContext(
		ctx,
		fmt.Sprintf(prepareScanResultsSQL, app.FileTypeAudio),
	)

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
		sql.Named("file_type", int(r.FileType)),
		sql.Named("file_size", r.FileSize),
		sql.Named("file_mtime_unix_nano", r.FileModTime.UnixNano()),
		sql.Named("track_metadata", metadata),
		sql.Named("normalized_metadata", normalizedMetadata),
		sql.Named("scan_error", r.ScanError),
	)
	if err != nil {
		return err
	}

	if r.FileType != app.FileTypeAudio {
		return nil
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
	}

	return cacheScannedFileITunesArtwork(ctx, r, itunesLookup)
}

func cacheScannedFileITunesArtwork(
	ctx context.Context,
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
	if _, err := tx.ExecContext(
		ctx,
		buildLibraryRootsSQL,
		sql.Named("audio_file_type", app.FileTypeAudio),
	); err != nil {
		return fmt.Errorf("build library roots: %w", err)
	}

	return nil
}

func buildLibraryAlbums(ctx context.Context, tx *sql.Tx) error {
	if _, err := tx.ExecContext(
		ctx,
		buildLibraryAlbumsSQL,
		sql.Named("audio_file_type", app.FileTypeAudio),
	); err != nil {
		return fmt.Errorf("build library albums: %w", err)
	}

	return nil
}

func buildLibraryAlbumArtists(ctx context.Context, tx *sql.Tx) error {
	if _, err := tx.ExecContext(
		ctx,
		buildLibraryAlbumArtistsSQL,
		sql.Named("audio_file_type", app.FileTypeAudio),
	); err != nil {
		return fmt.Errorf("build library album artists: %w", err)
	}

	return nil
}

func buildLibraryTracks(ctx context.Context, tx *sql.Tx) error {
	if _, err := tx.ExecContext(
		ctx,
		buildLibraryTracksSQL,
		sql.Named("audio_file_type", app.FileTypeAudio),
	); err != nil {
		return fmt.Errorf("build library tracks: %w", err)
	}

	return nil
}

func buildLibraryTrackEmbeddedArtwork(ctx context.Context, tx *sql.Tx) error {
	if _, err := tx.ExecContext(
		ctx,
		buildLibraryTrackEmbeddedArtworkSQL,
		sql.Named("audio_file_type", app.FileTypeAudio),
	); err != nil {
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
		sql.Named("audio_file_type", app.FileTypeAudio),
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
	if _, err := tx.ExecContext(
		ctx,
		buildLibraryTrackSidecarsSQL,
		sql.Named("audio_file_type", app.FileTypeAudio),
		sql.Named("cover_file_type", app.FileTypeSidecarCover),
		sql.Named("other_file_type", app.FileTypeSidecarOther),
	); err != nil {
		return fmt.Errorf("build library track sidecars: %w", err)
	}

	return nil
}

func buildLibraryTrackArtwork(ctx context.Context, tx *sql.Tx) error {
	rows, err := tx.QueryContext(ctx, libraryTrackArtworkSourcesSQL)
	if err != nil {
		return fmt.Errorf("query library tracks for artwork: %w", err)
	}

	type libraryTrackArtworkSource struct {
		trackID  int64
		rootPath string
	}
	var sources []libraryTrackArtworkSource

	for rows.Next() {
		var source libraryTrackArtworkSource
		if err := rows.Scan(&source.trackID, &source.rootPath); err != nil {
			rows.Close()
			return fmt.Errorf("scan library track for artwork: %w", err)
		}
		sources = append(sources, source)
	}
	if err := rows.Err(); err != nil {
		rows.Close()
		return fmt.Errorf("iterate library tracks for artwork: %w", err)
	}
	if err := rows.Close(); err != nil {
		return fmt.Errorf("close library track artwork source rows: %w", err)
	}

	for _, source := range sources {
		artwork, sourceFormat, err := resolveLibraryTrackArtwork(ctx, tx, source.trackID, source.rootPath)
		if err != nil {
			return err
		}
		if artwork == nil {
			continue
		}

		for _, width := range []int{AlbumArtwork64, AlbumArtwork256} {
			artworkBytes, contentType, err := encodeLibraryTrackArtwork(
				resizeToWidth(artwork, width),
				sourceFormat,
			)
			if err != nil {
				return fmt.Errorf("encode library track %d artwork at width %d: %w", source.trackID, width, err)
			}

			if _, err := tx.ExecContext(
				ctx,
				insertLibraryTrackArtworkSQL,
				sql.Named("track_id", source.trackID),
				sql.Named("width_px", width),
				sql.Named("artwork_content_type", contentType),
				sql.Named("artwork_content_size", len(artworkBytes)),
				sql.Named("artwork_bytes", artworkBytes),
			); err != nil {
				return fmt.Errorf("insert library track %d artwork at width %d: %w", source.trackID, width, err)
			}
		}
	}

	return nil
}

func resolveLibraryTrackArtwork(
	ctx context.Context,
	tx *sql.Tx,
	trackID int64,
	rootPath string,
) (image.Image, string, error) {
	rows, err := tx.QueryContext(
		ctx,
		libraryTrackSidecarArtworkSQL,
		sql.Named("track_id", trackID),
		sql.Named("cover_file_type", app.FileTypeSidecarCover),
	)
	if err != nil {
		return nil, "", fmt.Errorf("query library track %d sidecar artwork: %w", trackID, err)
	}

	var resolved image.Image
	var sourceFormat string
	for rows.Next() {
		var sidecarPath string
		if err := rows.Scan(&sidecarPath); err != nil {
			rows.Close()
			return nil, "", fmt.Errorf("scan library track %d sidecar artwork: %w", trackID, err)
		}

		artwork, format, err := decodeImageFile(filepath.Join(rootPath, sidecarPath))
		if err == nil {
			resolved = artwork
			sourceFormat = format
			break
		}
	}
	if err := rows.Err(); err != nil {
		rows.Close()
		return nil, "", fmt.Errorf("iterate library track %d sidecar artwork: %w", trackID, err)
	}
	if err := rows.Close(); err != nil {
		return nil, "", fmt.Errorf("close library track %d sidecar artwork rows: %w", trackID, err)
	}
	if resolved != nil {
		return resolved, sourceFormat, nil
	}

	rows, err = tx.QueryContext(
		ctx,
		libraryTrackEmbeddedArtworkSQL,
		sql.Named("track_id", trackID),
	)
	if err != nil {
		return nil, "", fmt.Errorf("query library track %d embedded artwork: %w", trackID, err)
	}

	for rows.Next() {
		var artworkBytes []byte
		if err := rows.Scan(&artworkBytes); err != nil {
			rows.Close()
			return nil, "", fmt.Errorf("scan library track %d embedded artwork: %w", trackID, err)
		}

		artwork, format, err := image.Decode(bytes.NewReader(artworkBytes))
		if err == nil {
			resolved = artwork
			sourceFormat = format
			break
		}
	}
	if err := rows.Err(); err != nil {
		rows.Close()
		return nil, "", fmt.Errorf("iterate library track %d embedded artwork: %w", trackID, err)
	}
	if err := rows.Close(); err != nil {
		return nil, "", fmt.Errorf("close library track %d embedded artwork rows: %w", trackID, err)
	}
	if resolved != nil {
		return resolved, sourceFormat, nil
	}

	rows, err = tx.QueryContext(
		ctx,
		libraryTrackITunesArtworkSQL,
		sql.Named("track_id", trackID),
	)
	if err != nil {
		return nil, "", fmt.Errorf("query library track %d iTunes artwork: %w", trackID, err)
	}

	for rows.Next() {
		var artworkBytes []byte
		if err := rows.Scan(&artworkBytes); err != nil {
			rows.Close()
			return nil, "", fmt.Errorf("scan library track %d iTunes artwork: %w", trackID, err)
		}

		artwork, format, err := image.Decode(bytes.NewReader(artworkBytes))
		if err == nil {
			resolved = artwork
			sourceFormat = format
			break
		}
	}
	if err := rows.Err(); err != nil {
		rows.Close()
		return nil, "", fmt.Errorf("iterate library track %d iTunes artwork: %w", trackID, err)
	}
	if err := rows.Close(); err != nil {
		return nil, "", fmt.Errorf("close library track %d iTunes artwork rows: %w", trackID, err)
	}

	return resolved, sourceFormat, nil
}

func decodeImageFile(path string) (image.Image, string, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, "", err
	}
	defer f.Close()

	return image.Decode(f)
}

func encodeLibraryTrackArtwork(src image.Image, sourceFormat string) ([]byte, string, error) {
	var encoded bytes.Buffer

	if sourceFormat == "jpeg" {
		err := jpeg.Encode(&encoded, src, &jpeg.Options{Quality: 90})
		return encoded.Bytes(), "image/jpeg", err
	}

	err := png.Encode(&encoded, src)
	return encoded.Bytes(), "image/png", err
}

func resizeToWidth(src image.Image, width int) image.Image {
	srcBounds := src.Bounds()
	height := int(float64(srcBounds.Dy())/float64(srcBounds.Dx())*float64(width) + 0.5)
	if height < 1 {
		height = 1
	}

	dst := image.NewNRGBA(image.Rect(0, 0, width, height))
	draw.CatmullRom.Scale(dst, dst.Bounds(), src, srcBounds, draw.Over, nil)
	return dst
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
