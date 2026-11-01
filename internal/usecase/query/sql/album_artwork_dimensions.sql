-- Keep selection aligned with albums.sql and the other album artwork query.
SELECT thumbnail.width_px, thumbnail.height_px
FROM library_album_artwork_candidates AS artwork
JOIN thumbnail_cache AS thumbnail ON thumbnail.id = artwork.thumbnail_cache_id
WHERE artwork.album_id = :album_id AND thumbnail.width_px = :width
 AND thumbnail.artwork_content_size > 0
ORDER BY artwork.priority, artwork.disc_number, artwork.missing_track_number,
 artwork.track_number, artwork.path COLLATE BINARY, artwork.track_id, artwork.artwork_id, thumbnail.id
LIMIT 1;
