SELECT EXISTS (
    SELECT 1
    FROM library_albums
    WHERE id = :album_id
);
