INSERT OR IGNORE INTO taxonomy_genres (genre)
SELECT name
FROM taxonomy
WHERE kind = 'genre'
ORDER BY id;

-- Insert our special unknown token so foreign-key constraints don't fire.
INSERT OR IGNORE INTO taxonomy_genres (genre)
VALUES (:unknown_item);

INSERT OR IGNORE INTO taxonomy_styles (style, parent_genre_id)
SELECT styles.name, genres.id
FROM taxonomy AS styles
JOIN taxonomy_genres AS genres
    ON genres.genre = styles.parent
WHERE styles.kind = 'style'
ORDER BY styles.id;
