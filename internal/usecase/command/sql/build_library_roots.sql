INSERT OR IGNORE INTO library_roots (pos, path)
SELECT DISTINCT
    root_pos,
    root_path
FROM scan_tracks
WHERE file_type = :audio_file_type;
