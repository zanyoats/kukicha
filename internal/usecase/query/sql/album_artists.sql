SELECT artists.id, artists.artist
FROM library_album_artists AS album_artists
JOIN available_library_albums AS albums ON albums.id = album_artists.album_id
JOIN library_artist_ids AS artists
    ON artists.id = album_artists.artist_id
WHERE album_artists.album_id = :album_id
ORDER BY artists.artist COLLATE NOCASE, artists.id;
