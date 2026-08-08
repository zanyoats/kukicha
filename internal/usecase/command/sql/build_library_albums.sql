WITH
audio_tracks AS (
    SELECT
        album_key,
        root_pos,
        path,
        track_metadata,
        normalized_metadata
    FROM scan_tracks
    WHERE file_type = :audio_file_type
),
album_values AS (
    SELECT album_key, root_pos, path, 'format' AS field, NULLIF(json_extract(track_metadata, '$.format'), '') AS value FROM audio_tracks
    UNION ALL SELECT album_key, root_pos, path, 'album', NULLIF(json_extract(normalized_metadata, '$.album_display'), '') FROM audio_tracks
    UNION ALL SELECT album_key, root_pos, path, 'track_count', NULLIF(json_extract(track_metadata, '$.track_count'), 0) FROM audio_tracks
    UNION ALL SELECT album_key, root_pos, path, 'disc_count', NULLIF(json_extract(track_metadata, '$.disc_count'), 0) FROM audio_tracks
    UNION ALL SELECT album_key, root_pos, path, 'release_date', NULLIF(json_extract(track_metadata, '$.release_date'), '') FROM audio_tracks
    UNION ALL SELECT album_key, root_pos, path, 'original_release_date', NULLIF(json_extract(track_metadata, '$.original_release_date'), '') FROM audio_tracks
    UNION ALL SELECT album_key, root_pos, path, 'recorded_date', NULLIF(json_extract(track_metadata, '$.recorded_date'), '') FROM audio_tracks
    UNION ALL SELECT album_key, root_pos, path, 'release_year', NULLIF(json_extract(track_metadata, '$.release_year'), 0) FROM audio_tracks
    UNION ALL SELECT album_key, root_pos, path, 'original_release_year', NULLIF(json_extract(track_metadata, '$.original_release_year'), 0) FROM audio_tracks
    UNION ALL SELECT album_key, root_pos, path, 'release_country', NULLIF(json_extract(track_metadata, '$.release_country'), '') FROM audio_tracks
    UNION ALL SELECT album_key, root_pos, path, 'label', NULLIF(json_extract(track_metadata, '$.label'), '') FROM audio_tracks
    UNION ALL SELECT album_key, root_pos, path, 'composer', NULLIF(json_extract(track_metadata, '$.composer'), '') FROM audio_tracks
    UNION ALL SELECT album_key, root_pos, path, 'copyright', NULLIF(json_extract(track_metadata, '$.copyright'), '') FROM audio_tracks
    UNION ALL SELECT album_key, root_pos, path, 'purchase_date', NULLIF(json_extract(track_metadata, '$.purchase_date'), '') FROM audio_tracks
    UNION ALL SELECT album_key, root_pos, path, 'is_compilation', json_extract(track_metadata, '$.is_compilation') FROM audio_tracks
),
ranked_album_values AS (
    SELECT
        album_key,
        field,
        value,
        ROW_NUMBER() OVER (
            PARTITION BY album_key, field
            ORDER BY COUNT(*) DESC, MIN(root_pos), MIN(path), value
        ) AS rank
    FROM album_values
    WHERE value IS NOT NULL
    GROUP BY album_key, field, value
),
album_keys AS (
    SELECT album_key
    FROM audio_tracks
    GROUP BY album_key
)
INSERT INTO library_albums (
    album_key,
    format,
    album,
    track_count,
    disc_count,
    release_date,
    original_release_date,
    recorded_date,
    release_year,
    original_release_year,
    release_country,
    label,
    composer,
    copyright,
    purchase_date,
    is_compilation
)
SELECT
    album_keys.album_key,
    MAX(CASE WHEN field = 'format' THEN value END),
    MAX(CASE WHEN field = 'album' THEN value END),
    CAST(MAX(CASE WHEN field = 'track_count' THEN value END) AS INTEGER),
    CAST(MAX(CASE WHEN field = 'disc_count' THEN value END) AS INTEGER),
    MAX(CASE WHEN field = 'release_date' THEN value END),
    MAX(CASE WHEN field = 'original_release_date' THEN value END),
    MAX(CASE WHEN field = 'recorded_date' THEN value END),
    CAST(MAX(CASE WHEN field = 'release_year' THEN value END) AS INTEGER),
    CAST(MAX(CASE WHEN field = 'original_release_year' THEN value END) AS INTEGER),
    MAX(CASE WHEN field = 'release_country' THEN value END),
    MAX(CASE WHEN field = 'label' THEN value END),
    MAX(CASE WHEN field = 'composer' THEN value END),
    MAX(CASE WHEN field = 'copyright' THEN value END),
    MAX(CASE WHEN field = 'purchase_date' THEN value END),
    CAST(MAX(CASE WHEN field = 'is_compilation' THEN value END) AS INTEGER)
FROM album_keys
LEFT JOIN ranked_album_values
    ON ranked_album_values.album_key = album_keys.album_key
    AND ranked_album_values.rank = 1
GROUP BY album_keys.album_key
ON CONFLICT(album_key) DO UPDATE SET
    format = excluded.format,
    album = excluded.album,
    track_count = excluded.track_count,
    disc_count = excluded.disc_count,
    release_date = excluded.release_date,
    original_release_date = excluded.original_release_date,
    recorded_date = excluded.recorded_date,
    release_year = excluded.release_year,
    original_release_year = excluded.original_release_year,
    release_country = excluded.release_country,
    label = excluded.label,
    composer = excluded.composer,
    copyright = excluded.copyright,
    purchase_date = excluded.purchase_date,
    is_compilation = excluded.is_compilation;
