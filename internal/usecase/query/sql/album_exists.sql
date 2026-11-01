SELECT EXISTS (
    SELECT 1
    FROM available_library_albums
    WHERE id = :album_id
);
