SELECT
    (SELECT COUNT(*) FROM library_roots) AS library_roots_count,
    (SELECT COUNT(*) FROM library_albums) AS library_albums_count,
    (SELECT COUNT(*) FROM library_tracks) AS library_tracks_count;
