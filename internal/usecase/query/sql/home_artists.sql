SELECT DISTINCT
    artists.id,
    artists.artist,
    (SELECT COALESCE(MAX(position) + 1, 0)
     FROM artists_radios
     WHERE artist_id = artists.id) AS track_count
FROM library_album_artists AS album_artists
JOIN available_library_albums AS albums ON albums.id = album_artists.album_id
JOIN library_artist_ids AS artists
    ON artists.id = album_artists.artist_id
ORDER BY
    artists.artist COLLATE NOCASE,
    artists.id;
