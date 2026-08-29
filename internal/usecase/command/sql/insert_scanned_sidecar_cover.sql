INSERT INTO scan_sidecar_covers (
    root_pos,
    path_dir,
    stem,
    ext,
    path,
    file_size,
    file_mtime_unix_nano,
    thumbnail_cache_name
)
VALUES (
    :root_pos,
    :path_dir,
    :stem,
    :ext,
    :path,
    :file_size,
    :file_mtime_unix_nano,
    :thumbnail_cache_name
);
