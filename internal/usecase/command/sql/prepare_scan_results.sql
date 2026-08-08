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
    file_type            INTEGER NOT NULL                 ,
    file_size            INTEGER NOT NULL                 ,
    file_mtime_unix_nano INTEGER NOT NULL                 ,
    track_metadata       TEXT    NOT NULL                 , -- JSON object
    normalized_metadata  TEXT    NOT NULL                 , -- JSON object
    scan_error           TEXT    NOT NULL                 ,

    PRIMARY KEY (root_pos, path)                          ,
    CHECK (
            json_valid(track_metadata)
        AND json_valid(normalized_metadata)
        AND (
               file_type != %[1]d
            OR length(album_key) > 0
        )
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

    PRIMARY KEY (root_pos, path, artwork_pos)              ,
    CHECK (
            length(picture_type) > 0
        AND artwork_content_size > 0
    )
) WITHOUT ROWID;
