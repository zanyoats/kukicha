SELECT
    tracks.id,
    roots.path
FROM library_tracks AS tracks
JOIN library_roots AS roots
    ON roots.id = tracks.root_id
ORDER BY tracks.id;
