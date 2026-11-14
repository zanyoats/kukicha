WITH sidecars AS (
    SELECT
        root_pos,
        path_dir,
        stem,
        ext,
        path,
        file_size,
        file_mtime_unix_nano,
        thumbnail_cache_name,
        'cover' AS kind
    FROM scan_sidecar_covers

    UNION ALL

    SELECT
        root_pos,
        path_dir,
        stem,
        ext,
        path,
        file_size,
        file_mtime_unix_nano,
        NULL AS thumbnail_cache_name,
        'other' AS kind
    FROM scan_sidecar_others
)
INSERT INTO library_album_sidecars (
    album_id,
    root_id,
    kind,
    stem,
    ext,
    path_dir,
    path,
    file_size,
    file_mtime_unix_nano,
    thumbnail_cache_name
)
SELECT DISTINCT
    tracks.album_id,
    roots.id,
    sidecars.kind,
    sidecars.stem,
    sidecars.ext,
    sidecars.path_dir,
    sidecars.path,
    sidecars.file_size,
    sidecars.file_mtime_unix_nano,
    sidecars.thumbnail_cache_name
FROM sidecars
JOIN scan_tracks AS audio
    ON audio.root_pos = sidecars.root_pos
    AND substr(sidecars.path, 1, length(audio.path_dir)) = audio.path_dir
    -- Exclude loose tracks at the storage root or generated import root.
    AND audio.path_dir <> ''
    AND (audio.path_dir NOT GLOB '????-??-??T??????Z/job-[0-9]*/'
        OR audio.path_dir GLOB '????-??-??T??????Z/job-[0-9]*/*/')
JOIN library_roots AS roots
    ON roots.config_id = audio.config_id
JOIN library_tracks AS tracks
    ON tracks.root_id = roots.id
    AND tracks.path = audio.path
ORDER BY tracks.album_id, roots.id, sidecars.path;
