SELECT
    tracks.id,
    roots.config_id,
    title,
    artists,
    duration_seconds,
    disc_number,
    track_number
FROM library_tracks AS tracks
JOIN library_roots AS roots ON roots.id = tracks.root_id
WHERE tracks.album_id = :album_id
ORDER BY
    roots.config_id COLLATE BINARY,
    COALESCE(disc_number, 1),
    CASE WHEN track_number IS NULL THEN 1 ELSE 0 END,
    track_number,
    path COLLATE BINARY,
    tracks.id;
