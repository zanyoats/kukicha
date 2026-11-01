SELECT DISTINCT
    genres.id,
    genres.genre,
    (SELECT COALESCE(MAX(position) + 1, 0)
     FROM genres_radios
     WHERE genre_id = genres.id) AS track_count
FROM library_album_genres AS album_genres
JOIN taxonomy_genres AS genres
    ON genres.id = album_genres.genre_id
ORDER BY
    genres.genre COLLATE NOCASE,
    genres.id;
