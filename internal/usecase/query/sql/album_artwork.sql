SELECT
    thumbnail.artwork_content_type,
    thumbnail.artwork_bytes
FROM library_tracks AS tracks
JOIN library_track_artwork AS artwork
    ON artwork.track_id = tracks.id
JOIN thumbnail_cache AS thumbnail
    ON thumbnail.id = artwork.thumbnail_cache_id
WHERE tracks.album_id = :album_id
    AND thumbnail.width_px = :width
    AND thumbnail.artwork_content_size > 0
ORDER BY
    COALESCE(tracks.disc_number, 1),
    CASE
        WHEN tracks.track_number IS NULL THEN 1
        ELSE 0
    END,
    tracks.track_number,
    tracks.path COLLATE BINARY,
    tracks.id,
    artwork.id,
    thumbnail.id
LIMIT 1;
