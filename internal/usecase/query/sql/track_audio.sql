SELECT
    roots.config_id,
    tracks.path,
    tracks.ext
FROM library_tracks AS tracks
JOIN library_roots AS roots
    ON roots.id = tracks.root_id
WHERE tracks.id = :track_id;
