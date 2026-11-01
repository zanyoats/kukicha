INSERT INTO album_artist_mappings (album_key, mapped_artists)
SELECT album_key, json(:mapped_artists)
FROM library_album_ids
WHERE album_key = :album_key
ON CONFLICT(album_key) DO UPDATE SET mapped_artists = excluded.mapped_artists;
