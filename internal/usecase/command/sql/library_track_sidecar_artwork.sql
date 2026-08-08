SELECT path
FROM library_track_sidecars
WHERE track_id = :track_id
    AND file_type = :cover_file_type
ORDER BY path;
