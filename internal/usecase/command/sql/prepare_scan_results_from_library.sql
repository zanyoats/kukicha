-- Include stored roots even when they are no longer configured. Rebuilds must
-- preserve their saved resources so restoring availability does not require imports.
-- Explicitly dropped roots and all their resources are excluded from staging.
INSERT INTO scan_roots (root_pos, config_id)
SELECT ROW_NUMBER() OVER (ORDER BY config_id COLLATE BINARY) - 1, config_id
FROM (
    SELECT value AS config_id FROM json_each(:root_config_ids)
    UNION
    SELECT config_id FROM library_roots
    UNION
    SELECT config_id FROM unrecognized_tracks
) AS candidates
WHERE NOT EXISTS (
    SELECT 1 FROM library_roots AS removed
    WHERE removed.config_id = candidates.config_id AND removed.state = 'pending_removal'
);

INSERT INTO scan_tracks (
    root_pos, config_id, album_key, path_dir, stem, ext, path,
    file_size, file_mtime_unix_nano, duration_seconds,
    track_metadata
)
SELECT scanned.root_pos, roots.config_id, albums.album_key, tracks.path_dir, tracks.stem, tracks.ext,
    tracks.path, tracks.file_size, tracks.file_mtime_unix_nano,
    tracks.duration_seconds, tracks.track_metadata
FROM library_tracks AS tracks
JOIN library_roots AS roots ON roots.id = tracks.root_id
JOIN scan_roots AS scanned ON scanned.config_id = roots.config_id
JOIN library_album_ids AS albums ON albums.id = tracks.album_id;

INSERT INTO scan_artwork
SELECT s.root_pos, s.path, a.id, a.picture_type, a.artwork_content_type, a.artwork_content_size, a.artwork_bytes
FROM library_track_embedded_artwork a
JOIN library_tracks t ON t.id = a.track_id
JOIN library_roots r ON r.id = t.root_id
JOIN scan_tracks s ON s.config_id = r.config_id AND s.path = t.path;

INSERT INTO scan_saved_track_artwork
SELECT a.track_id, a.thumbnail_cache_id, a.source
FROM library_track_artwork a
JOIN library_tracks t ON t.id = a.track_id
JOIN library_roots r ON r.id = t.root_id
JOIN scan_tracks s ON s.config_id = r.config_id AND s.path = t.path;

INSERT OR IGNORE INTO scan_sidecar_covers
SELECT s.root_pos, c.path_dir, c.stem, c.ext, c.path, c.file_size, c.file_mtime_unix_nano, c.thumbnail_cache_name
FROM library_album_sidecars c
JOIN library_roots r ON r.id = c.root_id JOIN scan_roots s ON s.config_id = r.config_id
WHERE c.kind = 'cover';

INSERT OR IGNORE INTO scan_sidecar_others
SELECT s.root_pos, c.path_dir, c.stem, c.ext, c.path, c.file_size, c.file_mtime_unix_nano
FROM library_album_sidecars c
JOIN library_roots r ON r.id = c.root_id JOIN scan_roots s ON s.config_id = r.config_id
WHERE c.kind = 'other';

-- Snapshot the configured sources before network work, without holding query rows open.
INSERT INTO scan_album_override_sources
SELECT album_key, provider, entity_type, entity_id, source_url
FROM album_metadata_overrides;
