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
WHERE s.file_type = :audio_file_type;
