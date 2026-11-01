package command

import (
	"context"
	"crypto/rand"
	"database/sql"
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"os"
	"slices"
	"strconv"

	"github.com/zanyoats/kukicha/internal/app"
	"github.com/zanyoats/kukicha/internal/usecase/query"
)

//go:embed sql/library_counts.sql
var libraryCountsSQL string

//go:embed sql/insert_scanned_playlist.sql
var insertScannedPlaylistSQL string

//go:embed sql/insert_scanned_file.sql
var insertScannedFileSQL string

//go:embed sql/insert_scan_root.sql
var insertScanRootSQL string

//go:embed sql/insert_scanned_artwork.sql
var insertScannedArtworkSQL string

//go:embed sql/insert_scanned_artwork_cover.sql
var insertScannedArtworkCoverSQL string

//go:embed sql/insert_scanned_sidecar_cover.sql
var insertScannedSidecarCoverSQL string

//go:embed sql/insert_scanned_sidecar_other.sql
var insertScannedSidecarOtherSQL string

//go:embed sql/reset_library.sql
var resetLibrarySQL string

//go:embed sql/allocate_library_ids.sql
var allocateLibraryIDsSQL string

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

//go:embed sql/build_library_album_genres_and_styles.sql
var buildLibraryAlbumGenresAndStylesSQL string

//go:embed sql/build_library_radios.sql
var buildLibraryRadiosSQL string

//go:embed sql/genre_resolution_results.sql
var genreResolutionResultsSQL string

//go:embed sql/build_library_album_sidecars.sql
var buildLibraryAlbumSidecarsSQL string

//go:embed sql/build_library_track_artwork.sql
var buildLibraryTrackArtworkSQL string

//go:embed sql/build_library_album_overrides.sql
var buildLibraryAlbumOverridesSQL string

//go:embed sql/current_timestamp.sql
var currentTimestampSQL string

type BuildLibraryResult struct {
	ScanStats SyncScanStats
	LogStats  query.JobLogStats
}

type SyncScanStats struct {
	ProcessedFiles             int
	SidecarFiles               int
	UnassociatedSidecars       int
	LibraryRoots               int
	LibraryAlbums              int
	LibraryTracks              int
	LibraryPlaylists           int
	LibraryAlbumSidecars       int
	LibraryTrackTitleUnknowns  int
	LibraryAlbumUnknowns       int
	LibraryAlbumArtistUnknowns int
}

// BuildLibraryFromScan atomically rebuilds the library from staged resources and
// commits the generation marker so recovered jobs can skip completed rebuilds.
// NormalizeScanTracks must complete successfully after all tracks are staged and
// before this call. Staged tracks must not change between normalization and publishing.
func BuildLibraryFromScan(
	ctx context.Context,
	conn *sql.Conn,
	unknownItem string,
	secondaryGenreSplitTokens map[string]bool,
	recognizedAudioExtensions map[string]bool,
	recognizedPlaylistExtensions map[string]bool,
	recognizedSidecarExtensions map[string]bool,
) (BuildLibraryResult, error) {
	var result BuildLibraryResult
	jobID, ok := app.JobIDFromContext(ctx)
	if !ok {
		return result, errors.New("library build requires a job ID in context")
	}
	tx, err := conn.BeginTx(ctx, nil)
	if err != nil {
		return result, err
	}
	defer tx.Rollback()

	// Publish the new version only when the rebuilt membership commits.

	// Scan reuses root, album, and track IDs, but updated artist rules
	// can introduce new artists that need fresh IDs.
	if err = allocateLibraryIDs(ctx, tx); err != nil {
		return result, err
	}

	if err = resetLibrary(ctx, tx); err != nil {
		return result, err
	}
	if err = buildLibraryRoots(ctx, tx); err != nil {
		return result, err
	}

	if err = buildLibraryAlbums(ctx, tx, unknownItem); err != nil {
		return result, err
	}

	if err = buildLibraryAlbumArtists(ctx, tx); err != nil {
		return result, err
	}

	if err = buildLibraryTracks(ctx, tx); err != nil {
		return result, err
	}

	if err = buildLibraryTrackGenresAndStyles(
		ctx, tx,
		unknownItem,
		secondaryGenreSplitTokens,
		jobID,
	); err != nil {
		return result, err
	}

	if err = buildLibraryAlbumGenresAndStyles(ctx, tx); err != nil {
		return result, err
	}

	if _, err = tx.ExecContext(ctx, buildLibraryRadiosSQL); err != nil {
		return result, fmt.Errorf("build library radios: %w", err)
	}
	if err = buildLibraryTrackEmbeddedArtwork(ctx, tx); err != nil {
		return result, err
	}
	if err = buildLibraryAlbumSidecars(ctx, tx); err != nil {
		return result, err
	}
	if err = buildLibraryTrackArtwork(ctx, tx); err != nil {
		return result, err
	}

	if _, err = tx.ExecContext(ctx, buildLibraryAlbumOverridesSQL); err != nil {
		return result, err
	}

	err = tx.QueryRowContext(ctx, libraryCountsSQL, sql.Named("unknown_item", unknownItem)).Scan(
		&result.ScanStats.ProcessedFiles,
		&result.ScanStats.SidecarFiles,
		&result.ScanStats.UnassociatedSidecars,
		&result.ScanStats.LibraryRoots,
		&result.ScanStats.LibraryAlbums,
		&result.ScanStats.LibraryTracks,
		&result.ScanStats.LibraryPlaylists,
		&result.ScanStats.LibraryAlbumSidecars,
		&result.ScanStats.LibraryTrackTitleUnknowns,
		&result.ScanStats.LibraryAlbumUnknowns,
		&result.ScanStats.LibraryAlbumArtistUnknowns,
	)
	if err != nil {
		return result, err
	}

	if result.LogStats, err = query.JobLogCounts(ctx, tx, jobID); err != nil {
		return result, err
	}

	if err := setLibraryBuildMetadata(ctx, tx, jobID,
		recognizedAudioExtensions, recognizedPlaylistExtensions, recognizedSidecarExtensions,
	); err != nil {
		return result, err
	}

	return result, tx.Commit()
}

func InsertScannedFile(
	ctx context.Context,
	conn *sql.Conn,
	r *app.ScannedFile,
	localArtworkMaxBytes int64,
	physicalPath string,
) error {
	switch r.FileType {
	case app.FileTypeAudio:
		return insertScannedTrack(ctx, conn, r)

	case app.FileTypeSidecarCover:
		return insertScannedSidecarCover(
			ctx,
			conn,
			r,
			localArtworkMaxBytes,
			physicalPath,
		)

	case app.FileTypeSidecarOther:
		return insertScannedSidecarOther(ctx, conn, r)

	case app.FileTypePlaylist:
		_, err := conn.ExecContext(ctx, insertScannedPlaylistSQL,
			sql.Named("root_pos", r.RootPos), sql.Named("path", r.Path))
		return err

	default:
		return fmt.Errorf("unsupported scanned file type %d", r.FileType)
	}
}

func insertScannedTrack(
	ctx context.Context,
	conn *sql.Conn,
	r *app.ScannedFile,
) error {
	if r.Metadata == nil {
		return errors.New("scanned audio track is missing metadata")
	}

	b, err := json.Marshal(r.Metadata)
	if err != nil {
		return err
	}
	metadata := string(b)

	_, err = conn.ExecContext(
		ctx,
		insertScannedFileSQL,
		sql.Named("root_pos", r.RootPos),
		sql.Named("config_id", r.Root.ConfigID()),
		sql.Named("unknown_item", app.UnknownItem),
		sql.Named("path_dir", r.FileIdentity.Directory()),
		sql.Named("stem", r.Stem),
		sql.Named("ext", r.Ext),
		sql.Named("path", r.Path),
		sql.Named("file_size", r.FileSize),
		sql.Named("file_mtime_unix_nano", r.FileModTime.UnixNano()),
		sql.Named("duration_seconds", r.DurationSeconds),
		sql.Named("track_metadata", metadata),
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
			thumbnailCacheName, err = CacheArtworkThumbnails(
				ctx, conn, artwork.ArtworkBytes,
				fmt.Sprintf("embedded artwork %q position %d", r.Path, artworkPos),
				r.Root.ConfigID(), r.Path,
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

	return nil
}

// InsertScanRoot records a config reference independently of the files it contains.
func InsertScanRoot(ctx context.Context, conn *sql.Conn, pos int, root app.Root) error {
	_, err := conn.ExecContext(ctx, insertScanRootSQL,
		sql.Named("root_pos", pos),
		sql.Named("config_id", root.ConfigID()),
	)
	return err
}

func insertScannedSidecarCover(
	ctx context.Context,
	conn *sql.Conn,
	r *app.ScannedFile,
	localArtworkMaxBytes int64,
	physicalPath string,
) error {
	thumbnailCacheName := ""
	artworkBytes, err := readLocalArtworkFile(
		physicalPath, localArtworkMaxBytes,
	)
	if err != nil {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		app.JobLogger(ctx).WarnContext(ctx, fmt.Sprintf("cannot read sidecar artwork: %v", err), "root", r.Root.ConfigID(), "path", r.Path)
	} else {
		thumbnailCacheName, err = CacheArtworkThumbnails(
			ctx, conn, artworkBytes,
			fmt.Sprintf("sidecar artwork %q", r.Path),
			r.Root.ConfigID(), r.Path,
		)
		if err != nil {
			return err
		}
	}

	_, err = conn.ExecContext(
		ctx,
		insertScannedSidecarCoverSQL,
		sql.Named("root_pos", r.RootPos),
		sql.Named("path_dir", r.FileIdentity.Directory()),
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

func readLocalArtworkFile(path string, maxBytes int64) ([]byte, error) {
	if maxBytes < 1 {
		return nil, errors.New("local artwork byte limit must be positive")
	}

	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer file.Close()

	artworkBytes, err := io.ReadAll(io.LimitReader(file, maxBytes))
	if err != nil {
		return nil, err
	}

	var extra [1]byte
	extraBytes, err := file.Read(extra[:])
	if err != nil && !errors.Is(err, io.EOF) {
		return nil, err
	}
	if extraBytes != 0 {
		return nil, fmt.Errorf(
			"artwork exceeds configured limit of %d bytes",
			maxBytes,
		)
	}

	return artworkBytes, nil
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
		sql.Named("path_dir", r.FileIdentity.Directory()),
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

func nullableThumbnailCacheName(name string) any {
	if name == "" {
		return nil
	}
	return name
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

func allocateLibraryIDs(ctx context.Context, tx *sql.Tx) error {
	if _, err := tx.ExecContext(ctx, allocateLibraryIDsSQL); err != nil {
		return fmt.Errorf("allocate persistent library IDs: %w", err)
	}

	return nil
}

func buildLibraryRoots(ctx context.Context, tx *sql.Tx) error {
	if _, err := tx.ExecContext(ctx, buildLibraryRootsSQL); err != nil {
		return fmt.Errorf("build library roots: %w", err)
	}

	return nil
}

func buildLibraryAlbums(ctx context.Context, tx *sql.Tx, unknownItem string) error {
	if _, err := tx.ExecContext(ctx, buildLibraryAlbumsSQL, sql.Named("unknown_item", unknownItem)); err != nil {
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
	jobID int64,
) error {
	splitDelimiters := make([]string, 0, len(secondaryGenreSplitTokens))
	for delimiter, enabled := range secondaryGenreSplitTokens {
		if enabled {
			splitDelimiters = append(splitDelimiters, delimiter)
		}
	}
	slices.Sort(splitDelimiters)

	splitDelimitersJSON, err := json.Marshal(splitDelimiters)
	if err != nil {
		return fmt.Errorf("JSON encoding error for secondary genre split tokens: %w", err)
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
		return err
	}

	logger := app.JobLogger(ctx)
	if _, err := tx.ExecContext(ctx, genreResolutionResultsSQL,
		sql.Named("job_id", jobID),
		sql.Named("error_enabled", logger.Enabled(ctx, slog.LevelError)),
		sql.Named("warn_enabled", logger.Enabled(ctx, slog.LevelWarn)),
	); err != nil {
		return fmt.Errorf("record genre resolution issues: %w", err)
	}
	return nil
}

func buildLibraryAlbumGenresAndStyles(
	ctx context.Context,
	tx *sql.Tx,
) error {
	if _, err := tx.ExecContext(
		ctx,
		buildLibraryAlbumGenresAndStylesSQL,
	); err != nil {
		return fmt.Errorf(
			"build library album genres and styles: %w",
			err,
		)
	}

	return nil
}

func buildLibraryAlbumSidecars(ctx context.Context, tx *sql.Tx) error {
	if _, err := tx.ExecContext(ctx, buildLibraryAlbumSidecarsSQL); err != nil {
		return fmt.Errorf("build library album sidecars: %w", err)
	}

	return nil
}

func buildLibraryTrackArtwork(ctx context.Context, tx *sql.Tx) error {
	if _, err := tx.ExecContext(
		ctx,
		buildLibraryTrackArtworkSQL,
		sql.Named("artwork_width_small", AlbumArtwork64),
		sql.Named("artwork_width_large", AlbumArtwork256),
	); err != nil {
		return fmt.Errorf("build library track artwork: %w", err)
	}

	return nil
}

func setLibraryBuildMetadata(
	ctx context.Context,
	tx *sql.Tx,
	jobID int64,
	recognizedAudioExtensions map[string]bool,
	recognizedPlaylistExtensions map[string]bool,
	recognizedSidecarExtensions map[string]bool,
) error {
	if err := SetAppMetadata(ctx, tx, app.RadioMembershipVersion, rand.Text()); err != nil {
		return fmt.Errorf("setting app metadata key %d: %w", int(app.RadioMembershipVersion), err)
	}

	if err := SetAppMetadata(ctx, tx, app.LibraryGeneratedJobId, strconv.FormatInt(jobID, 10)); err != nil {
		return fmt.Errorf("setting app metadata key %d: %w", int(app.LibraryGeneratedJobId), err)
	}

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
