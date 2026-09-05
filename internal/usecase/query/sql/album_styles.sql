SELECT styles.id, styles.style
FROM library_album_styles AS album_styles
JOIN taxonomy_styles AS styles
    ON styles.id = album_styles.style_id
WHERE album_styles.album_id = :album_id
ORDER BY styles.style COLLATE NOCASE, styles.id;
