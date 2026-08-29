CREATE TABLE app_metadata (
    key   INTEGER PRIMARY KEY,
    value TEXT    NOT NULL
);

CREATE TABLE taxonomy_genres (
    genre TEXT PRIMARY KEY COLLATE NOCASE
);

CREATE TABLE taxonomy_styles (
    style        TEXT PRIMARY KEY COLLATE NOCASE,
    parent_genre TEXT NOT NULL    COLLATE NOCASE,

    FOREIGN KEY (parent_genre)
      REFERENCES taxonomy_genres (genre)
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
        and source in ('discogs_20260301_masters', 'mb_genres_v0')
    )
);

CREATE TABLE album_artist_split_mappings (
    album_key      TEXT PRIMARY KEY,
    mapped_artists TEXT NOT NULL   , -- JSON array

    CHECK(json_valid(mapped_artists))
) WITHOUT ROWID;

CREATE TABLE library_roots (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    pos       INTEGER NOT NULL                 ,
    path      TEXT    NOT NULL                 ,

    UNIQUE (pos)                               ,
    UNIQUE (path)
);

CREATE TABLE library_albums (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    album_key             TEXT    NOT NULL                 ,
    album                 TEXT    NOT NULL                 ,
    format                TEXT    NOT NULL                 ,
    track_count           INTEGER                          ,
    disc_count            INTEGER                          ,
    release_date          TEXT                             ,
    original_release_date TEXT                             ,
    recorded_date         TEXT                             ,
    release_year          INTEGER                          ,
    original_release_year INTEGER                          ,
    release_country       TEXT                             ,
    label                 TEXT                             ,
    composer              TEXT                             ,
    copyright             TEXT                             ,
    purchase_date         TEXT                             ,
    is_compilation        INTEGER                          ,

    UNIQUE (album_key)
);

CREATE TABLE library_album_artists (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    album_id   INTEGER NOT NULL                 ,
    artist_key TEXT    NOT NULL                 ,
    artist     TEXT    NOT NULL                 ,

    UNIQUE (album_id, artist_key)               ,
    FOREIGN KEY (album_id)
      REFERENCES library_albums (id)
      ON DELETE CASCADE
);

CREATE TABLE library_track_sidecars (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id             INTEGER NOT NULL                 ,
    stem                 TEXT    NOT NULL                 ,
    ext                  TEXT    NOT NULL                 ,
    path                 TEXT    NOT NULL                 ,
    file_size            INTEGER NOT NULL                 ,
    file_mtime_unix_nano INTEGER NOT NULL                 ,

    FOREIGN KEY (track_id)
      REFERENCES library_tracks (id)
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
    artwork_content_type TEXT    NOT NULL                 ,
    artwork_content_size INTEGER NOT NULL                 ,
    artwork_bytes        BLOB    NOT NULL                 ,

    UNIQUE (name, width_px)                               ,
    CHECK (length(name) = 64)                             ,
    CHECK (width_px > 0)                                  ,
    CHECK (
            artwork_content_size > 0
        AND length(artwork_bytes) = artwork_content_size
    )
);

CREATE TABLE library_track_artwork (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id           INTEGER NOT NULL                 ,
    thumbnail_cache_id INTEGER NOT NULL                 ,

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
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id INTEGER NOT NULL                 ,
    genre    TEXT    NOT NULL                 ,

    UNIQUE (track_id, genre)                  ,
    FOREIGN KEY (track_id)
      REFERENCES library_tracks (id)
      ON DELETE CASCADE                       ,
    FOREIGN KEY (genre)
      REFERENCES taxonomy_genres (genre)
      ON DELETE CASCADE
);

CREATE TABLE library_track_styles (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id INTEGER NOT NULL                 ,
    style    TEXT    NOT NULL                 ,

    UNIQUE (track_id, style)                  ,
    FOREIGN KEY (track_id)
      REFERENCES library_tracks (id)
      ON DELETE CASCADE                       ,
    FOREIGN KEY (style)
      REFERENCES taxonomy_styles (style)
      ON DELETE CASCADE
);

CREATE TABLE library_tracks (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    root_id              INTEGER NOT NULL                 ,
    album_id             INTEGER NOT NULL                 ,
    ext                  TEXT    NOT NULL                 ,
    path                 TEXT    NOT NULL                 ,
    file_size            INTEGER NOT NULL                 ,
    file_mtime_unix_nano INTEGER NOT NULL                 ,
    track_metadata       TEXT    NOT NULL                 ,
    scan_error           TEXT    NOT NULL                 ,
    title                TEXT    NOT NULL                 ,
    artists              TEXT    NOT NULL                 , -- JSON Array
    work                 TEXT                             ,
    grouping             TEXT                             ,
    movement_name        TEXT                             ,
    movement_number      INTEGER                          ,
    movement_count       INTEGER                          ,
    show_movement        INTEGER                          ,
    track_number         INTEGER                          ,
    disc_number          INTEGER                          ,

    UNIQUE (root_id, path)                                ,
    CHECK (
            json_valid(artists)
        AND json_type(artists) = 'array'
    )                                                     ,
    FOREIGN KEY (root_id)
      REFERENCES library_roots (id)
      ON DELETE CASCADE                                   ,
    FOREIGN KEY (album_id)
      REFERENCES library_albums (id)
      ON DELETE CASCADE
);

CREATE TABLE itunes_lookup_image_cache (
    cache_key            TEXT    PRIMARY KEY,
    lookup_kind          TEXT    NOT NULL,
    lookup_id            TEXT    NOT NULL,
    result_kind          TEXT    NOT NULL,
    fetched_at           TEXT    NOT NULL,
    lookup_url           TEXT    NOT NULL,
    artwork_url          TEXT    NOT NULL,
    artwork_content_type TEXT    NOT NULL,
    artwork_content_size INTEGER NOT NULL,
    artwork_bytes        BLOB    NOT NULL,
    thumbnail_cache_name TEXT            ,

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
