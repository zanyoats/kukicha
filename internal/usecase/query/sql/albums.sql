SELECT
    albums.id,
    albums.album,
    json_group_array(
        json_object('id', artists.id, 'name', artists.artist)
            ORDER BY
                artists.artist COLLATE NOCASE,
                artists.id
    ) FILTER (
        WHERE artists.id IS NOT NULL
    )
FROM library_albums AS albums
LEFT JOIN library_album_artists AS album_artists
    ON album_artists.album_id = albums.id
LEFT JOIN library_artist_ids AS artists
    ON artists.id = album_artists.artist_id
GROUP BY
    albums.id,
    albums.album
ORDER BY
    albums.album COLLATE NOCASE,
    albums.id;
