SELECT DISTINCT
    genres.id,
    genres.genre
FROM library_album_genres AS album_genres
JOIN taxonomy_genres AS genres
    ON genres.id = album_genres.genre_id
ORDER BY
    genres.genre COLLATE NOCASE,
    genres.id;
