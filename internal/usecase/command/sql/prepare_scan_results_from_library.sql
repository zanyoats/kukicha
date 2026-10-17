INSERT INTO scan_roots (root_pos, config_id)
SELECT CAST(key AS INTEGER), value
FROM json_each(:root_config_ids);

INSERT INTO scan_tracks (
    root_pos, config_id, album_key, path_dir, stem, ext, path,
    file_size, file_mtime_unix_nano, duration_seconds,
    track_metadata, normalized_metadata
)
SELECT scanned.root_pos, roots.config_id, albums.album_key, '', tracks.stem, tracks.ext,
    tracks.path, tracks.file_size, tracks.file_mtime_unix_nano,
    tracks.duration_seconds, tracks.track_metadata, '{}'
FROM library_tracks AS tracks
JOIN library_roots AS roots ON roots.id = tracks.root_id
JOIN scan_roots AS scanned ON scanned.config_id = roots.config_id
JOIN library_album_ids AS albums ON albums.id = tracks.album_id;
