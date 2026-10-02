INSERT OR IGNORE INTO library_track_artwork (track_id, thumbnail_cache_id, source)
SELECT tracks.id, thumbnail.id, 'itunes'
FROM library_tracks AS tracks
JOIN thumbnail_cache AS thumbnail ON thumbnail.name = :name AND thumbnail.width_px IN (64, 256)
WHERE tracks.id = :track_id
    AND EXISTS (SELECT 1 FROM thumbnail_cache WHERE name = :name AND width_px = 64)
    AND EXISTS (SELECT 1 FROM thumbnail_cache WHERE name = :name AND width_px = 256)
    AND NOT EXISTS (
        -- Prevent mixing images: uniqueness on (track_id, thumbnail_cache_id)
        -- allows both thumbnail sizes, but also allows thumbnails from different images.
        SELECT 1 FROM library_track_artwork AS artwork
        JOIN thumbnail_cache AS existing ON existing.id = artwork.thumbnail_cache_id
        WHERE artwork.track_id = tracks.id AND existing.name != :name
    );
