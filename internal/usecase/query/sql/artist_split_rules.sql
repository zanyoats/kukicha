SELECT
    mappings.album_key,
    mappings.mapped_artists,
    albums.id,
    albums.album
FROM album_artist_split_mappings AS mappings
LEFT JOIN library_album_ids AS album_ids
    ON album_ids.album_key = mappings.album_key
LEFT JOIN library_albums AS albums
    ON albums.id = album_ids.id
ORDER BY albums.album, albums.id;
