INSERT INTO library_tracks (
    id,
    root_id,
    album_id,
    ext,
    path,
    file_size,
    file_mtime_unix_nano,
    duration_seconds,
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
    track_ids.id,
    roots.id,
    albums.id,
    s.ext,
    s.path,
    s.file_size,
    s.file_mtime_unix_nano,
    s.duration_seconds,
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
JOIN library_track_ids AS track_ids
    ON track_ids.root_path = s.root_path
    AND track_ids.path = s.path
JOIN library_roots AS roots
    ON roots.path = s.root_path
JOIN library_album_ids AS album_ids
    ON album_ids.album_key = s.album_key
JOIN library_albums AS albums
    ON albums.id = album_ids.id;
