DELETE FROM taxonomy;
DELETE FROM taxonomy_styles;
DELETE FROM taxonomy_genres;

DELETE FROM sqlite_sequence
WHERE name IN (
    'taxonomy_genres',
    'taxonomy_styles'
);
