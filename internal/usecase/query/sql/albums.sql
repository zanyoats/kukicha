SELECT
    albums.id,
    albums.album,
    json_group_array(
        artists.artist
            ORDER BY
                artists.artist COLLATE NOCASE,
                artists.artist_key,
                artists.id
    ) FILTER (
        WHERE artists.id IS NOT NULL
    )
FROM library_albums AS albums
LEFT JOIN library_album_artists AS artists
    ON artists.album_id = albums.id
GROUP BY
    albums.id,
    albums.album,
    albums.album_key
ORDER BY
    albums.album COLLATE NOCASE,
    albums.album_key,
    albums.id;
