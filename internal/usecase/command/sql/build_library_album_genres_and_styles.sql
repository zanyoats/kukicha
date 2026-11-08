INSERT INTO library_album_genres (album_id, genre_id)
WITH album_genres AS (
    SELECT DISTINCT
        tracks.album_id,
        track_genres.genre_id
    FROM library_track_genres AS track_genres
    JOIN library_tracks AS tracks
        ON tracks.id = track_genres.track_id
)
SELECT album_genres.album_id, album_genres.genre_id
FROM album_genres
JOIN taxonomy_genres AS genres ON genres.id = album_genres.genre_id
-- Unknown is an album fallback only when no recognized genre is present.
WHERE genres.genre <> :unknown_item
    OR NOT EXISTS (
        SELECT 1
        FROM album_genres AS recognized
        JOIN taxonomy_genres AS recognized_genres ON recognized_genres.id = recognized.genre_id
        WHERE recognized.album_id = album_genres.album_id
            AND recognized_genres.genre <> :unknown_item
    )
ORDER BY album_genres.album_id, album_genres.genre_id;

INSERT INTO library_album_styles (album_id, style_id)
SELECT
    tracks.album_id,
    track_styles.style_id
FROM library_track_styles AS track_styles
JOIN library_tracks AS tracks
    ON tracks.id = track_styles.track_id
GROUP BY tracks.album_id, track_styles.style_id
ORDER BY tracks.album_id, track_styles.style_id;
