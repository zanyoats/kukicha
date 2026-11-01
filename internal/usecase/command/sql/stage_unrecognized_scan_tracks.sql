-- Snapshot corrections so concurrent edits cannot be silently consumed.
INSERT INTO scan_ready_unrecognized_tracks
SELECT pending.* FROM unrecognized_tracks AS pending
JOIN scan_roots USING (config_id)
WHERE pending.status = 'ready';

INSERT INTO scan_tracks (
    root_pos, config_id, album_key, path_dir, stem, ext, path,
    file_size, file_mtime_unix_nano, duration_seconds, track_metadata
)
SELECT roots.root_pos, pending.config_id,
    json_array(trim(album), json((SELECT json_group_array(artist) FROM (
        SELECT trim(value) AS artist FROM json_each(pending.artists) ORDER BY key
    )))),
    path_dir, stem, ext, uploaded_path, file_size, file_mtime_unix_nano, duration_seconds,
    -- Corrections become the saved tags, so later rebuilds retain them.
    json_set(track_metadata, '$.album', trim(album),
        '$.album_artists', json(artists), '$.artists', json(artists))
FROM scan_ready_unrecognized_tracks AS pending
JOIN scan_roots AS roots USING (config_id);

INSERT INTO scan_artwork
SELECT roots.root_pos, artwork.uploaded_path, artwork.artwork_pos,
    artwork.picture_type, artwork.artwork_content_type, artwork.artwork_content_size, artwork.artwork_bytes
FROM unrecognized_artwork AS artwork
JOIN scan_ready_unrecognized_tracks USING (uploaded_path)
JOIN scan_roots AS roots USING (config_id);

INSERT INTO scan_artwork_cover
SELECT roots.root_pos, artwork.uploaded_path, artwork.artwork_pos,
    artwork.picture_type, artwork.thumbnail_cache_name
FROM unrecognized_artwork AS artwork
JOIN scan_ready_unrecognized_tracks USING (uploaded_path)
JOIN scan_roots AS roots USING (config_id)
WHERE artwork.thumbnail_cache_name IS NOT NULL
    AND artwork.picture_type IN ('front_cover', 'other');

INSERT OR IGNORE INTO scan_sidecar_covers
SELECT roots.root_pos, sidecars.path_dir, sidecars.stem, sidecars.ext, sidecars.path,
    sidecars.file_size, sidecars.file_mtime_unix_nano, sidecars.thumbnail_cache_name
FROM unrecognized_sidecars AS sidecars
JOIN scan_roots AS roots USING (config_id)
WHERE sidecars.kind = 'cover';

INSERT OR IGNORE INTO scan_sidecar_others
SELECT roots.root_pos, sidecars.path_dir, sidecars.stem, sidecars.ext, sidecars.path,
    sidecars.file_size, sidecars.file_mtime_unix_nano
FROM unrecognized_sidecars AS sidecars
JOIN scan_roots AS roots USING (config_id)
WHERE sidecars.kind = 'other';
