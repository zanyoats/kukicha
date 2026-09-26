-- Execute inside a transaction so replacement is atomic.
DELETE FROM library_track_artwork WHERE source = 'itunes';

WITH
current_itunes_candidates AS (
    SELECT
        tracks.id AS track_id,
        cache.thumbnail_cache_name,
        2 AS source_priority,
        candidates.candidate_priority,
        0 AS count_priority,
        tracks.id AS id_priority,
        candidates.lookup_id AS text_priority
    FROM library_tracks AS tracks
    JOIN (
        SELECT
            id AS track_id,
            0 AS candidate_priority,
            CAST(json_extract(track_metadata, '$.itunes_store_album_id') AS TEXT) AS lookup_id,
            'album' AS lookup_kind
        FROM library_tracks
        UNION ALL
        SELECT
            id,
            1,
            CAST(json_extract(track_metadata, '$.itunes_store_track_id') AS TEXT),
            'track'
        FROM library_tracks
    ) AS candidates
        ON candidates.track_id = tracks.id
    JOIN itunes_lookup_image_cache AS cache
        ON cache.cache_key =
            candidates.lookup_kind || ':' || candidates.lookup_id
    WHERE CAST(candidates.lookup_id AS INTEGER) > 0
        AND cache.result_kind = 'hit'
        AND cache.thumbnail_cache_name IS NOT NULL
),
sibling_itunes_candidates AS (
    SELECT
        current_track.id AS track_id,
        cache.thumbnail_cache_name,
        2 AS source_priority,
        2 AS candidate_priority,
        -COUNT(*) AS count_priority,
        MIN(siblings.id) AS id_priority,
        CAST(
            json_extract(
                siblings.track_metadata,
                '$.itunes_store_album_id'
            )
            AS TEXT
        ) AS text_priority
    FROM library_tracks AS current_track
    JOIN library_tracks AS siblings
        ON siblings.album_id = current_track.album_id
        AND siblings.id != current_track.id
    JOIN itunes_lookup_image_cache AS cache
        ON cache.cache_key = 'album:' || CAST(
            json_extract(
                siblings.track_metadata,
                '$.itunes_store_album_id'
            )
            AS TEXT
        )
    WHERE CAST(
        json_extract(
            siblings.track_metadata,
            '$.itunes_store_album_id'
        )
        AS INTEGER
    ) > 0
        AND CAST(
            json_extract(
                siblings.track_metadata,
                '$.itunes_store_album_id'
            )
            AS INTEGER
        ) != COALESCE(
            CAST(
                json_extract(
                    current_track.track_metadata,
                    '$.itunes_store_album_id'
                )
                AS INTEGER
            ),
            0
        )
        AND cache.result_kind = 'hit'
        AND cache.thumbnail_cache_name IS NOT NULL
    GROUP BY
        current_track.id,
        cache.thumbnail_cache_name,
        CAST(
            json_extract(
                siblings.track_metadata,
                '$.itunes_store_album_id'
            )
            AS TEXT
        )
),
all_candidates AS (
    SELECT * FROM current_itunes_candidates
    UNION ALL SELECT * FROM sibling_itunes_candidates
),
ranked_candidates AS (
    SELECT
        candidates.track_id,
        candidates.thumbnail_cache_name,
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
INSERT INTO library_track_artwork (
    track_id,
    thumbnail_cache_id,
    source
)
SELECT
    ranked.track_id,
    thumbnail.id,
    'itunes'
FROM ranked_candidates AS ranked
JOIN thumbnail_cache AS thumbnail
    ON thumbnail.name = ranked.thumbnail_cache_name
    AND thumbnail.width_px IN (
        :artwork_width_small,
        :artwork_width_large
    )
WHERE ranked.rank = 1
    AND NOT EXISTS (
        SELECT 1
        FROM library_track_artwork AS artwork
        WHERE artwork.track_id = ranked.track_id
            AND artwork.source IN ('sidecar', 'embedded')
    )
ORDER BY ranked.track_id, thumbnail.width_px;
