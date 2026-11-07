WITH sidecars AS (
    SELECT root_pos, path FROM scan_sidecar_covers
    UNION ALL
    SELECT root_pos, path FROM scan_sidecar_others
), scanned_roots AS (
    SELECT root_pos, config_id FROM scan_roots
)
SELECT
    (SELECT COUNT(*) FROM scan_tracks)
        + (SELECT COUNT(*) FROM scan_unrecognized_tracks)
        + (SELECT COUNT(*) FROM sidecars) AS processed_files_count,
    (SELECT COUNT(*) FROM sidecars) AS sidecar_files_count,
    (
        SELECT COUNT(*) FROM sidecars AS sidecar
        WHERE NOT EXISTS (
            SELECT 1
            FROM library_album_sidecars AS associated
            JOIN library_roots AS roots ON roots.id = associated.root_id
            JOIN scanned_roots ON scanned_roots.config_id = roots.config_id
            WHERE scanned_roots.root_pos = sidecar.root_pos
                AND associated.path = sidecar.path
        )
    ) AS unassociated_sidecars_count,
    (SELECT COUNT(*) FROM library_roots) AS library_roots_count,
    (SELECT COUNT(*) FROM library_albums) AS library_albums_count,
    (SELECT COUNT(*) FROM library_tracks) AS library_tracks_count,
    (SELECT COUNT(*) FROM library_album_sidecars) AS library_album_sidecars_count,
    (SELECT COUNT(*) FROM library_tracks WHERE title = :unknown_item) AS library_track_title_unknowns_count,
    (SELECT COUNT(*) FROM library_albums WHERE album = :unknown_item) AS library_album_unknowns_count,
    (
        SELECT COUNT(*)
        FROM library_albums AS albums
        WHERE EXISTS (
            SELECT 1
            FROM library_album_artists AS album_artists
            JOIN library_artist_ids AS artists ON artists.id = album_artists.artist_id
            WHERE album_artists.album_id = albums.id
                AND artists.artist = :unknown_item
        )
    ) AS library_album_artist_unknowns_count;
