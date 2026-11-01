CREATE TABLE app_metadata (
    key   INTEGER PRIMARY KEY,
    value TEXT    NOT NULL
);

CREATE TABLE taxonomy_genres (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    genre TEXT    NOT NULL COLLATE NOCASE  ,

    UNIQUE (genre),
    CHECK (length(trim(genre)) > 0)
);

CREATE TABLE taxonomy_styles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    style           TEXT    NOT NULL COLLATE NOCASE  ,
    parent_genre_id INTEGER NOT NULL                 ,

    UNIQUE (style)                                   ,
    CHECK (length(trim(style)) > 0)                  ,
    FOREIGN KEY (parent_genre_id)
      REFERENCES taxonomy_genres (id)
      ON DELETE CASCADE
);

CREATE TABLE taxonomy (
    id     INTEGER PRIMARY KEY,
    kind   TEXT    NOT NULL   ,
    name   TEXT    NOT NULL   , -- canonical
    parent TEXT    NOT NULL   ,
    source TEXT    NOT NULL   ,
    alias  TEXT    NOT NULL   ,
    status TEXT    NOT NULL   ,
    count  INTEGER            ,

    CHECK(
            kind in ('genre', 'style')
        AND (length(trim(name)) > 0)
        AND (kind != 'style' OR length(trim(parent)) > 0)
        AND source in ('discogs_20260301_masters', 'mb_genres_v0')
        AND (length(trim(alias)) > 0)
        AND (length(trim(status)) > 0)
    )
);

CREATE TABLE album_artist_mappings (
    album_key      TEXT PRIMARY KEY    ,
    mapped_artists TEXT NOT NULL       ,

    CHECK (length(trim(album_key)) > 0),
    CHECK (
            json_valid(mapped_artists)
        AND json_type(mapped_artists) = 'array'
        AND json_array_length(mapped_artists) > 0
    )
) WITHOUT ROWID;

CREATE TABLE library_root_ids (
    id        INTEGER PRIMARY KEY AUTOINCREMENT      ,
    config_id TEXT    NOT NULL                       ,
    timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    UNIQUE (config_id)                               ,
    CHECK (length(trim(config_id)) > 0)
);

CREATE TABLE library_album_ids (
    id        INTEGER PRIMARY KEY AUTOINCREMENT      ,
    album_key TEXT    NOT NULL                       ,
    timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    UNIQUE (album_key)                               ,
    CHECK (length(trim(album_key)) > 0)
);

CREATE TABLE library_artist_ids (
    id        INTEGER PRIMARY KEY AUTOINCREMENT      ,
    artist    TEXT NOT NULL COLLATE NOCASE           ,
    timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    UNIQUE (artist)                                  ,
    CHECK (length(trim(artist)) > 0)
);

CREATE TABLE library_track_ids (
    id        INTEGER PRIMARY KEY AUTOINCREMENT      ,
    config_id TEXT NOT NULL                          ,
    path      TEXT  NOT NULL                         ,
    timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    UNIQUE (config_id, path)                         ,
    CHECK (
            length(trim(config_id)) > 0
        AND length(trim(path)) > 0
    )
);

CREATE TABLE library_roots (
    id        INTEGER PRIMARY KEY,
    config_id TEXT    NOT NULL   ,

    state TEXT NOT NULL DEFAULT 'available' CHECK (state IN ('available', 'lost', 'disowned', 'pending_removal')),

    UNIQUE (config_id)           ,
    CHECK (length(trim(config_id)) > 0)
);

CREATE TABLE library_albums (
    id                    INTEGER PRIMARY KEY,
    album                 TEXT    NOT NULL   ,
    added_at              TEXT    NOT NULL   ,
    first_artist          TEXT    NOT NULL COLLATE NOCASE,
    format                TEXT    NOT NULL   ,
    disc_count            INTEGER            ,
    release_date          TEXT               ,
    original_release_date TEXT               ,
    recorded_date         TEXT               ,
    release_year          INTEGER            ,
    original_release_year INTEGER            ,
    release_country       TEXT               ,
    label                 TEXT               ,
    composer              TEXT               ,
    copyright             TEXT               ,
    purchase_date         TEXT               ,
    is_compilation        INTEGER            ,

    CHECK (length(trim(album)) > 0)          ,
    CHECK (length(trim(format)) > 0)
);

CREATE INDEX library_albums_added_at_idx
ON library_albums (added_at DESC, id DESC);

CREATE INDEX library_albums_album_idx
ON library_albums (album COLLATE NOCASE, id);

CREATE INDEX library_albums_artist_album_idx
ON library_albums (first_artist COLLATE NOCASE, album COLLATE NOCASE, id);

CREATE TABLE library_album_artists (
    album_id  INTEGER NOT NULL              ,
    artist_id INTEGER NOT NULL              ,

    PRIMARY KEY (album_id, artist_id)       ,
    FOREIGN KEY (album_id)
      REFERENCES library_albums (id)
      ON DELETE CASCADE                     ,
    FOREIGN KEY (artist_id)
      REFERENCES library_artist_ids (id)
      ON DELETE RESTRICT
) WITHOUT ROWID;

CREATE INDEX library_album_artists_artist_id_idx
ON library_album_artists (artist_id, album_id);

CREATE TABLE library_album_sidecars (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    album_id             INTEGER NOT NULL                 ,
    root_id              INTEGER NOT NULL                 ,
    kind                 TEXT NOT NULL CHECK (kind IN ('cover', 'other')),
    stem                 TEXT    NOT NULL                 ,
    ext                  TEXT    NOT NULL                 ,
    path_dir             TEXT    NOT NULL                 ,
    path                 TEXT    NOT NULL                 ,
    file_size            INTEGER NOT NULL                 ,
    file_mtime_unix_nano INTEGER NOT NULL                 ,
    thumbnail_cache_name TEXT                             ,

    UNIQUE (album_id, root_id, path)                      ,
    CHECK (thumbnail_cache_name IS NULL OR length(thumbnail_cache_name) = 64),
    FOREIGN KEY (album_id)
      REFERENCES library_albums (id)
      ON DELETE CASCADE                                   ,
    FOREIGN KEY (root_id)
      REFERENCES library_roots (id)
      ON DELETE CASCADE
);

CREATE TABLE library_track_embedded_artwork (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id             INTEGER NOT NULL                 ,
    picture_type         TEXT    NOT NULL                 ,
    artwork_content_type TEXT                             ,
    artwork_content_size INTEGER NOT NULL                 ,
    artwork_bytes        BLOB    NOT NULL                 ,

    FOREIGN KEY (track_id)
      REFERENCES library_tracks (id)
      ON DELETE CASCADE
);

CREATE TABLE thumbnail_cache (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    name                 TEXT    NOT NULL                 ,
    width_px             INTEGER NOT NULL                 ,
    height_px            INTEGER NOT NULL                 ,
    artwork_content_type TEXT    NOT NULL                 ,
    artwork_content_size INTEGER NOT NULL                 ,
    artwork_bytes        BLOB    NOT NULL                 ,

    UNIQUE (name, width_px)                               ,
    CHECK (length(name) = 64)                             ,
    CHECK (width_px > 0)                                  ,
    CHECK (height_px > 0)                                 ,
    CHECK (
            artwork_content_size > 0
        AND length(artwork_bytes) = artwork_content_size
    )
);

CREATE TABLE library_track_artwork (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id           INTEGER NOT NULL                 ,
    thumbnail_cache_id INTEGER NOT NULL                 ,
    source             TEXT    NOT NULL                 ,

    CHECK (source IN ('sidecar', 'embedded', 'itunes')) ,
    UNIQUE (track_id, thumbnail_cache_id)               ,
    FOREIGN KEY (track_id)
      REFERENCES library_tracks (id)
      ON DELETE CASCADE                                 ,
    FOREIGN KEY (thumbnail_cache_id)
      REFERENCES thumbnail_cache (id)
      ON DELETE RESTRICT
);

CREATE INDEX library_track_artwork_track_id_idx
ON library_track_artwork (track_id);

CREATE TABLE library_track_genres (
    track_id INTEGER NOT NULL       ,
    genre_id INTEGER NOT NULL       ,

    PRIMARY KEY (track_id, genre_id),
    FOREIGN KEY (track_id)
      REFERENCES library_tracks (id)
      ON DELETE CASCADE             ,
    FOREIGN KEY (genre_id)
      REFERENCES taxonomy_genres (id)
      ON DELETE CASCADE
) WITHOUT ROWID;

CREATE TABLE library_track_styles (
    track_id INTEGER NOT NULL        ,
    style_id INTEGER NOT NULL        ,

    PRIMARY KEY (track_id, style_id) ,
    FOREIGN KEY (track_id)
      REFERENCES library_tracks (id)
      ON DELETE CASCADE              ,
    FOREIGN KEY (style_id)
      REFERENCES taxonomy_styles (id)
      ON DELETE CASCADE
) WITHOUT ROWID;

CREATE TABLE library_album_genres (
    album_id INTEGER NOT NULL       ,
    genre_id INTEGER NOT NULL       ,

    PRIMARY KEY (album_id, genre_id),
    FOREIGN KEY (album_id)
      REFERENCES library_albums (id)
      ON DELETE CASCADE             ,
    FOREIGN KEY (genre_id)
      REFERENCES taxonomy_genres (id)
      ON DELETE CASCADE
) WITHOUT ROWID;

CREATE INDEX library_album_genres_genre_id_idx
ON library_album_genres (genre_id, album_id);

CREATE TABLE library_album_styles (
    album_id INTEGER NOT NULL       ,
    style_id INTEGER NOT NULL       ,

    PRIMARY KEY (album_id, style_id),
    FOREIGN KEY (album_id)
      REFERENCES library_albums (id)
      ON DELETE CASCADE             ,
    FOREIGN KEY (style_id)
      REFERENCES taxonomy_styles (id)
      ON DELETE CASCADE
) WITHOUT ROWID;

CREATE INDEX library_album_styles_style_id_idx
ON library_album_styles (style_id, album_id);

CREATE TABLE library_tracks (
    id                   INTEGER PRIMARY KEY,
    root_id              INTEGER NOT NULL   ,
    album_id             INTEGER NOT NULL   ,
    stem                 TEXT    NOT NULL   ,
    ext                  TEXT    NOT NULL   ,
    path_dir             TEXT    NOT NULL   ,
    path                 TEXT    NOT NULL   ,
    file_size            INTEGER NOT NULL   ,
    file_mtime_unix_nano INTEGER NOT NULL   ,
    duration_seconds     INTEGER NOT NULL   ,
    track_metadata       TEXT    NOT NULL   ,
    title                TEXT    NOT NULL   ,
    artists              TEXT    NOT NULL   ,
    work                 TEXT               ,
    grouping             TEXT               ,
    movement_name        TEXT               ,
    movement_number      INTEGER            ,
    movement_count       INTEGER            ,
    show_movement        INTEGER            ,
    track_number         INTEGER            ,
    disc_number          INTEGER            ,

    UNIQUE (root_id, path)                  ,
    CHECK (
            duration_seconds >= 0
        AND json_valid(track_metadata)
        AND json_type(track_metadata) = 'object'
        AND json_valid(artists)
        AND json_type(artists) = 'array'
        AND json_array_length(artists) > 0
        AND length(trim(title)) > 0
        AND length(trim(path)) > 0
    )                                       ,
    FOREIGN KEY (root_id)
      REFERENCES library_roots (id)
      ON DELETE CASCADE                     ,
    FOREIGN KEY (album_id)
      REFERENCES library_albums (id)
      ON DELETE CASCADE
);

CREATE INDEX library_tracks_album_id_idx
ON library_tracks (album_id);

CREATE TABLE itunes_lookup_image_cache (
    cache_key            TEXT    PRIMARY KEY ,
    lookup_kind          TEXT    NOT NULL    ,
    lookup_id            TEXT    NOT NULL    ,
    result_kind          TEXT    NOT NULL    ,
    fetched_at           TEXT    NOT NULL    ,
    lookup_url           TEXT    NOT NULL    ,
    artwork_url          TEXT    NOT NULL    ,
    artwork_content_type TEXT    NOT NULL    ,
    artwork_content_size INTEGER NOT NULL    ,
    artwork_bytes        BLOB    NOT NULL    ,
    thumbnail_cache_name TEXT                ,

    CHECK (lookup_kind IN ('album', 'track')),
    CHECK (result_kind IN ('hit', 'missing')),
    CHECK (
        (
            result_kind = 'hit'
            AND artwork_content_size > 0
            AND length(artwork_bytes) = artwork_content_size
        )
        OR
        (
            result_kind = 'missing'
            AND artwork_content_size = 0
            AND length(artwork_bytes) = 0
            AND thumbnail_cache_name IS NULL
        )
    )
) WITHOUT ROWID;

CREATE TABLE jobs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT      ,
    type                TEXT NOT NULL                          ,
    payload             TEXT NOT NULL                          ,
    state               TEXT NOT NULL DEFAULT 'queued'         ,
    attempts            INTEGER NOT NULL DEFAULT 0             ,
    created_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at          TEXT                                   ,
    finished_at         TEXT                                   ,
    last_error          TEXT                                   ,
    cancel_requested_at TEXT                                   ,

    CHECK (type = 'build')                   ,
    CHECK (json_valid(payload))                                ,
    CHECK (state IN (
        -- transient states
        'queued',
        'running',
        -- final states
        'failed',
        'succeeded',
        'canceled'
    ))                                                         ,
    CHECK (attempts >= 0)
);

CREATE INDEX jobs_queued_id ON jobs (id) WHERE state = 'queued';

CREATE INDEX jobs_pending_state_id
ON jobs (state, id)
WHERE state IN ('running', 'queued');

CREATE TABLE library_job_logs (
    id        INTEGER PRIMARY KEY AUTOINCREMENT            ,
    job_id    INTEGER NOT NULL                             ,
    level     TEXT NOT NULL                                ,
    message   TEXT NOT NULL                                ,
    timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP      ,
    data      TEXT NOT NULL DEFAULT '{}'                   ,

    CHECK (level IN ('error', 'warn', 'info', 'debug'))    ,
    CHECK (json_valid(data) AND json_type(data) = 'object'),
    FOREIGN KEY (job_id)
      REFERENCES jobs (id)
      ON DELETE CASCADE
);

CREATE INDEX library_job_logs_job_id_id
ON library_job_logs (job_id, id);

CREATE TABLE artists_radios (
    artist_id INTEGER NOT NULL,
    position  INTEGER NOT NULL,
    track_id  INTEGER NOT NULL,

    PRIMARY KEY (artist_id, position),
    UNIQUE (artist_id, track_id),
    CHECK (position >= 0),
    FOREIGN KEY (artist_id)
      REFERENCES library_artist_ids (id)
      ON DELETE CASCADE,
    FOREIGN KEY (track_id)
      REFERENCES library_tracks (id)
      ON DELETE CASCADE
) WITHOUT ROWID;

CREATE TABLE genres_radios (
    genre_id INTEGER NOT NULL,
    position INTEGER NOT NULL,
    track_id INTEGER NOT NULL,

    PRIMARY KEY (genre_id, position),
    UNIQUE (genre_id, track_id),
    CHECK (position >= 0),
    FOREIGN KEY (genre_id)
      REFERENCES taxonomy_genres (id)
      ON DELETE CASCADE,
    FOREIGN KEY (track_id)
      REFERENCES library_tracks (id)
      ON DELETE CASCADE
) WITHOUT ROWID;

CREATE TABLE styles_radios (
    style_id INTEGER NOT NULL,
    position INTEGER NOT NULL,
    track_id INTEGER NOT NULL,

    PRIMARY KEY (style_id, position),
    UNIQUE (style_id, track_id),
    CHECK (position >= 0),
    FOREIGN KEY (style_id)
      REFERENCES taxonomy_styles (id)
      ON DELETE CASCADE,
    FOREIGN KEY (track_id)
      REFERENCES library_tracks (id)
      ON DELETE CASCADE
) WITHOUT ROWID;

-- Library browsing and playback share the same availability boundary.
CREATE VIEW available_library_tracks AS
SELECT tracks.* FROM library_tracks AS tracks
JOIN library_roots AS roots ON roots.id = tracks.root_id
WHERE roots.state = 'available';

CREATE VIEW available_library_albums AS
SELECT albums.* FROM library_albums AS albums
WHERE EXISTS (SELECT 1 FROM available_library_tracks AS tracks WHERE tracks.album_id = albums.id);

CREATE VIEW available_library_album_genres AS
SELECT relation.* FROM library_album_genres AS relation
WHERE EXISTS (
    SELECT 1 FROM available_library_tracks AS tracks
    JOIN library_track_genres AS track_relation ON track_relation.track_id = tracks.id
    WHERE tracks.album_id = relation.album_id AND track_relation.genre_id = relation.genre_id
);

CREATE VIEW available_library_album_styles AS
SELECT relation.* FROM library_album_styles AS relation
WHERE EXISTS (
    SELECT 1 FROM available_library_tracks AS tracks
    JOIN library_track_styles AS track_relation ON track_relation.track_id = tracks.id
    WHERE tracks.album_id = relation.album_id AND track_relation.style_id = relation.style_id
);

CREATE TABLE album_metadata_overrides (
    album_key TEXT PRIMARY KEY REFERENCES library_album_ids(album_key),
    provider TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL CHECK (length(entity_id) > 0),
    source_url TEXT NOT NULL,
    CHECK ((provider = 'discogs' AND entity_type IN ('release', 'master'))
        OR (provider = 'musicbrainz' AND entity_type IN ('release', 'release-group')))
);

-- Successful metadata is cached even while artwork remains retryable.
-- These results and their thumbnails survive library rebuilds and override removal.
CREATE TABLE metadata_entity_cache (
    provider TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    result_json TEXT NOT NULL CHECK (json_valid(result_json)),
    fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (provider, entity_type, entity_id)
);

CREATE TABLE library_album_overrides (
    album_key TEXT PRIMARY KEY REFERENCES library_album_ids(album_key),
    provider TEXT NOT NULL,
    source_url TEXT NOT NULL,
    genres_json TEXT NOT NULL CHECK (json_valid(genres_json)),
    thumbnail_cache_name TEXT
);

-- Ordinary selected artwork stays intact underneath an album override.
CREATE VIEW library_album_artwork_candidates AS
SELECT ids.id AS album_id, thumbnail.id AS thumbnail_cache_id,
    -1 AS priority, 0 AS disc_number, 0 AS missing_track_number,
    0 AS track_number, '' AS path, 0 AS track_id, 0 AS artwork_id
FROM library_album_overrides AS overrides
JOIN library_album_ids AS ids ON ids.album_key = overrides.album_key
JOIN thumbnail_cache AS thumbnail ON thumbnail.name = overrides.thumbnail_cache_name
UNION ALL
SELECT tracks.album_id, artwork.thumbnail_cache_id,
    0, COALESCE(tracks.disc_number, 1), tracks.track_number IS NULL,
    tracks.track_number, tracks.path, tracks.id, artwork.id
FROM available_library_tracks AS tracks
JOIN library_track_artwork AS artwork ON artwork.track_id = tracks.id;

-- Scan results withheld until a user supplies a complete album identity.
-- These rows survive rebuilds; root_pos is resolved afresh for each build.
CREATE TABLE unrecognized_tracks (
    config_id TEXT NOT NULL CHECK (length(trim(config_id)) > 0),
    uploaded_path TEXT NOT NULL CHECK (length(trim(uploaded_path)) > 0),
    album TEXT CHECK (album IS NULL OR (length(trim(album)) > 0 AND trim(album) <> '__Unknown')),
    artists TEXT CHECK (artists IS NULL OR CASE WHEN json_valid(artists) THEN
        json_type(artists) = 'array' AND json_array_length(artists) > 0 ELSE 0 END),
    status TEXT GENERATED ALWAYS AS (
        CASE WHEN album IS NOT NULL AND artists IS NOT NULL THEN 'ready' ELSE 'issue' END
    ) VIRTUAL,
    path_dir TEXT NOT NULL,
    stem TEXT NOT NULL,
    ext TEXT,
    file_size INTEGER NOT NULL,
    file_mtime_unix_nano INTEGER NOT NULL,
    duration_seconds INTEGER NOT NULL CHECK (duration_seconds >= 0),
    track_metadata TEXT NOT NULL CHECK (json_valid(track_metadata) AND json_type(track_metadata) = 'object'),
    -- App-managed upload paths include a globally unique job prefix.
    PRIMARY KEY (uploaded_path)
) WITHOUT ROWID;

-- CHECK constraints cannot inspect individual JSON array entries.
CREATE TRIGGER validate_unrecognized_artists_insert
BEFORE INSERT ON unrecognized_tracks WHEN NEW.artists IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'artists must contain nonblank recognized strings')
    WHERE EXISTS (SELECT 1 FROM json_each(NEW.artists)
        WHERE type <> 'text' OR length(trim(value)) = 0 OR trim(value) = '__Unknown');
END;
CREATE TRIGGER validate_unrecognized_artists_update
BEFORE UPDATE OF artists ON unrecognized_tracks WHEN NEW.artists IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'artists must contain nonblank recognized strings')
    WHERE EXISTS (SELECT 1 FROM json_each(NEW.artists)
        WHERE type <> 'text' OR length(trim(value)) = 0 OR trim(value) = '__Unknown');
END;

CREATE TABLE unrecognized_artwork (
    uploaded_path TEXT NOT NULL,
    artwork_pos INTEGER NOT NULL,
    picture_type TEXT NOT NULL,
    artwork_content_type TEXT,
    artwork_content_size INTEGER NOT NULL,
    artwork_bytes BLOB NOT NULL,
    thumbnail_cache_name TEXT,
    PRIMARY KEY (uploaded_path, artwork_pos),
    FOREIGN KEY (uploaded_path)
        REFERENCES unrecognized_tracks (uploaded_path) ON DELETE CASCADE
) WITHOUT ROWID;

-- Shared by unresolved tracks using the ordinary directory association rule.
-- kind preserves covers even when no thumbnail could be generated.
CREATE TABLE unrecognized_sidecars (
    config_id TEXT NOT NULL,
    path TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('cover', 'other')),
    path_dir TEXT NOT NULL,
    stem TEXT NOT NULL,
    ext TEXT NOT NULL,
    file_size INTEGER NOT NULL,
    file_mtime_unix_nano INTEGER NOT NULL,
    thumbnail_cache_name TEXT,
    PRIMARY KEY (config_id, path)
) WITHOUT ROWID;
