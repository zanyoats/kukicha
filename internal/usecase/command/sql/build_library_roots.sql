INSERT INTO library_roots (id, path)
SELECT
    ids.id,
    scanned.root_path
FROM (
    SELECT DISTINCT root_path
    FROM scan_tracks
) AS scanned
JOIN library_root_ids AS ids
    ON ids.root_path = scanned.root_path
ORDER BY scanned.root_path COLLATE BINARY;
