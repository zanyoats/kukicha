INSERT INTO scan_tracks (
    root_pos,
    root_path,
    album_key,
    path_dir,
    stem,
    ext,
    path,
    file_size,
    file_mtime_unix_nano,
    track_metadata,
    normalized_metadata,
    scan_error
)
VALUES (
    :root_pos,
    :root_path,
    :album_key,
    :path_dir,
    :stem,
    :ext,
    :path,
    :file_size,
    :file_mtime_unix_nano,
    json(:track_metadata),
    json(:normalized_metadata),
    :scan_error
);
