SELECT
    COALESCE((SELECT artist FROM library_artist_ids WHERE id = :artist_id), ''),
    COALESCE((SELECT genre FROM taxonomy_genres WHERE id = :genre_id), ''),
    COALESCE((SELECT style FROM taxonomy_styles WHERE id = :style_id), '');
