SELECT
    albums.id,
    albums.album,
    album_ids.album_key,
    COALESCE(overrides.source_url, '')
FROM available_library_albums AS albums
JOIN library_album_ids AS album_ids ON album_ids.id = albums.id
LEFT JOIN album_metadata_overrides AS overrides ON overrides.album_key = album_ids.album_key
WHERE EXISTS (
    SELECT 1 FROM library_album_artists AS matching_artists
    WHERE matching_artists.album_id = albums.id AND matching_artists.artist_id = :artist_id
)
ORDER BY albums.album, albums.id;
