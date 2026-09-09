DROP TABLE IF EXISTS scan_tracks;

CREATE TEMP TABLE scan_tracks (
    root_pos             INTEGER NOT NULL                 ,
    root_path            TEXT    NOT NULL                 ,
    album_key            TEXT    NOT NULL                 ,
    path_dir             TEXT    NOT NULL                 ,
    stem                 TEXT    NOT NULL                 ,
    ext                  TEXT    NULL                     ,
    path                 TEXT    NOT NULL                 ,
    file_size            INTEGER NOT NULL                 ,
    file_mtime_unix_nano INTEGER NOT NULL                 ,
    duration_seconds     INTEGER NOT NULL                 ,
    track_metadata       TEXT    NOT NULL                 ,
    normalized_metadata  TEXT    NOT NULL                 ,
    scan_error           TEXT    NOT NULL                 ,

    PRIMARY KEY (root_pos, path)                          ,
    CHECK (
            duration_seconds >= 0
        AND json_valid(track_metadata)
        AND json_type(track_metadata) = 'object'
        AND json_valid(normalized_metadata)
        AND json_type(normalized_metadata) = 'object'
        AND length(trim(root_path)) > 0
        AND length(trim(path)) > 0
        AND length(trim(album_key)) > 0
    )
) WITHOUT ROWID;

DROP TABLE IF EXISTS scan_artwork;

CREATE TEMP TABLE scan_artwork (
    root_pos             INTEGER NOT NULL                 ,
    path                 TEXT    NOT NULL                 ,
    artwork_pos          INTEGER NOT NULL                 ,
    picture_type         TEXT    NOT NULL                 ,
    artwork_content_type TEXT                             ,
    artwork_content_size INTEGER NOT NULL                 ,
    artwork_bytes        BLOB    NOT NULL                 ,

    PRIMARY KEY (root_pos, path, artwork_pos)             ,
    CHECK (
            length(picture_type) > 0
        AND artwork_content_size > 0
        AND length(artwork_bytes) = artwork_content_size
        AND length(trim(path)) > 0
    )
) WITHOUT ROWID;

DROP TABLE IF EXISTS scan_artwork_cover;

CREATE TEMP TABLE scan_artwork_cover (
    root_pos             INTEGER NOT NULL                 ,
    path                 TEXT    NOT NULL                 ,
    artwork_pos          INTEGER NOT NULL                 ,
    picture_type         TEXT    NOT NULL                 ,
    thumbnail_cache_name TEXT                             ,

    PRIMARY KEY (root_pos, path, artwork_pos)             ,
    CHECK (
            length(picture_type) > 0
        AND length(trim(path)) > 0
    )
) WITHOUT ROWID;

DROP TABLE IF EXISTS scan_sidecar_covers;

CREATE TEMP TABLE scan_sidecar_covers (
    root_pos             INTEGER NOT NULL                 ,
    path_dir             TEXT    NOT NULL                 ,
    stem                 TEXT    NOT NULL                 ,
    ext                  TEXT    NOT NULL                 ,
    path                 TEXT    NOT NULL                 ,
    file_size            INTEGER NOT NULL                 ,
    file_mtime_unix_nano INTEGER NOT NULL                 ,
    thumbnail_cache_name TEXT                             ,

    PRIMARY KEY (root_pos, path)                          ,
    CHECK (length(trim(path)) > 0)
) WITHOUT ROWID;

DROP TABLE IF EXISTS scan_sidecar_others;

CREATE TEMP TABLE scan_sidecar_others (
    root_pos             INTEGER NOT NULL                 ,
    path_dir             TEXT    NOT NULL                 ,
    stem                 TEXT    NOT NULL                 ,
    ext                  TEXT    NOT NULL                 ,
    path                 TEXT    NOT NULL                 ,
    file_size            INTEGER NOT NULL                 ,
    file_mtime_unix_nano INTEGER NOT NULL                 ,

    PRIMARY KEY (root_pos, path)                          ,
    CHECK (length(trim(path)) > 0)
) WITHOUT ROWID;
