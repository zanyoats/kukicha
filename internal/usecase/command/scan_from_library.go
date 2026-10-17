package command

import (
	"context"
	"database/sql"
	_ "embed"
	"encoding/json"
	"fmt"

	"github.com/zanyoats/kukicha/internal/app"
)

//go:embed sql/prepare_scan_results_from_library.sql
var prepareScanResultsFromLibrarySQL string

// PrepareScanResultsFromLibrary stages the current library without reading files.
// The callback reapplies normalization rules without introducing a dependency
// from command back to scan. It is called with no open result set on conn.
func PrepareScanResultsFromLibrary(
	ctx context.Context,
	conn *sql.Conn,
	conf *app.AppConfig,
	normalize func(*app.ScannedFile) error,
) error {
	if err := PrepareScanResults(ctx, conn, conf.Settings.Server.Database.CacheSizeKiB); err != nil {
		return err
	}
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

	// Bound memory usage and close each batch before normalization, which may
	// itself write artist mappings using this connection.
	var afterID int64
	for {
		rows, err := conn.QueryContext(ctx, `
			SELECT tracks.id, scanned.root_pos, scanned.path, scanned.album_key, scanned.track_metadata,
			    tracks.ext, tracks.stem
			FROM library_tracks AS tracks
			JOIN library_roots AS roots ON roots.id = tracks.root_id
			JOIN scan_roots AS sr ON sr.config_id = roots.config_id
			JOIN scan_tracks AS scanned
				ON scanned.root_pos = sr.root_pos AND scanned.path = tracks.path
			WHERE tracks.id > ? ORDER BY tracks.id LIMIT 256`, afterID)
		if err != nil {
			return err
		}
		var batch []app.ScannedFile
		var albumKeys []string
		for rows.Next() {
			var file app.ScannedFile
			var metadata, albumKey string
			if err := rows.Scan(
				&afterID, &file.RootPos, &file.Path, &albumKey,
				&metadata, &file.Ext, &file.Stem,
			); err != nil {
				rows.Close()
				return err
			}
			file.Metadata = &app.TrackMetadata{}
			if err := json.Unmarshal([]byte(metadata), file.Metadata); err != nil {
				rows.Close()
				return fmt.Errorf("decode saved metadata for %q: %w", file.Path, err)
			}
			file.NormalizedMetadata = &app.NormalizedTrackMetadata{}
			batch = append(batch, file)
			albumKeys = append(albumKeys, albumKey)
		}
		err = rows.Err()
		closeErr := rows.Close()
		if err != nil {
			return err
		}
		if closeErr != nil {
			return closeErr
		}
		if len(batch) == 0 {
			return nil
		}
		for i := range batch {
			file := &batch[i]
			if err := normalize(file); err != nil {
				return fmt.Errorf("normalize saved track %q: %w", file.Path, err)
			}
			if file.NormalizedMetadata.AlbumKey != albumKeys[i] {
				return fmt.Errorf("album key changed for %q; run sync to rebuild album identities", file.Path)
			}
			metadata, err := json.Marshal(file.NormalizedMetadata)
			if err != nil {
				return err
			}
			if _, err := conn.ExecContext(ctx, `
				UPDATE scan_tracks SET normalized_metadata = ?
				WHERE root_pos = ? AND path = ?`,
				string(metadata),
				file.RootPos, file.Path,
			); err != nil {
				return err
			}
		}
	}
}
