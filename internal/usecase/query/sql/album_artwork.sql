SELECT
    artwork.artwork_content_type,
    artwork.artwork_bytes
FROM library_tracks AS tracks
JOIN library_track_artwork AS artwork
    ON artwork.track_id = tracks.id
WHERE tracks.album_id = :album_id
    AND artwork.width_px = :width
    AND artwork.artwork_content_size > 0
ORDER BY
    COALESCE(tracks.disc_number, 1),
    CASE
        WHEN tracks.track_number IS NULL THEN 1
        ELSE 0
    END,
    tracks.track_number,
    tracks.path COLLATE BINARY,
    tracks.id,
    artwork.id
LIMIT 1;
