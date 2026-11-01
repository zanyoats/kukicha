-- Called in the transaction that publishes the library, after staging succeeds.
INSERT INTO unrecognized_tracks (
    config_id, uploaded_path, path_dir, stem, ext, file_size,
    file_mtime_unix_nano, duration_seconds, track_metadata
)
SELECT config_id, path, path_dir, stem, ext, file_size,
    file_mtime_unix_nano, duration_seconds, track_metadata
FROM scan_unrecognized_tracks WHERE true
ON CONFLICT (uploaded_path) DO NOTHING;

INSERT INTO unrecognized_artwork
SELECT tracks.path, artwork.artwork_pos, artwork.picture_type,
    artwork.artwork_content_type, artwork.artwork_content_size, artwork.artwork_bytes,
    covers.thumbnail_cache_name
FROM scan_unrecognized_tracks AS tracks
JOIN scan_artwork AS artwork USING (root_pos, path)
LEFT JOIN scan_artwork_cover AS covers USING (root_pos, path, artwork_pos)
WHERE true
ON CONFLICT (uploaded_path, artwork_pos) DO NOTHING;

DELETE FROM unrecognized_tracks
WHERE uploaded_path IN (
    SELECT uploaded_path FROM scan_ready_unrecognized_tracks
);

DELETE FROM unrecognized_tracks
WHERE config_id IN (SELECT config_id FROM library_roots WHERE state = 'pending_removal');

-- Retain sidecars while any unresolved track can use them, including sidecars
-- already attached to another album. Promotion of one track must not lose them.
WITH sidecars AS (
    SELECT *, 'cover' AS kind FROM scan_sidecar_covers
    UNION ALL
    SELECT *, NULL AS thumbnail_cache_name, 'other' AS kind FROM scan_sidecar_others
)
INSERT INTO unrecognized_sidecars
SELECT roots.config_id, sidecars.path, sidecars.kind, sidecars.path_dir,
    sidecars.stem, sidecars.ext, sidecars.file_size, sidecars.file_mtime_unix_nano,
    sidecars.thumbnail_cache_name
FROM sidecars JOIN scan_roots AS roots USING (root_pos)
WHERE EXISTS (
    SELECT 1 FROM unrecognized_tracks AS tracks
    WHERE tracks.config_id = roots.config_id
        AND substr(sidecars.path, 1, length(tracks.path_dir)) = tracks.path_dir
)
ON CONFLICT (config_id, path) DO UPDATE SET
    kind = excluded.kind,
    thumbnail_cache_name = excluded.thumbnail_cache_name;

DELETE FROM unrecognized_sidecars AS sidecars
WHERE NOT EXISTS (
    SELECT 1 FROM unrecognized_tracks AS tracks
    WHERE tracks.config_id = sidecars.config_id
        AND substr(sidecars.path, 1, length(tracks.path_dir)) = tracks.path_dir
);
