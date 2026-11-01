SELECT
    (SELECT artist FROM library_artist_ids WHERE id = :artist_id AND EXISTS (
        SELECT 1 FROM library_album_artists AS relation
        JOIN available_library_albums AS albums ON albums.id = relation.album_id
        WHERE relation.artist_id = :artist_id
    )),
    (SELECT genre FROM taxonomy_genres WHERE id = :genre_id AND EXISTS (
        SELECT 1 FROM available_library_album_genres WHERE genre_id = :genre_id
    )),
    (SELECT style FROM taxonomy_styles WHERE id = :style_id AND EXISTS (
        SELECT 1 FROM available_library_album_styles WHERE style_id = :style_id
    ));
