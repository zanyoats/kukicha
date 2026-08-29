PRAGMA temp_store = FILE;
PRAGMA temp.cache_size = -32768;

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
    track_metadata       TEXT    NOT NULL                 , -- JSON object
    normalized_metadata  TEXT    NOT NULL                 , -- JSON object
    scan_error           TEXT    NOT NULL                 ,

    PRIMARY KEY (root_pos, path)                          ,
    CHECK (
            json_valid(track_metadata)
        AND json_valid(normalized_metadata)
        AND length(album_key) > 0
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
    CHECK (length(picture_type) > 0)
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

    PRIMARY KEY (root_pos, path)
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

    PRIMARY KEY (root_pos, path)
) WITHOUT ROWID;
