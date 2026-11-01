INSERT INTO library_tracks (
    id,
    root_id,
    album_id,
    stem,
    ext,
    path_dir,
    path,
    file_size,
    file_mtime_unix_nano,
    duration_seconds,
    track_metadata,
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
    s.stem,
    s.ext,
    s.path_dir,
    s.path,
    s.file_size,
    s.file_mtime_unix_nano,
    s.duration_seconds,
    s.track_metadata,
    json_extract(normalized.normalized_metadata, '$.track_display'),
    json_extract(normalized.normalized_metadata, '$.raw_track_artist_displays'),
    NULLIF(json_extract(s.track_metadata, '$.work'), ''),
    NULLIF(json_extract(s.track_metadata, '$.grouping'), ''),
    NULLIF(json_extract(s.track_metadata, '$.movement_name'), ''),
    NULLIF(json_extract(s.track_metadata, '$.movement_number'), 0),
    NULLIF(json_extract(s.track_metadata, '$.movement_count'), 0),
    NULLIF(json_extract(s.track_metadata, '$.show_movement'), 0),
    NULLIF(json_extract(s.track_metadata, '$.track_number'), 0),
    NULLIF(json_extract(s.track_metadata, '$.disc_number'), 0)
FROM scan_tracks AS s
JOIN scan_track_normalized_metadata AS normalized
    ON normalized.root_pos = s.root_pos AND normalized.path = s.path
JOIN library_track_ids AS track_ids
    ON track_ids.path = s.path
JOIN library_roots AS roots
    ON roots.config_id = s.config_id
JOIN library_album_ids AS album_ids
    ON album_ids.album_key = s.album_key
JOIN library_albums AS albums
    ON albums.id = album_ids.id
WHERE true
ON CONFLICT (id) DO UPDATE SET
    title = excluded.title,
    artists = excluded.artists,
    work = excluded.work,
    grouping = excluded.grouping,
    movement_name = excluded.movement_name,
    movement_number = excluded.movement_number,
    movement_count = excluded.movement_count,
    show_movement = excluded.show_movement,
    track_number = excluded.track_number,
    disc_number = excluded.disc_number;
