-- Each selected track can produce:
--  * 1 candidate from its own album lookup ID.
--  * 1 candidate from its own track lookup ID.
--  * 1 candidate per distinct eligible album lookup ID found on sibling tracks,
--    excluding its own album lookup ID.
--
-- If the selected tracks are
-- | Track ID | Path           | iTunes album ID | iTunes track ID |
-- | 101      | album/01.mp3   | 700             | 9001            |
-- | 102      | album/02.mp3   | 701             | 9002            |
-- | 103      | album/03.mp3   | 701             | —               |
--
-- ... and we batch [101-102]; it will  be expanded for like
-- | track_id | path           | lookup_kind | lookup_id |
-- | 101      | album/01.mp3   | album       | 700       |
-- | 101      | album/01.mp3   | track       | 9001      |
-- | 101      | album/01.mp3   | album       | 701       |
-- | 102      | album/02.mp3   | album       | 701       |
-- | 102      | album/02.mp3   | track       | 9002      |
-- | 102      | album/02.mp3   | album       | 700       |
--
WITH selected_tracks AS (
    SELECT * FROM library_tracks WHERE id > :after_id AND id <= :last_id
), candidates AS (
    SELECT -- track itunes store album id
        id AS track_id,
        'album' AS lookup_kind,
        CAST(json_extract(track_metadata, '$.itunes_store_album_id') AS TEXT) AS lookup_id,
        0 AS priority,
        0 AS frequency,
        id AS first_id
    FROM selected_tracks
    UNION ALL
    SELECT -- track itunes store track id
        id,
        'track',
        CAST(json_extract(track_metadata, '$.itunes_store_track_id') AS TEXT),
        1,
        0,
        id
    FROM selected_tracks
    UNION ALL
    SELECT -- use siblings (library_tracks.album_id) itunes store album ids as fallback for this track
        current.id,
        'album',
        CAST(json_extract(sibling.track_metadata, '$.itunes_store_album_id') AS TEXT),
        2,
        -COUNT(*),
        MIN(sibling.id)
    FROM selected_tracks AS current
    JOIN library_tracks AS sibling
        ON sibling.album_id = current.album_id
        AND sibling.id != current.id
    WHERE COALESCE(json_extract(current.track_metadata, '$.itunes_store_album_id'), 0)
        != json_extract(sibling.track_metadata, '$.itunes_store_album_id')
    GROUP BY
        current.id,
        json_extract(sibling.track_metadata, '$.itunes_store_album_id')
)
SELECT
    candidates.track_id,
    roots.path,
    tracks.path,
    candidates.lookup_kind,
    candidates.lookup_id
FROM candidates
JOIN library_tracks AS tracks
    ON tracks.id = candidates.track_id
JOIN library_roots AS roots
    ON roots.id = tracks.root_id
WHERE CAST(candidates.lookup_id AS INTEGER) > 0
    AND NOT EXISTS ( -- candidate = when we don't have both thumbnail sizes
        SELECT 1
        FROM library_track_artwork AS artwork
        JOIN thumbnail_cache AS thumbnail
            ON thumbnail.id = artwork.thumbnail_cache_id
        WHERE artwork.track_id = tracks.id
            AND thumbnail.width_px = 64
            AND EXISTS (
                SELECT 1
                FROM library_track_artwork AS large_artwork
                JOIN thumbnail_cache AS large_thumbnail
                    ON large_thumbnail.id = large_artwork.thumbnail_cache_id
                WHERE large_artwork.track_id = tracks.id
                    AND large_thumbnail.name = thumbnail.name
                    AND large_thumbnail.width_px = 256
            )
    )
ORDER BY
    candidates.track_id,
    candidates.priority,
    candidates.frequency,
    candidates.first_id,
    candidates.lookup_id COLLATE BINARY;
