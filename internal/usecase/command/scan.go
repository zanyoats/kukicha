package command

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"path/filepath"
	"slices"

	"github.com/zanyoats/kukicha/internal/app"
)

func PrepareScanResults(ctx context.Context, conn *sql.Conn) error {
	_, err := conn.ExecContext(ctx, fmt.Sprintf(`
		PRAGMA temp_store = FILE;
		PRAGMA temp.cache_size = -32768;

		DROP TABLE IF EXISTS scan_tracks;

		CREATE TEMP TABLE scan_tracks (
			root_pos             INTEGER NOT NULL                 ,
			root_path            TEXT    NOT NULL                 ,
			album_key            TEXT    NOT NULL                 ,
			path_dir             TEXT    NOT NULL                 ,
			stem                 TEXT    NOT NULL                 ,
			ext                  TEXT    NULL                     ,
			path                 TEXT    NOT NULL                 ,
			file_type            INTEGER NOT NULL                 ,
			file_size            INTEGER NOT NULL                 ,
			file_mtime_unix_nano INTEGER NOT NULL                 ,
			track_metadata       TEXT    NOT NULL                 , -- JSON object
			normalized_metadata  TEXT    NOT NULL                 , -- JSON object
			scan_error           TEXT    NOT NULL                 ,

			PRIMARY KEY (root_pos, path)                          ,
			CHECK (
				    json_valid(track_metadata)
				and json_valid(normalized_metadata)
				and (
					   file_type != %[1]d
					or length(album_key) > 0
				)
			)
		) WITHOUT ROWID;

		DROP TABLE IF EXISTS scan_artwork;

		CREATE TEMP TABLE scan_artwork (
			root_pos             INTEGER NOT NULL                 ,
			path                 TEXT    NOT NULL                 ,
			artwork_pos          INTEGER NOT NULL                 ,
			picture_type         TEXT    NOT NULL                 ,
			artwork_content_type TEXT                             ,
			artwork_content_size INTEGER NOT NULL                 ,
			artwork_bytes        BLOB    NOT NULL                 ,

			PRIMARY KEY (root_pos, path, artwork_pos)              ,
			CHECK (
				    length(picture_type) > 0
				and artwork_content_size > 0
			)
		) WITHOUT ROWID;
	`, app.FileTypeAudio))

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

	if err = setBuildLibraryAppMetadata(
		ctx, tx,
		recognizedAudioExtensions,
		recognizedPlaylistExtensions,
		recognizedSidecarExtensions,
	); err != nil {
		_ = tx.Rollback()
		return result, err
	}

	err = tx.QueryRowContext(ctx, `
		SELECT
			(SELECT COUNT(*) FROM library_roots) AS library_roots_count,
			(SELECT COUNT(*) FROM library_albums) AS library_albums_count,
			(SELECT COUNT(*) FROM library_tracks) AS library_tracks_count;
	`).Scan(
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

func InsertScannedFile(ctx context.Context, conn *sql.Conn, r *app.ScannedFile) error {
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

	_, err = conn.ExecContext(ctx, `
		INSERT INTO scan_tracks (
			root_pos, root_path, album_key,
			path_dir, stem, ext, path,
			file_type, file_size, file_mtime_unix_nano,
			track_metadata, normalized_metadata, scan_error
		)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, json(?), json(?), ?)
	`,
		r.RootPos,
		r.RootPath,
		r.NormalizedMetadata.AlbumKey,
		filepath.Dir(r.Path),
		r.Stem,
		r.Ext,
		r.Path,
		int(r.FileType),
		r.FileSize,
		r.FileModTime.UnixNano(),
		metadata,
		normalizedMetadata,
		r.ScanError,
	)
	if err != nil {
		return err
	}

	if r.FileType != app.FileTypeAudio {
		return nil
	}

	for artworkPos, artwork := range r.Artwork {
		_, err = conn.ExecContext(ctx, `
			INSERT INTO scan_artwork (
				root_pos,
				path,
				artwork_pos,
				picture_type,
				artwork_content_type,
				artwork_content_size,
				artwork_bytes
			)
			VALUES (?, ?, ?, ?, ?, ?, ?)
		`,
			r.RootPos,
			r.Path,
			artworkPos,
			artwork.PictureType,
			artwork.ArtworkContentType,
			artwork.ArtworkContentSize,
			artwork.ArtworkBytes,
		)
		if err != nil {
			return err
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

	err = conn.QueryRowContext(ctx, `
		INSERT INTO album_artist_split_mappings (album_key, mapped_artists)
		VALUES (?, json(?))
		ON CONFLICT(album_key) DO UPDATE SET
			mapped_artists = mapped_artists
		RETURNING mapped_artists;
	`, albumKey, string(b)).Scan(&mappingJSON)

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
	if _, err := tx.ExecContext(ctx, `
		DELETE FROM library_track_styles;
		DELETE FROM library_track_genres;
		DELETE FROM library_track_sidecars;
		DELETE FROM library_track_embedded_artwork;
		DELETE FROM library_tracks;
		DELETE FROM library_album_artists;
		DELETE FROM library_albums;
		DELETE FROM library_roots;

		DELETE FROM sqlite_sequence
		WHERE name IN (
			'library_roots',
			'library_albums',
			'library_album_artists',
			'library_tracks',
			'library_track_embedded_artwork',
			'library_track_sidecars',
			'library_track_genres',
			'library_track_styles'
		);
	`); err != nil {
		return fmt.Errorf("reset library tables: %w", err)
	}

	return nil
}

func buildLibraryRoots(ctx context.Context, tx *sql.Tx) error {
	if _, err := tx.ExecContext(ctx, `
		INSERT OR IGNORE INTO library_roots (pos, path)
			SELECT DISTINCT
				root_pos,
				root_path
			FROM scan_tracks
			WHERE file_type = ?;
	`, app.FileTypeAudio); err != nil {
		return fmt.Errorf("build library roots: %w", err)
	}

	return nil
}

func buildLibraryAlbums(ctx context.Context, tx *sql.Tx) error {
	if _, err := tx.ExecContext(ctx, `
		WITH
		audio_tracks AS (
			SELECT
				album_key,
				root_pos,
				path,
				track_metadata,
				normalized_metadata
			FROM scan_tracks
			WHERE file_type = ?
		),
		album_values AS (
			SELECT album_key, root_pos, path, 'format' AS field, NULLIF(json_extract(track_metadata, '$.format'), '') AS value FROM audio_tracks
			UNION ALL SELECT album_key, root_pos, path, 'album', NULLIF(json_extract(normalized_metadata, '$.album_display'), '') FROM audio_tracks
			UNION ALL SELECT album_key, root_pos, path, 'track_count', NULLIF(json_extract(track_metadata, '$.track_count'), 0) FROM audio_tracks
			UNION ALL SELECT album_key, root_pos, path, 'disc_count', NULLIF(json_extract(track_metadata, '$.disc_count'), 0) FROM audio_tracks
			UNION ALL SELECT album_key, root_pos, path, 'release_date', NULLIF(json_extract(track_metadata, '$.release_date'), '') FROM audio_tracks
			UNION ALL SELECT album_key, root_pos, path, 'original_release_date', NULLIF(json_extract(track_metadata, '$.original_release_date'), '') FROM audio_tracks
			UNION ALL SELECT album_key, root_pos, path, 'recorded_date', NULLIF(json_extract(track_metadata, '$.recorded_date'), '') FROM audio_tracks
			UNION ALL SELECT album_key, root_pos, path, 'release_year', NULLIF(json_extract(track_metadata, '$.release_year'), 0) FROM audio_tracks
			UNION ALL SELECT album_key, root_pos, path, 'original_release_year', NULLIF(json_extract(track_metadata, '$.original_release_year'), 0) FROM audio_tracks
			UNION ALL SELECT album_key, root_pos, path, 'is_compilation', json_extract(track_metadata, '$.is_compilation') FROM audio_tracks
		),
		ranked_album_values AS (
			SELECT
				album_key,
				field,
				value,
				ROW_NUMBER() OVER (
					PARTITION BY album_key, field
					ORDER BY COUNT(*) DESC, MIN(root_pos), MIN(path), value
				) AS rank
			FROM album_values
			WHERE value IS NOT NULL
			GROUP BY album_key, field, value
		),
		album_keys AS (
			SELECT album_key
			FROM audio_tracks
			GROUP BY album_key
		)
		INSERT INTO library_albums (
			album_key,
			format,
			album,
			track_count,
			disc_count,
			release_date,
			original_release_date,
			recorded_date,
			release_year,
			original_release_year,
			is_compilation
		)
		SELECT
			album_keys.album_key,
			MAX(CASE WHEN field = 'format' THEN value END),
			MAX(CASE WHEN field = 'album' THEN value END),
			CAST(MAX(CASE WHEN field = 'track_count' THEN value END) AS INTEGER),
			CAST(MAX(CASE WHEN field = 'disc_count' THEN value END) AS INTEGER),
			MAX(CASE WHEN field = 'release_date' THEN value END),
			MAX(CASE WHEN field = 'original_release_date' THEN value END),
			MAX(CASE WHEN field = 'recorded_date' THEN value END),
			CAST(MAX(CASE WHEN field = 'release_year' THEN value END) AS INTEGER),
			CAST(MAX(CASE WHEN field = 'original_release_year' THEN value END) AS INTEGER),
			CAST(MAX(CASE WHEN field = 'is_compilation' THEN value END) AS INTEGER)
		FROM album_keys
		LEFT JOIN ranked_album_values
			ON ranked_album_values.album_key = album_keys.album_key
			AND ranked_album_values.rank = 1
		GROUP BY album_keys.album_key
		ON CONFLICT(album_key) DO UPDATE SET
			format = excluded.format,
			album = excluded.album,
			track_count = excluded.track_count,
			disc_count = excluded.disc_count,
			release_date = excluded.release_date,
			original_release_date = excluded.original_release_date,
			recorded_date = excluded.recorded_date,
			release_year = excluded.release_year,
			original_release_year = excluded.original_release_year,
			is_compilation = excluded.is_compilation;
	`, app.FileTypeAudio); err != nil {
		return fmt.Errorf("build library albums: %w", err)
	}

	return nil
}

func buildLibraryAlbumArtists(ctx context.Context, tx *sql.Tx) error {
	if _, err := tx.ExecContext(ctx, `
		INSERT OR IGNORE INTO library_album_artists (album_id, artist_key, artist)
		SELECT DISTINCT
			albums.id,
			artist_keys.value,
			artist_displays.value
		FROM scan_tracks AS s
		JOIN library_albums AS albums
			ON albums.album_key = json_extract(s.normalized_metadata, '$.album_key')
		JOIN json_each(s.normalized_metadata, '$.mapped_album_artist_keys') AS artist_keys
		JOIN json_each(s.normalized_metadata, '$.mapped_album_artist_displays') AS artist_displays
			ON artist_displays.key = artist_keys.key
		WHERE s.file_type = ?;
	`, app.FileTypeAudio); err != nil {
		return fmt.Errorf("build library album artists: %w", err)
	}

	return nil
}

func buildLibraryTracks(ctx context.Context, tx *sql.Tx) error {
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO library_tracks (
			root_id,
			album_id,
			ext,
			path,
			file_type,
			file_size,
			file_mtime_unix_nano,
			track_metadata,
			scan_error,
			title,
			artists,
			work,
			grouping,
			movement_name,
			movement_number,
			movement_count,
			show_movement,
			track_number,
			disc_number
		)
		SELECT
			roots.id,
			albums.id,
			s.ext,
			s.path,
			s.file_type,
			s.file_size,
			s.file_mtime_unix_nano,
			s.track_metadata,
			s.scan_error,
			json_extract(s.normalized_metadata, '$.track_display'),
			json_extract(s.normalized_metadata, '$.mapped_track_artist_displays'),
			NULLIF(json_extract(s.track_metadata, '$.work'), ''),
			NULLIF(json_extract(s.track_metadata, '$.grouping'), ''),
			NULLIF(json_extract(s.track_metadata, '$.movement_name'), ''),
			NULLIF(json_extract(s.track_metadata, '$.movement_number'), 0),
			NULLIF(json_extract(s.track_metadata, '$.movement_count'), 0),
			NULLIF(json_extract(s.track_metadata, '$.show_movement'), 0),
			NULLIF(json_extract(s.track_metadata, '$.track_number'), 0),
			NULLIF(json_extract(s.track_metadata, '$.disc_number'), 0)
		FROM scan_tracks AS s
		JOIN library_roots AS roots
			ON roots.pos = s.root_pos
		JOIN library_albums AS albums
			ON albums.album_key = json_extract(s.normalized_metadata, '$.album_key')
		WHERE s.file_type = ?;
	`, app.FileTypeAudio); err != nil {
		return fmt.Errorf("build library tracks: %w", err)
	}

	return nil
}

func buildLibraryTrackEmbeddedArtwork(ctx context.Context, tx *sql.Tx) error {
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO library_track_embedded_artwork (
			track_id,
			picture_type,
			artwork_content_type,
			artwork_content_size,
			artwork_bytes
		)
		SELECT
			tracks.id,
			artwork.picture_type,
			artwork.artwork_content_type,
			artwork.artwork_content_size,
			artwork.artwork_bytes
		FROM scan_artwork AS artwork
		JOIN scan_tracks AS s
			ON s.root_pos = artwork.root_pos
			AND s.path = artwork.path
			AND s.file_type = ?
		JOIN library_roots AS roots
			ON roots.pos = s.root_pos
		JOIN library_tracks AS tracks
			ON tracks.root_id = roots.id
			AND tracks.path = s.path
		ORDER BY tracks.id, artwork.artwork_pos;
	`, app.FileTypeAudio); err != nil {
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

	if _, err = tx.ExecContext(ctx, `
		DROP TABLE IF EXISTS scan_taxonomy_style_resolution;

		CREATE TEMP TABLE scan_taxonomy_style_resolution AS
		WITH ranked AS (
			SELECT
				style_key,
				canonical_style,
				ROW_NUMBER() OVER (
					PARTITION BY style_key COLLATE NOCASE
					ORDER BY taxonomy_id, pos
				) AS rank
			FROM (
				SELECT
					name AS style_key,
					name AS canonical_style,
					id AS taxonomy_id,
					0 AS pos
				FROM taxonomy
				WHERE kind = 'style'

				UNION ALL

				SELECT
					alias AS style_key,
					name AS canonical_style,
					id AS taxonomy_id,
					1 AS pos
				FROM taxonomy
				WHERE
					    kind = 'style'
					AND alias COLLATE NOCASE <> name COLLATE NOCASE
			)
		)
		SELECT
			ranked.style_key,
			ranked.canonical_style
		FROM ranked
		JOIN taxonomy_styles AS canonical_styles
			ON canonical_styles.style = ranked.canonical_style
		WHERE ranked.rank = 1;

		DROP TABLE IF EXISTS scan_track_genre_resolution;

		CREATE TEMP TABLE scan_track_genre_resolution AS
		WITH RECURSIVE
		split_delimiters(pos, delimiter) AS (
			SELECT
				CAST(tokens.key AS INTEGER) + 1,
				tokens.value
			FROM json_each(?) AS tokens
		),
		raw_genres AS (
			SELECT
				tracks.id AS track_id,
				s.path,
				genres.key AS genre_pos,
				genres.value AS input_genre
			FROM scan_tracks AS s
			JOIN library_roots AS roots
				ON roots.pos = s.root_pos
			JOIN library_tracks AS tracks
				ON tracks.root_id = roots.id
				AND tracks.path = s.path
			LEFT JOIN json_each(
				s.normalized_metadata,
				'$.genres'
			) AS genres
			WHERE s.file_type = ?
		),
		raw_resolutions AS (
			SELECT
				raw.track_id,
				raw.path,
				raw.genre_pos,
				raw.input_genre,
				tg.genre AS resolved_genre,
				tsr.canonical_style AS resolved_style
			FROM raw_genres AS raw
			LEFT JOIN taxonomy_genres AS tg
				ON tg.genre = raw.input_genre
			LEFT JOIN scan_taxonomy_style_resolution AS tsr
				ON tsr.style_key COLLATE NOCASE
					= raw.input_genre COLLATE NOCASE
				AND tg.genre IS NULL
		),
		normalized_genres(
			track_id,
			path,
			genre_pos,
			original_genre,
			delimiter_pos,
			normalized_genre
		) AS (
			SELECT
				raw.track_id,
				raw.path,
				raw.genre_pos,
				raw.input_genre,
				0,
				raw.input_genre
			FROM raw_resolutions AS raw
			WHERE raw.input_genre IS NOT NULL
				AND raw.resolved_genre IS NULL
				AND raw.resolved_style IS NULL
				AND EXISTS (
					SELECT 1
					FROM split_delimiters AS delimiters
					WHERE instr(
						raw.input_genre,
						delimiters.delimiter
					) > 0
				)

			UNION ALL

			SELECT
				normalized.track_id,
				normalized.path,
				normalized.genre_pos,
				normalized.original_genre,
				delimiters.pos,
				replace(
					normalized.normalized_genre,
					delimiters.delimiter,
					char(31)
				)
			FROM normalized_genres AS normalized
			JOIN split_delimiters AS delimiters
				ON delimiters.pos
					= normalized.delimiter_pos + 1
		),
		split_sources AS (
			SELECT
				track_id,
				path,
				genre_pos,
				original_genre,
				normalized_genre
			FROM normalized_genres
			WHERE delimiter_pos = (
				SELECT MAX(pos)
				FROM split_delimiters
			)
		),
		split_parts(
			track_id,
			path,
			genre_pos,
			original_genre,
			part_pos,
			part,
			rest
		) AS (
			SELECT
				track_id,
				path,
				genre_pos,
				original_genre,
				0,
				trim(substr(
					normalized_genre,
					1,
					instr(
						normalized_genre || char(31),
						char(31)
					) - 1
				)),
				substr(
					normalized_genre || char(31),
					instr(
						normalized_genre || char(31),
						char(31)
					) + 1
				)
			FROM split_sources

			UNION ALL

			SELECT
				track_id,
				path,
				genre_pos,
				original_genre,
				part_pos + 1,
				trim(substr(
					rest,
					1,
					instr(rest, char(31)) - 1
				)),
				substr(
					rest,
					instr(rest, char(31)) + 1
				)
			FROM split_parts
			WHERE rest <> ''
		),
		split_resolutions AS (
			SELECT
				parts.track_id,
				parts.path,
				parts.genre_pos,
				parts.original_genre,
				parts.part_pos,
				parts.part AS input_genre,
				tg.genre AS resolved_genre,
				tsr.canonical_style AS resolved_style
			FROM split_parts AS parts
			LEFT JOIN taxonomy_genres AS tg
				ON tg.genre = parts.part
			LEFT JOIN scan_taxonomy_style_resolution AS tsr
				ON tsr.style_key COLLATE NOCASE
					= parts.part COLLATE NOCASE
				AND tg.genre IS NULL
			WHERE parts.part <> ''
		),
		expandable_genres AS (
			SELECT
				track_id,
				genre_pos
			FROM split_resolutions
			GROUP BY
				track_id,
				genre_pos
			HAVING SUM(
				CASE
					WHEN resolved_genre IS NOT NULL
						OR resolved_style IS NOT NULL
						THEN 1
					ELSE 0
				END
			) > 0
		)
		SELECT
			raw.track_id,
			raw.path,
			raw.genre_pos,
			raw.input_genre,
			raw.resolved_genre,
			raw.resolved_style
		FROM raw_resolutions AS raw
		WHERE raw.input_genre IS NULL
			OR raw.resolved_genre IS NOT NULL
			OR raw.resolved_style IS NOT NULL
			OR NOT EXISTS (
				SELECT 1
				FROM expandable_genres AS expandable
				WHERE expandable.track_id = raw.track_id
					AND expandable.genre_pos = raw.genre_pos
			)

		UNION ALL

		SELECT
			split.track_id,
			split.path,
			split.genre_pos,
			split.input_genre,
			split.resolved_genre,
			split.resolved_style
		FROM split_resolutions AS split
		JOIN expandable_genres AS expandable
			ON expandable.track_id = split.track_id
			AND expandable.genre_pos = split.genre_pos;

		INSERT OR IGNORE INTO library_track_genres (
			track_id,
			genre
		)
		SELECT DISTINCT
			track_id,
			resolved_genre
		FROM scan_track_genre_resolution
		WHERE resolved_genre IS NOT NULL;

		INSERT OR IGNORE INTO library_track_genres (
			track_id,
			genre
		)
		SELECT
			track_id,
			?
		FROM scan_track_genre_resolution
		GROUP BY track_id
		HAVING SUM(
			CASE
				WHEN resolved_genre IS NOT NULL
					OR resolved_style IS NOT NULL
					THEN 1
				ELSE 0
			END
		) = 0;

		INSERT OR IGNORE INTO library_track_styles (
			track_id,
			style
		)
		SELECT DISTINCT
			track_id,
			resolved_style
		FROM scan_track_genre_resolution
		WHERE resolved_style IS NOT NULL;
	`, string(splitDelimitersJSON), app.FileTypeAudio, unknownItem); err != nil {
		err = fmt.Errorf(
			"build library track genres and styles: %w",
			err,
		)
		return
	}

	var rows *sql.Rows

	if rows, err = tx.QueryContext(ctx, `
		WITH summaries AS (
			SELECT
				track_id,
				MAX(path) AS path,
				COUNT(input_genre) AS input_count,
				SUM(CASE
					WHEN input_genre IS NOT NULL
						AND (
							resolved_genre IS NOT NULL
							OR resolved_style IS NOT NULL
						)
						THEN 1
					ELSE 0
				END) AS resolved_count,
				SUM(CASE
					WHEN input_genre IS NOT NULL
						AND resolved_genre IS NULL
						AND resolved_style IS NULL
						THEN 1
					ELSE 0
				END) AS unresolved_count,
				json_group_array(input_genre)
					FILTER (
						WHERE input_genre IS NOT NULL
					) AS input_genres_json,
				json_group_array(input_genre)
					FILTER (
						WHERE input_genre IS NOT NULL
							AND resolved_genre IS NULL
							AND resolved_style IS NULL
					) AS unresolved_genres_json,
				group_concat(quote(input_genre), ', ')
					FILTER (
						WHERE input_genre IS NOT NULL
							AND resolved_genre IS NULL
							AND resolved_style IS NULL
					) AS unresolved_display
			FROM scan_track_genre_resolution
			GROUP BY track_id
		)
		SELECT
			track_id,
			path,
			CASE
				WHEN input_count = 0 THEN 'empty'
				WHEN resolved_count = 0 THEN 'unresolved'
				WHEN unresolved_count > 0 THEN 'partial'
			END AS status,
			CASE
				WHEN input_count = 0
					THEN 'set to unknown since audio file genres was empty'
				WHEN resolved_count = 0
					THEN 'set to unknown since the following audio file genres could not resolve to either a canonical style or genre: ' || unresolved_display
				WHEN unresolved_count > 0
					THEN 'the following audio file genres could not resolve to either a canonical style or genre: ' || unresolved_display
			END AS log_message
		FROM summaries
		WHERE input_count = 0
			OR resolved_count = 0
			OR unresolved_count > 0
		ORDER BY track_id
	`); err != nil {
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
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO library_track_sidecars (
			track_id,
			stem,
			ext,
			path,
			file_size,
			file_mtime_unix_nano
		)
		SELECT
			tracks.id,
			sidecars.stem,
			sidecars.ext,
			sidecars.path,
			sidecars.file_size,
			sidecars.file_mtime_unix_nano
		FROM scan_tracks AS sidecars
		JOIN scan_tracks AS audio
			ON audio.root_pos = sidecars.root_pos
			AND audio.file_type = ?
			AND (
				(audio.path_dir = '.' AND sidecars.path_dir = '.')
				OR substr(sidecars.path, 1, length(audio.path_dir) + 1) = audio.path_dir || '/'
			)
		JOIN library_roots AS roots
			ON roots.pos = audio.root_pos
		JOIN library_tracks AS tracks
			ON tracks.root_id = roots.id
			AND tracks.path = audio.path
		WHERE sidecars.file_type = ?
		ORDER BY tracks.id, sidecars.path;
	`, app.FileTypeAudio, app.FileTypeSidecar); err != nil {
		return fmt.Errorf("build library track sidecars: %w", err)
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
		if err := tx.QueryRowContext(ctx, `SELECT CURRENT_TIMESTAMP`).Scan(&currentTimestamp); err != nil {
			return err
		}

		if err := SetAppMetadata(ctx, tx, int(app.LibraryGeneratedAt), currentTimestamp); err != nil {
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
	} else if err = SetAppMetadata(ctx, tx, int(app.LibrarySupportedExtensionsJson), string(b)); err != nil {
		return fmt.Errorf("setting app metadata key %d: %w", int(app.LibrarySupportedExtensionsJson), err)
	}

	return nil
}
