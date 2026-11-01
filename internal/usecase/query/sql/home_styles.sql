SELECT DISTINCT
    styles.id,
    styles.style,
    (SELECT COALESCE(MAX(position) + 1, 0)
     FROM styles_radios
     WHERE style_id = styles.id) AS track_count
FROM library_album_styles AS album_styles
JOIN taxonomy_styles AS styles
    ON styles.id = album_styles.style_id
ORDER BY
    styles.style COLLATE NOCASE,
    styles.id;
