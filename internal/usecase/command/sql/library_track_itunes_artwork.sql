WITH
current_track AS (
    SELECT
        id,
        album_id,
        track_metadata
    FROM library_tracks
    WHERE id = :track_id
),
lookup_candidates AS (
    SELECT
        0 AS priority,
        0 AS sibling_count,
        id AS first_track_id,
        'album' AS lookup_kind,
        CAST(
            json_extract(track_metadata, '$.itunes_store_album_id')
            AS TEXT
        ) AS lookup_id
    FROM current_track

    UNION ALL

    SELECT
        1 AS priority,
        0 AS sibling_count,
        id AS first_track_id,
        'track' AS lookup_kind,
        CAST(
            json_extract(track_metadata, '$.itunes_store_track_id')
            AS TEXT
        ) AS lookup_id
    FROM current_track

    UNION ALL

    SELECT
        2 AS priority,
        COUNT(*) AS sibling_count,
        MIN(siblings.id) AS first_track_id,
        'album' AS lookup_kind,
        CAST(
            json_extract(
                siblings.track_metadata,
                '$.itunes_store_album_id'
            )
            AS TEXT
        ) AS lookup_id
    FROM current_track
    JOIN library_tracks AS siblings
        ON siblings.album_id = current_track.album_id
        AND siblings.id != current_track.id
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
    GROUP BY CAST(
        json_extract(
            siblings.track_metadata,
            '$.itunes_store_album_id'
        )
        AS TEXT
    )
)
SELECT cache.artwork_bytes
FROM lookup_candidates AS candidates
JOIN itunes_lookup_image_cache AS cache
    ON cache.cache_key =
        candidates.lookup_kind || ':' || candidates.lookup_id
WHERE CAST(candidates.lookup_id AS INTEGER) > 0
    AND cache.result_kind = 'hit'
ORDER BY
    candidates.priority,
    candidates.sibling_count DESC,
    candidates.first_track_id,
    candidates.lookup_id;
