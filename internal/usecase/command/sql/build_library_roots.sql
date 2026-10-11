INSERT INTO library_roots (id, config_id)
SELECT ids.id, scanned.config_id
FROM scan_roots AS scanned
JOIN library_root_ids AS ids
    ON ids.config_id = scanned.config_id
ORDER BY scanned.config_id COLLATE BINARY;
