SELECT
    id,
    album,
    format,
    release_date,
    release_year,
    original_release_date,
    original_release_year,
    release_country,
    label,
    composer,
    copyright,
    purchase_date,
    is_compilation,
    disc_count
FROM library_albums AS albums
WHERE albums.id = :album_id
AND (:artist_id = 0 OR EXISTS (
    SELECT 1
    FROM library_album_artists AS matching_artists
    WHERE matching_artists.album_id = albums.id
        AND matching_artists.artist_id = :artist_id
))
AND (:genre_id = 0 OR EXISTS (
    SELECT 1
    FROM library_album_genres AS matching_genres
    WHERE matching_genres.album_id = albums.id
        AND matching_genres.genre_id = :genre_id
))
AND (:style_id = 0 OR EXISTS (
    SELECT 1
    FROM library_album_styles AS matching_styles
    WHERE matching_styles.album_id = albums.id
        AND matching_styles.style_id = :style_id
));
