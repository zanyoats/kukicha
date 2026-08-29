INSERT INTO scan_sidecar_others (
    root_pos,
    path_dir,
    stem,
    ext,
    path,
    file_size,
    file_mtime_unix_nano
)
VALUES (
    :root_pos,
    :path_dir,
    :stem,
    :ext,
    :path,
    :file_size,
    :file_mtime_unix_nano
);
