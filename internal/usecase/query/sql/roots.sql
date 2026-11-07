WITH track_counts AS (
    SELECT root_id, COUNT(*) AS tracks, COUNT(DISTINCT album_id) AS albums
    FROM library_tracks
    GROUP BY root_id
), sidecar_counts AS (
    SELECT root_id, COUNT(DISTINCT path) AS album_sidecars
    FROM library_album_sidecars
    GROUP BY root_id
)
SELECT
    roots.config_id,
    roots.state,
    COALESCE(track_counts.tracks, 0),
    COALESCE(track_counts.albums, 0),
    COALESCE(sidecar_counts.album_sidecars, 0)
FROM library_roots AS roots
LEFT JOIN track_counts ON track_counts.root_id = roots.id
LEFT JOIN sidecar_counts ON sidecar_counts.root_id = roots.id
ORDER BY roots.config_id COLLATE BINARY;
