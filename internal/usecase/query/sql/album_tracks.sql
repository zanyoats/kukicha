SELECT
    tracks.id,
    tracks.path,
    title,
    artists,
    duration_seconds,
    disc_number,
    track_number
FROM available_library_tracks AS tracks
WHERE tracks.album_id = :album_id
ORDER BY
    COALESCE(disc_number, 1),
    CASE WHEN track_number IS NULL THEN 1 ELSE 0 END,
    track_number,
    tracks.path COLLATE BINARY,
    tracks.id;
