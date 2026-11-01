-- Preserve original tags; derive identity independently of later normalization.
-- Identity is case-sensitive; trim ordinary surrounding spaces only.
WITH album_identity AS (
    SELECT
        COALESCE(NULLIF(trim(json_extract(:track_metadata, '$.album')), ''),
                 :unknown_item) AS album,
        CASE
            WHEN json_array_length(:track_metadata, '$.album_artists') > 0
                THEN json_extract(:track_metadata, '$.album_artists')
            WHEN json_array_length(:track_metadata, '$.artists') > 0
                THEN json_extract(:track_metadata, '$.artists')
            ELSE json_array(:unknown_item)
        END AS artists
)
INSERT INTO scan_unrecognized_tracks (
    root_pos,
    config_id,
    album_key,
    path_dir,
    stem,
    ext,
    path,
    file_size,
    file_mtime_unix_nano,
    duration_seconds,
    track_metadata
)
VALUES (
    :root_pos,
    :config_id,
    (SELECT json_array(album, json((
        SELECT json_group_array(artist) FROM (
            SELECT trim(value) AS artist
            FROM json_each(artists) ORDER BY key
        )
    ))) FROM album_identity),
    :path_dir,
    :stem,
    :ext,
    :path,
    :file_size,
    :file_mtime_unix_nano,
    :duration_seconds,
    json(:track_metadata)
);
