SELECT
    id,
    title,
    artists,
    duration_seconds,
    disc_number,
    track_number
FROM library_tracks
WHERE album_id = :album_id
ORDER BY
    COALESCE(disc_number, 1),
    CASE WHEN track_number IS NULL THEN 1 ELSE 0 END,
    track_number,
    path COLLATE BINARY,
    id;
