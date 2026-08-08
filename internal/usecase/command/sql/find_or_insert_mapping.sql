INSERT INTO album_artist_split_mappings (album_key, mapped_artists)
VALUES (:album_key, json(:mapped_artists))
ON CONFLICT(album_key) DO UPDATE SET
    mapped_artists = mapped_artists
RETURNING mapped_artists;
