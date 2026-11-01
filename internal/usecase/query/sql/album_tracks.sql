SELECT
    tracks.id,
    track_ids.path,
    title,
    artists,
    duration_seconds,
    disc_number,
    track_number
FROM available_library_tracks AS tracks
JOIN library_track_ids AS track_ids ON track_ids.id = tracks.id
WHERE tracks.album_id = :album_id
ORDER BY
    COALESCE(disc_number, 1),
    CASE WHEN track_number IS NULL THEN 1 ELSE 0 END,
    track_number,
    track_ids.path COLLATE BINARY,
    tracks.id;
