UPDATE album_artist_split_mappings
SET mapped_artists = json(:mapped_artists)
WHERE album_key = :album_key;
