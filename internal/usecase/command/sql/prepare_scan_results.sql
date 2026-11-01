DROP TABLE IF EXISTS scan_roots;

CREATE TEMP TABLE scan_roots (
    root_pos INTEGER PRIMARY KEY,
    config_id TEXT NOT NULL,
    UNIQUE (config_id)
);

DROP TABLE IF EXISTS scan_tracks;

CREATE TEMP TABLE scan_tracks (
    root_pos             INTEGER NOT NULL                 ,
    config_id            TEXT    NOT NULL                 ,
    album_key            TEXT    NOT NULL                 ,
    path_dir             TEXT    NOT NULL                 ,
    stem                 TEXT    NOT NULL                 ,
    ext                  TEXT    NULL                     ,
    path                 TEXT    NOT NULL                 ,
    file_size            INTEGER NOT NULL                 ,
    file_mtime_unix_nano INTEGER NOT NULL                 ,
    duration_seconds     INTEGER NOT NULL                 ,
    track_metadata       TEXT    NOT NULL                 ,

    PRIMARY KEY (root_pos, path)                          ,
    CHECK (
            duration_seconds >= 0
        AND json_valid(track_metadata)
        AND json_type(track_metadata) = 'object'
        AND length(trim(config_id)) > 0
        AND length(trim(path)) > 0
        AND length(trim(album_key)) > 0
    )
) WITHOUT ROWID;

DROP TABLE IF EXISTS scan_track_normalized_metadata;

CREATE TEMP TABLE scan_track_normalized_metadata (
    root_pos INTEGER NOT NULL,
    path TEXT NOT NULL,
    normalized_metadata TEXT NOT NULL,
    PRIMARY KEY (root_pos, path),
    CHECK (
        json_valid(normalized_metadata)
        AND json_type(normalized_metadata) = 'object'
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
    thumbnail_cache_name TEXT    NOT NULL                 ,

    PRIMARY KEY (root_pos, path, artwork_pos)             ,
    CHECK (
            picture_type IN ('front_cover', 'other')
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

DROP TABLE IF EXISTS scan_playlists;

CREATE TEMP TABLE scan_playlists (
    root_pos INTEGER NOT NULL,
    path TEXT NOT NULL,
    PRIMARY KEY (root_pos, path)
) WITHOUT ROWID;

DROP TABLE IF EXISTS scan_saved_track_artwork;

CREATE TEMP TABLE scan_saved_track_artwork (
    track_id INTEGER NOT NULL,
    thumbnail_cache_id INTEGER NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (track_id, thumbnail_cache_id)
);

DROP TABLE IF EXISTS scan_album_overrides;

CREATE TEMP TABLE scan_album_overrides (
 album_key TEXT PRIMARY KEY,
 provider TEXT NOT NULL,
 source_url TEXT NOT NULL,
 genres_json TEXT NOT NULL,
 thumbnail_cache_name TEXT
);

DROP TABLE IF EXISTS scan_album_override_sources;

CREATE TEMP TABLE scan_album_override_sources (
    album_key TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    source_url TEXT NOT NULL
);
CREATE INDEX scan_album_override_sources_entity_idx
ON scan_album_override_sources (provider, entity_type, entity_id);

-- Incoming scan results are published only after all file transfers succeed.
DROP TABLE IF EXISTS scan_unrecognized_tracks;
CREATE TEMP TABLE scan_unrecognized_tracks AS SELECT * FROM scan_tracks WHERE 0;
CREATE UNIQUE INDEX scan_unrecognized_tracks_path ON scan_unrecognized_tracks (root_pos, path);

-- Entering scan_tracks atomically removes the matching pending scan row.
-- Recognition is checked by the inserting query, not repeated here. Inserts
-- from the saved library or corrected tracks normally have no pending match.
CREATE TEMP TRIGGER remove_promoted_scan_track
AFTER INSERT ON scan_tracks
BEGIN
    DELETE FROM scan_unrecognized_tracks
    WHERE root_pos = NEW.root_pos AND path = NEW.path;
END;

DROP TABLE IF EXISTS scan_ready_unrecognized_tracks;
CREATE TEMP TABLE scan_ready_unrecognized_tracks AS
SELECT * FROM unrecognized_tracks WHERE 0;
