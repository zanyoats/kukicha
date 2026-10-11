WITH
sidecar_candidates AS (
    SELECT
        tracks.id AS track_id,
        covers.thumbnail_cache_name,
        'sidecar' AS source,
        0 AS source_priority,
        0 AS candidate_priority,
        0 AS count_priority,
        0 AS id_priority,
        covers.path AS text_priority
    FROM scan_tracks AS scanned_tracks
    JOIN scan_sidecar_covers AS covers
        ON covers.root_pos = scanned_tracks.root_pos
        AND (
            (
                scanned_tracks.path_dir = ''
                AND covers.path_dir = ''
            )
            OR (scanned_tracks.path_dir != '' AND substr(
                covers.path,
                1,
                length(scanned_tracks.path_dir)
            ) = scanned_tracks.path_dir)
        )
    JOIN library_roots AS roots
        ON roots.config_id = scanned_tracks.config_id
    JOIN library_tracks AS tracks
        ON tracks.root_id = roots.id
        AND tracks.path = scanned_tracks.path
    WHERE covers.thumbnail_cache_name IS NOT NULL
),
embedded_candidates AS (
    SELECT
        tracks.id AS track_id,
        artwork.thumbnail_cache_name,
        'embedded' AS source,
        1 AS source_priority,
        CASE artwork.picture_type
            WHEN 'front_cover' THEN 0
            WHEN 'other' THEN 1
        END AS candidate_priority,
        0 AS count_priority,
        artwork.artwork_pos AS id_priority,
        '' AS text_priority
    FROM scan_artwork_cover AS artwork
    JOIN scan_tracks AS scanned_tracks
        ON scanned_tracks.root_pos = artwork.root_pos
        AND scanned_tracks.path = artwork.path
    JOIN library_roots AS roots
        ON roots.config_id = scanned_tracks.config_id
    JOIN library_tracks AS tracks
        ON tracks.root_id = roots.id
        AND tracks.path = scanned_tracks.path
    WHERE artwork.thumbnail_cache_name IS NOT NULL
        AND artwork.picture_type IN ('front_cover', 'other')
),
all_candidates AS (
    SELECT * FROM sidecar_candidates
    UNION ALL SELECT * FROM embedded_candidates
),
ranked_candidates AS (
    SELECT
        candidates.track_id,
        candidates.thumbnail_cache_name,
        candidates.source,
        ROW_NUMBER() OVER (
            PARTITION BY candidates.track_id
            ORDER BY
                candidates.source_priority,
                candidates.candidate_priority,
                candidates.count_priority,
                candidates.id_priority,
                candidates.text_priority COLLATE BINARY,
                candidates.thumbnail_cache_name
        ) AS rank
    FROM all_candidates AS candidates
    WHERE EXISTS (
        SELECT 1
        FROM thumbnail_cache AS small_thumbnail
        WHERE small_thumbnail.name = candidates.thumbnail_cache_name
            AND small_thumbnail.width_px = :artwork_width_small
    )
        AND EXISTS (
            SELECT 1
            FROM thumbnail_cache AS large_thumbnail
            WHERE large_thumbnail.name = candidates.thumbnail_cache_name
                AND large_thumbnail.width_px = :artwork_width_large
        )
)
INSERT OR IGNORE INTO library_track_artwork (
    track_id,
    thumbnail_cache_id,
    source
)
SELECT
    ranked.track_id,
    thumbnail.id,
    ranked.source
FROM ranked_candidates AS ranked
JOIN thumbnail_cache AS thumbnail
    ON thumbnail.name = ranked.thumbnail_cache_name
    AND thumbnail.width_px IN (
        :artwork_width_small,
        :artwork_width_large
    )
WHERE ranked.rank = 1
ORDER BY ranked.track_id, thumbnail.width_px;
