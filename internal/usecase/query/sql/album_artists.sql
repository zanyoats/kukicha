SELECT artist
FROM library_album_artists
WHERE album_id = :album_id
ORDER BY artist COLLATE NOCASE, artist_key, id;
