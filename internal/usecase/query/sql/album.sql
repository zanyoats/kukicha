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
FROM library_albums
WHERE id = :album_id;
