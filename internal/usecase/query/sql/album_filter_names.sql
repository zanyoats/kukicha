SELECT
    (SELECT artist FROM library_artist_ids WHERE id = :artist_id),
    (SELECT genre FROM taxonomy_genres WHERE id = :genre_id),
    (SELECT style FROM taxonomy_styles WHERE id = :style_id);
