from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime
from importlib.resources import files
from pathlib import Path
from types import TracebackType
from typing import Iterable
from urllib.parse import urlsplit

from .._compat import UTC
from ..file_metadata import file_created_at
from ..library_sources import SOURCE_KIND_LOCAL, path_is_in_source
from ..models import ALBUM_ARTWORK_HEIGHT, UNKNOWN_METADATA_TAG
from ..taxonomy_data import parse_taxonomy_tsv

TAXONOMY_METADATA_KEY = "taxonomy_tsv_sha256"
UNKNOWN_GENRE_TAG = UNKNOWN_METADATA_TAG
ALBUM_SEARCH_METADATA_KEY = "album_search_index_version"
ALBUM_SEARCH_INDEX_VERSION = "5"
ALBUM_SEARCH_COLUMNS = {"album_id", "artist", "album"}
ALBUM_ROLLUP_METADATA_KEY = "album_rollup_version"
ALBUM_ROLLUP_COUNT_METADATA_KEY = "album_rollup_album_count"
ALBUM_ROLLUP_VERSION = "3"
ROOT_SCAN_STATS_METADATA_KEY = "root_scan_stats_version"
ROOT_SCAN_STATS_ROOT_COUNT_METADATA_KEY = "root_scan_stats_root_count"
ROOT_SCAN_STATS_TRACK_COUNT_METADATA_KEY = "root_scan_stats_track_count"
ROOT_SCAN_STATS_ALBUM_COUNT_METADATA_KEY = "root_scan_stats_album_count"
ROOT_SCAN_STATS_ALBUM_ROOT_COUNT_METADATA_KEY = "root_scan_stats_album_root_count"
ROOT_SCAN_STATS_VERSION = "6"

LIBRARY_TRACK_ARTWORK_SCHEMA = """
CREATE TABLE IF NOT EXISTS library_track_artwork (
    track_id INTEGER NOT NULL,
    height_px INTEGER NOT NULL,
    mime_type TEXT NOT NULL,
    data BLOB NOT NULL,
    PRIMARY KEY (track_id, height_px),
    FOREIGN KEY (track_id) REFERENCES library_tracks (track_id) ON DELETE CASCADE
);
"""

LIBRARY_PLAYLIST_SCHEMA = """
CREATE TABLE IF NOT EXISTS library_playlists (
    playlist_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'local' CHECK (kind IN ('local', 'remote')),
    source TEXT NOT NULL DEFAULT 'manual' CHECK (source IN ('manual', 'file_import')),
    cover_svg TEXT NOT NULL DEFAULT '',
    cover_mime_type TEXT NOT NULL DEFAULT '',
    cover_data BLOB,
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_library_playlists_name
    ON library_playlists (name);

CREATE TABLE IF NOT EXISTS library_playlist_items (
    playlist_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
    playlist_id INTEGER NOT NULL,
    position INTEGER NOT NULL,
    path TEXT NOT NULL,
    track_id INTEGER,
    title TEXT,
    duration_seconds REAL,
    duration_is_indeterminate INTEGER NOT NULL DEFAULT 0,
    genre TEXT,
    cover_url TEXT,
    UNIQUE (playlist_id, position),
    FOREIGN KEY (playlist_id) REFERENCES library_playlists (playlist_id) ON DELETE CASCADE,
    FOREIGN KEY (track_id) REFERENCES library_tracks (track_id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_library_playlist_items_playlist_id
    ON library_playlist_items (playlist_id);
CREATE INDEX IF NOT EXISTS idx_library_playlist_items_track_id
    ON library_playlist_items (track_id);
"""

DATABASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS app_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS taxonomy_genres (
    genre TEXT PRIMARY KEY COLLATE NOCASE
);

CREATE TABLE IF NOT EXISTS taxonomy_styles (
    style TEXT PRIMARY KEY COLLATE NOCASE,
    parent_genre TEXT NOT NULL COLLATE NOCASE,
    FOREIGN KEY (parent_genre) REFERENCES taxonomy_genres (genre)
);
CREATE INDEX IF NOT EXISTS idx_taxonomy_styles_parent_genre
    ON taxonomy_styles (parent_genre);

CREATE TABLE IF NOT EXISTS taxonomy_aliases (
    alias TEXT NOT NULL COLLATE NOCASE,
    canonical_kind TEXT NOT NULL CHECK (canonical_kind IN ('genre', 'style')),
    canonical TEXT NOT NULL COLLATE NOCASE,
    PRIMARY KEY (alias, canonical_kind)
);
CREATE INDEX IF NOT EXISTS idx_taxonomy_aliases_canonical
    ON taxonomy_aliases (canonical_kind, canonical);

CREATE TABLE IF NOT EXISTS library_roots (
    position INTEGER PRIMARY KEY,
    root_path TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL DEFAULT 'local',
    source_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS library_root_stats (
    root_position INTEGER PRIMARY KEY,
    tracks_scanned INTEGER NOT NULL DEFAULT 0,
    albums_scanned INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (root_position) REFERENCES library_roots (position) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS library_stats (
    stats_id INTEGER PRIMARY KEY CHECK (stats_id = 1),
    tracks_scanned INTEGER NOT NULL DEFAULT 0,
    albums_scanned INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS library_root_album_artist_stats (
    root_position INTEGER NOT NULL,
    album_artist TEXT NOT NULL,
    tracks_scanned INTEGER NOT NULL DEFAULT 0,
    albums_scanned INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (root_position, album_artist),
    FOREIGN KEY (root_position) REFERENCES library_roots (position) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_library_root_album_artist_stats_artist
    ON library_root_album_artist_stats (album_artist, root_position);

CREATE TABLE IF NOT EXISTS library_album_artist_stats (
    album_artist TEXT PRIMARY KEY,
    tracks_scanned INTEGER NOT NULL DEFAULT 0,
    albums_scanned INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS album_artist_split_mappings (
    album_artist TEXT PRIMARY KEY COLLATE NOCASE,
    mapped_artists TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS player_jobs (
    job_id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    cancel_requested_at TEXT,
    kind TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'canceled')),
    message TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    context_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_player_jobs_created_at
    ON player_jobs (created_at DESC, job_id DESC);
CREATE INDEX IF NOT EXISTS idx_player_jobs_status
    ON player_jobs (status, created_at, job_id);

CREATE TABLE IF NOT EXISTS player_queue_state (
    state_id INTEGER PRIMARY KEY CHECK (state_id = 1),
    position INTEGER NOT NULL DEFAULT 0,
    paused INTEGER NOT NULL DEFAULT 1 CHECK (paused IN (0, 1)),
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS player_queue_items (
    position INTEGER PRIMARY KEY,
    playback_id INTEGER NOT NULL,
    snapshot_json TEXT NOT NULL DEFAULT '{}',
    errored INTEGER NOT NULL DEFAULT 0 CHECK (errored IN (0, 1))
);
CREATE INDEX IF NOT EXISTS idx_player_queue_items_playback_id
    ON player_queue_items (playback_id);

CREATE TABLE IF NOT EXISTS play_events (
    play_event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    played_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    playback_id INTEGER,
    track_key TEXT,
    album_id TEXT,
    playlist_key TEXT,
    snapshot_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_play_events_played_at
    ON play_events (played_at DESC, play_event_id DESC);
CREATE INDEX IF NOT EXISTS idx_play_events_track_key
    ON play_events (track_key, played_at DESC);
CREATE INDEX IF NOT EXISTS idx_play_events_album_id
    ON play_events (album_id, played_at DESC);
CREATE INDEX IF NOT EXISTS idx_play_events_playlist_key
    ON play_events (playlist_key, played_at DESC);

CREATE TABLE IF NOT EXISTS play_now_playing (
    session_key TEXT PRIMARY KEY,
    updated_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    playback_id INTEGER,
    track_key TEXT,
    album_id TEXT,
    playlist_key TEXT,
    snapshot_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS opensubsonic_clients (
    client_name TEXT PRIMARY KEY,
    last_seen_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_opensubsonic_clients_last_seen
    ON opensubsonic_clients (last_seen_at DESC, client_name);

CREATE TABLE IF NOT EXISTS play_track_stats (
    track_key TEXT PRIMARY KEY,
    play_count INTEGER NOT NULL DEFAULT 0,
    last_played_at TEXT NOT NULL,
    track_id INTEGER,
    album_id TEXT,
    path TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    artist TEXT NOT NULL DEFAULT '',
    album TEXT NOT NULL DEFAULT '',
    snapshot_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_play_track_stats_recent
    ON play_track_stats (last_played_at DESC, play_count DESC, track_key);

CREATE TABLE IF NOT EXISTS play_album_stats (
    album_id TEXT PRIMARY KEY,
    play_count INTEGER NOT NULL DEFAULT 0,
    last_played_at TEXT NOT NULL,
    album TEXT NOT NULL DEFAULT '',
    artist TEXT NOT NULL DEFAULT '',
    art_track_id INTEGER,
    snapshot_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_play_album_stats_recent
    ON play_album_stats (last_played_at DESC, play_count DESC, album_id)
    WHERE album_id IS NOT NULL AND album_id != '';
CREATE INDEX IF NOT EXISTS idx_play_album_stats_frequent
    ON play_album_stats (play_count DESC, last_played_at DESC, album_id)
    WHERE album_id IS NOT NULL AND album_id != '';

CREATE TABLE IF NOT EXISTS play_artist_stats (
    artist_key TEXT PRIMARY KEY,
    artist TEXT NOT NULL,
    play_count INTEGER NOT NULL DEFAULT 0,
    last_played_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_play_artist_stats_recent
    ON play_artist_stats (last_played_at DESC, play_count DESC, artist_key);

CREATE TABLE IF NOT EXISTS play_playlist_stats (
    playlist_key TEXT PRIMARY KEY,
    play_count INTEGER NOT NULL DEFAULT 0,
    last_played_at TEXT NOT NULL,
    playlist_id INTEGER,
    path TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL DEFAULT '',
    cover_svg TEXT NOT NULL DEFAULT '',
    snapshot_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_play_playlist_stats_recent
    ON play_playlist_stats (last_played_at DESC, play_count DESC, playlist_key);

CREATE TABLE IF NOT EXISTS play_genre_stats (
    genre_key TEXT PRIMARY KEY,
    genre TEXT NOT NULL,
    play_count INTEGER NOT NULL DEFAULT 0,
    last_played_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_play_genre_stats_recent
    ON play_genre_stats (last_played_at DESC, play_count DESC, genre_key);

CREATE TABLE IF NOT EXISTS album_user_state (
    album_id TEXT PRIMARY KEY,
    starred_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_album_user_state_starred
    ON album_user_state (starred_at DESC, album_id)
    WHERE starred_at IS NOT NULL;

CREATE TABLE IF NOT EXISTS library_albums (
    album_id TEXT PRIMARY KEY,
    album TEXT NOT NULL,
    year INTEGER,
    track_count INTEGER NOT NULL,
    file_created_at TEXT NOT NULL DEFAULT '',
    added_at TEXT NOT NULL DEFAULT '',
    starred_at TEXT,
    artist_sort_key TEXT NOT NULL DEFAULT '',
    album_sort_key TEXT NOT NULL DEFAULT '',
    genre_sort_key TEXT NOT NULL DEFAULT '',
    art_track_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_library_albums_album ON library_albums (album);

CREATE TABLE IF NOT EXISTS library_album_artists (
    album_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    artist TEXT NOT NULL,
    PRIMARY KEY (album_id, position),
    FOREIGN KEY (album_id) REFERENCES library_albums (album_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_library_album_artists_artist
    ON library_album_artists (artist COLLATE NOCASE, album_id);

CREATE TABLE IF NOT EXISTS library_album_roots (
    album_id TEXT NOT NULL,
    root_position INTEGER NOT NULL,
    track_count INTEGER NOT NULL,
    genre_sort_key TEXT NOT NULL DEFAULT '',
    art_track_id INTEGER,
    PRIMARY KEY (album_id, root_position),
    FOREIGN KEY (album_id) REFERENCES library_albums (album_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_library_album_roots_root_position
    ON library_album_roots (root_position, album_id);

CREATE TABLE IF NOT EXISTS library_album_genres (
    album_id TEXT NOT NULL,
    genre TEXT NOT NULL,
    PRIMARY KEY (album_id, genre),
    FOREIGN KEY (album_id) REFERENCES library_albums (album_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_library_album_genres_genre
    ON library_album_genres (genre, album_id);

CREATE TABLE IF NOT EXISTS library_album_styles (
    album_id TEXT NOT NULL,
    style TEXT NOT NULL,
    PRIMARY KEY (album_id, style),
    FOREIGN KEY (album_id) REFERENCES library_albums (album_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_library_album_styles_style
    ON library_album_styles (style, album_id);

CREATE TABLE IF NOT EXISTS library_album_genre_styles (
    album_id TEXT NOT NULL,
    genre TEXT NOT NULL,
    style TEXT NOT NULL,
    PRIMARY KEY (album_id, genre, style),
    FOREIGN KEY (album_id) REFERENCES library_albums (album_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_library_album_genre_styles_genre_style
    ON library_album_genre_styles (genre, style, album_id);

CREATE TABLE IF NOT EXISTS library_album_root_genres (
    album_id TEXT NOT NULL,
    root_position INTEGER NOT NULL,
    genre TEXT NOT NULL,
    PRIMARY KEY (album_id, root_position, genre),
    FOREIGN KEY (album_id) REFERENCES library_albums (album_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_library_album_root_genres_genre
    ON library_album_root_genres (root_position, genre, album_id);

CREATE TABLE IF NOT EXISTS library_album_root_styles (
    album_id TEXT NOT NULL,
    root_position INTEGER NOT NULL,
    style TEXT NOT NULL,
    PRIMARY KEY (album_id, root_position, style),
    FOREIGN KEY (album_id) REFERENCES library_albums (album_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_library_album_root_styles_style
    ON library_album_root_styles (root_position, style, album_id);

CREATE TABLE IF NOT EXISTS library_album_root_genre_styles (
    album_id TEXT NOT NULL,
    root_position INTEGER NOT NULL,
    genre TEXT NOT NULL,
    style TEXT NOT NULL,
    PRIMARY KEY (album_id, root_position, genre, style),
    FOREIGN KEY (album_id) REFERENCES library_albums (album_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_library_album_root_genre_styles_genre_style
    ON library_album_root_genre_styles (root_position, genre, style, album_id);

CREATE TABLE IF NOT EXISTS album_musicbrainz_links (
    file_album_id TEXT NOT NULL,
    release_mbid TEXT,
    release_group_mbid TEXT,
    CHECK (
        COALESCE(release_mbid, '') != ''
        OR COALESCE(release_group_mbid, '') != ''
    )
);
CREATE INDEX IF NOT EXISTS idx_album_musicbrainz_links_release
    ON album_musicbrainz_links (release_mbid);
CREATE INDEX IF NOT EXISTS idx_album_musicbrainz_links_release_group
    ON album_musicbrainz_links (release_group_mbid);

CREATE TABLE IF NOT EXISTS album_musicbrainz_track_links (
    path TEXT PRIMARY KEY,
    file_album_id TEXT NOT NULL,
    release_mbid TEXT,
    release_group_mbid TEXT,
    CHECK (
        COALESCE(release_mbid, '') != ''
        OR COALESCE(release_group_mbid, '') != ''
    )
);
CREATE INDEX IF NOT EXISTS idx_album_musicbrainz_track_links_file_album
    ON album_musicbrainz_track_links (file_album_id);
CREATE INDEX IF NOT EXISTS idx_album_musicbrainz_track_links_release
    ON album_musicbrainz_track_links (release_mbid);
CREATE INDEX IF NOT EXISTS idx_album_musicbrainz_track_links_release_group
    ON album_musicbrainz_track_links (release_group_mbid);

CREATE TABLE IF NOT EXISTS library_tracks (
    track_id INTEGER PRIMARY KEY AUTOINCREMENT,
    album_id TEXT,
    root_position INTEGER,
    path TEXT NOT NULL UNIQUE,
    file_created_at TEXT,
    file_modified_at_ns INTEGER,
    file_size_bytes INTEGER,
    sidecar_artwork_path TEXT,
    sidecar_artwork_modified_at_ns INTEGER,
    sidecar_artwork_size_bytes INTEGER,
    file_type TEXT,
    scan_error TEXT,
    artist TEXT,
    album_artist TEXT,
    composer TEXT,
    album TEXT,
    title TEXT,
    play_fingerprint TEXT,
    work TEXT,
    grouping TEXT,
    movement_name TEXT,
    is_compilation INTEGER NOT NULL DEFAULT 0,
    track_number TEXT,
    disc_number TEXT,
    date TEXT,
    duration_seconds REAL,
    bitrate INTEGER,
    FOREIGN KEY (album_id) REFERENCES library_albums (album_id) ON DELETE SET NULL,
    FOREIGN KEY (root_position) REFERENCES library_roots (position) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_library_tracks_album_id ON library_tracks (album_id);
CREATE INDEX IF NOT EXISTS idx_library_tracks_artist ON library_tracks (artist);
CREATE INDEX IF NOT EXISTS idx_library_tracks_album ON library_tracks (album);
CREATE INDEX IF NOT EXISTS idx_library_tracks_title ON library_tracks (title);

CREATE TABLE IF NOT EXISTS library_track_sources (
    track_id INTEGER PRIMARY KEY,
    source_kind TEXT NOT NULL,
    root_position INTEGER,
    canonical_path TEXT NOT NULL,
    object_key TEXT,
    etag TEXT,
    version_id TEXT,
    last_modified TEXT,
    content_type TEXT,
    size_bytes INTEGER,
    sidecar_object_key TEXT,
    sidecar_etag TEXT,
    sidecar_version_id TEXT,
    sidecar_last_modified TEXT,
    sidecar_content_type TEXT,
    sidecar_size_bytes INTEGER,
    FOREIGN KEY (track_id) REFERENCES library_tracks (track_id) ON DELETE CASCADE,
    FOREIGN KEY (root_position) REFERENCES library_roots (position) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_library_track_sources_canonical_path
    ON library_track_sources (canonical_path);
CREATE INDEX IF NOT EXISTS idx_library_track_sources_source
    ON library_track_sources (source_kind, root_position);

CREATE TABLE IF NOT EXISTS library_track_genres (
    track_id INTEGER NOT NULL,
    position INTEGER NOT NULL,
    genre TEXT NOT NULL,
    PRIMARY KEY (track_id, position),
    FOREIGN KEY (track_id) REFERENCES library_tracks (track_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_library_track_genres_genre ON library_track_genres (genre);

CREATE TABLE IF NOT EXISTS library_track_styles (
    track_id INTEGER NOT NULL,
    position INTEGER NOT NULL,
    style TEXT NOT NULL,
    PRIMARY KEY (track_id, position),
    FOREIGN KEY (track_id) REFERENCES library_tracks (track_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_library_track_styles_style ON library_track_styles (style);

CREATE VIRTUAL TABLE IF NOT EXISTS library_album_search USING fts5(
    album_id UNINDEXED,
    artist,
    album,
    tokenize = 'unicode61'
);

CREATE TABLE IF NOT EXISTS musicbrainz_entity_cache (
    entity_type TEXT NOT NULL CHECK (entity_type IN ('release', 'release-group')),
    mbid TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    endpoint_url TEXT NOT NULL,
    response_json TEXT NOT NULL,
    PRIMARY KEY (entity_type, mbid)
);

CREATE TABLE IF NOT EXISTS cover_art_archive_entity_cache (
    entity_type TEXT NOT NULL CHECK (entity_type IN ('release', 'release-group')),
    mbid TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    endpoint_url TEXT NOT NULL,
    response_json TEXT NOT NULL,
    PRIMARY KEY (entity_type, mbid)
);

CREATE TABLE IF NOT EXISTS cover_art_archive_image_cache (
    image_url TEXT PRIMARY KEY,
    fetched_at TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    data BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS itunes_lookup_image_cache (
    cache_key TEXT PRIMARY KEY,
    lookup_kind TEXT NOT NULL CHECK (lookup_kind IN ('album', 'track')),
    lookup_id TEXT NOT NULL,
    result_kind TEXT NOT NULL DEFAULT 'hit' CHECK (result_kind IN ('hit', 'missing')),
    fetched_at TEXT NOT NULL,
    lookup_url TEXT NOT NULL,
    artwork_url TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    data BLOB NOT NULL
);

""" + LIBRARY_TRACK_ARTWORK_SCHEMA


class ClosingConnection(sqlite3.Connection):
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def connect_database(
    path: Path,
    *,
    create: bool = True,
) -> sqlite3.Connection:
    if not create and not path.exists():
        raise FileNotFoundError(path)

    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, factory=ClosingConnection)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.executescript(DATABASE_SCHEMA)
        connection.executescript(LIBRARY_PLAYLIST_SCHEMA)
        migrate_player_jobs_schema(connection)
        migrate_listening_schema(connection)
        migrate_library_schema(connection)
        migrate_album_user_state_schema(connection)
        migrate_album_musicbrainz_link_key_schema(connection)
        migrate_album_musicbrainz_schema(connection)
        migrate_album_search_schema(connection)
        migrate_itunes_lookup_cache_schema(connection)
        ensure_album_search_index(connection)
        ensure_album_rollups(connection)
        ensure_root_scan_stats(connection)
        seed_runtime_taxonomy(connection)
    except Exception:
        connection.close()
        raise
    return connection


def migrate_player_jobs_schema(connection: sqlite3.Connection) -> None:
    connection.execute("DROP TABLE IF EXISTS player_actions")


def migrate_listening_schema(connection: sqlite3.Connection) -> None:
    for table_name in ("play_events", "play_now_playing"):
        columns = table_columns(connection, table_name)
        if columns and "source" not in columns:
            connection.execute(
                f"ALTER TABLE {table_name} ADD COLUMN source TEXT NOT NULL DEFAULT ''"
            )


def migrate_library_schema(connection: sqlite3.Connection) -> None:
    root_columns = table_columns(connection, "library_roots")
    if root_columns and "kind" not in root_columns:
        connection.execute(
            "ALTER TABLE library_roots ADD COLUMN kind TEXT NOT NULL DEFAULT 'local'"
        )
    if root_columns and "source_json" not in table_columns(connection, "library_roots"):
        connection.execute(
            "ALTER TABLE library_roots ADD COLUMN source_json TEXT NOT NULL DEFAULT '{}'"
        )

    columns = table_columns(connection, "library_tracks")
    created_track_file_created_at = False
    if "root_position" not in columns:
        connection.execute("ALTER TABLE library_tracks ADD COLUMN root_position INTEGER")
        backfill_library_track_roots(connection)
    if "composer" not in columns:
        connection.execute("ALTER TABLE library_tracks ADD COLUMN composer TEXT")
    if "play_fingerprint" not in columns:
        connection.execute("ALTER TABLE library_tracks ADD COLUMN play_fingerprint TEXT")
    if "work" not in columns:
        connection.execute("ALTER TABLE library_tracks ADD COLUMN work TEXT")
    if "grouping" not in columns:
        connection.execute("ALTER TABLE library_tracks ADD COLUMN grouping TEXT")
    if "is_compilation" not in columns:
        connection.execute(
            "ALTER TABLE library_tracks ADD COLUMN is_compilation INTEGER NOT NULL DEFAULT 0"
        )
    if "file_created_at" not in columns:
        connection.execute("ALTER TABLE library_tracks ADD COLUMN file_created_at TEXT")
        created_track_file_created_at = True
    if "file_modified_at_ns" not in columns:
        connection.execute("ALTER TABLE library_tracks ADD COLUMN file_modified_at_ns INTEGER")
    if "file_size_bytes" not in columns:
        connection.execute("ALTER TABLE library_tracks ADD COLUMN file_size_bytes INTEGER")
    if "sidecar_artwork_path" not in columns:
        connection.execute("ALTER TABLE library_tracks ADD COLUMN sidecar_artwork_path TEXT")
    if "sidecar_artwork_modified_at_ns" not in columns:
        connection.execute(
            "ALTER TABLE library_tracks ADD COLUMN sidecar_artwork_modified_at_ns INTEGER"
        )
    if "sidecar_artwork_size_bytes" not in columns:
        connection.execute(
            "ALTER TABLE library_tracks ADD COLUMN sidecar_artwork_size_bytes INTEGER"
        )
    ensure_library_track_sources(connection)

    album_columns = table_columns(connection, "library_albums")
    created_album_file_created_at = False
    if album_columns and "file_created_at" not in album_columns:
        connection.execute(
            "ALTER TABLE library_albums ADD COLUMN file_created_at TEXT NOT NULL DEFAULT ''"
        )
        created_album_file_created_at = True
    if album_columns and "file_created_at" in table_columns(connection, "library_albums"):
        connection.execute(
            "UPDATE library_albums SET file_created_at = '' WHERE file_created_at IS NULL"
        )
    created_album_added_at = False
    if album_columns and "added_at" not in table_columns(connection, "library_albums"):
        connection.execute(
            "ALTER TABLE library_albums ADD COLUMN added_at TEXT NOT NULL DEFAULT ''"
        )
        created_album_added_at = True
    if album_columns and "added_at" in table_columns(connection, "library_albums"):
        connection.execute(
            "UPDATE library_albums SET added_at = '' WHERE added_at IS NULL"
        )
    if album_columns and "starred_at" not in album_columns:
        connection.execute("ALTER TABLE library_albums ADD COLUMN starred_at TEXT")
    if album_columns and "artist_sort_key" not in album_columns:
        connection.execute(
            "ALTER TABLE library_albums ADD COLUMN artist_sort_key TEXT NOT NULL DEFAULT ''"
        )
    if album_columns and "album_sort_key" not in album_columns:
        connection.execute(
            "ALTER TABLE library_albums ADD COLUMN album_sort_key TEXT NOT NULL DEFAULT ''"
        )
    if album_columns and "genre_sort_key" not in album_columns:
        connection.execute(
            "ALTER TABLE library_albums ADD COLUMN genre_sort_key TEXT NOT NULL DEFAULT ''"
        )
    if album_columns and "art_track_id" not in album_columns:
        connection.execute("ALTER TABLE library_albums ADD COLUMN art_track_id INTEGER")

    album_root_columns = table_columns(connection, "library_album_roots")
    if album_root_columns and "genre_sort_key" not in album_root_columns:
        connection.execute(
            "ALTER TABLE library_album_roots ADD COLUMN genre_sort_key TEXT NOT NULL DEFAULT ''"
        )

    ensure_library_playlist_schema(connection)

    playlist_item_columns = table_columns(connection, "library_playlist_items")
    if playlist_item_columns and "duration_is_indeterminate" not in playlist_item_columns:
        connection.execute(
            """
            ALTER TABLE library_playlist_items
            ADD COLUMN duration_is_indeterminate INTEGER NOT NULL DEFAULT 0
            """
        )
        connection.execute(
            """
            UPDATE library_playlist_items
            SET duration_is_indeterminate = 1,
                duration_seconds = NULL
            WHERE track_id IS NULL
                AND duration_seconds IS NOT NULL
                AND duration_seconds <= 0
            """
        )

    if created_track_file_created_at:
        backfill_library_file_created_at(connection)
    if created_album_file_created_at or created_track_file_created_at:
        backfill_library_album_file_created_at(connection)
    if created_album_added_at:
        backfill_library_album_added_at(connection)

    connection.execute("DROP INDEX IF EXISTS idx_play_album_stats_recent")
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_play_album_stats_recent
            ON play_album_stats (last_played_at DESC, play_count DESC, album_id)
            WHERE album_id IS NOT NULL AND album_id != ''
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_play_album_stats_frequent
            ON play_album_stats (play_count DESC, last_played_at DESC, album_id)
            WHERE album_id IS NOT NULL AND album_id != ''
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_library_tracks_root_position
            ON library_tracks (root_position)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_library_tracks_root_album
            ON library_tracks (root_position, album_id)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_library_tracks_album_root
            ON library_tracks (album_id, root_position)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_library_tracks_play_fingerprint
            ON library_tracks (play_fingerprint)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_library_tracks_file_created_at
            ON library_tracks (file_created_at)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_library_albums_file_created_at
            ON library_albums (file_created_at)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_library_albums_added_at
            ON library_albums (added_at)
        """
    )
    connection.execute("DROP INDEX IF EXISTS idx_library_albums_recently_added_sort")
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_library_albums_recently_added_sort
            ON library_albums (
                CASE WHEN NULLIF(added_at, '') IS NULL THEN 1 ELSE 0 END,
                added_at DESC,
                artist_sort_key,
                CASE WHEN year IS NULL THEN 1 ELSE 0 END,
                year,
                album_sort_key,
                album_id
            )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_library_albums_artist_sort
            ON library_albums (
                artist_sort_key,
                CASE WHEN year IS NULL THEN 1 ELSE 0 END,
                year,
                album_sort_key,
                album_id
            )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_library_albums_album_sort
            ON library_albums (
                album_sort_key,
                artist_sort_key,
                CASE WHEN year IS NULL THEN 1 ELSE 0 END,
                year,
                album_id
            )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_library_albums_genre_sort
            ON library_albums (
                CASE WHEN NULLIF(genre_sort_key, '') IS NULL THEN 1 ELSE 0 END,
                genre_sort_key,
                artist_sort_key,
                CASE WHEN year IS NULL THEN 1 ELSE 0 END,
                year,
                album_sort_key,
                album_id
            )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_library_albums_starred_sort
            ON library_albums (
                starred_at DESC,
                artist_sort_key,
                CASE WHEN year IS NULL THEN 1 ELSE 0 END,
                year,
                album_sort_key,
                album_id
            )
            WHERE starred_at IS NOT NULL
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_library_album_roots_genre_sort
            ON library_album_roots (
                root_position,
                CASE WHEN NULLIF(genre_sort_key, '') IS NULL THEN 1 ELSE 0 END,
                genre_sort_key,
                album_id
            )
        """
    )
    connection.execute("DROP INDEX IF EXISTS idx_library_albums_has_cover")
    connection.execute("DROP INDEX IF EXISTS idx_library_albums_is_compilation")
    connection.execute("DROP INDEX IF EXISTS idx_library_albums_is_work")
    connection.execute("DROP INDEX IF EXISTS idx_library_playlists_file_created_at")
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_library_playlists_created_at
            ON library_playlists (created_at)
        """
    )
    ensure_library_album_artists_artist_index(connection)
    backfill_library_album_artists(connection)
    migrate_library_album_artist_column(connection)
    canonicalize_library_album_artists(connection)
    connection.execute("DROP TABLE IF EXISTS library_album_paths")


def ensure_library_playlist_schema(connection: sqlite3.Connection) -> None:
    columns = table_columns(connection, "library_playlists")
    if not columns:
        connection.executescript(LIBRARY_PLAYLIST_SCHEMA)
        return
    desired_columns = {
        "playlist_id",
        "name",
        "kind",
        "source",
        "cover_svg",
        "cover_mime_type",
        "cover_data",
        "created_at",
        "updated_at",
    }
    if "cover_mime_type" not in columns:
        connection.execute(
            "ALTER TABLE library_playlists ADD COLUMN cover_mime_type TEXT NOT NULL DEFAULT ''"
        )
        columns.add("cover_mime_type")
    if "cover_data" not in columns:
        connection.execute("ALTER TABLE library_playlists ADD COLUMN cover_data BLOB")
        columns.add("cover_data")
    if desired_columns.issubset(columns) and "path" not in columns and "file_created_at" not in columns:
        return

    item_columns = table_columns(connection, "library_playlist_items")
    item_rows = []
    if item_columns:
        item_rows = list(
            connection.execute(
                """
                SELECT
                    playlist_item_id,
                    playlist_id,
                    position,
                    path,
                    track_id,
                    title,
                    duration_seconds,
                    duration_is_indeterminate,
                    genre,
                    cover_url
                FROM library_playlist_items
                ORDER BY playlist_id, position, playlist_item_id
                """
            )
        )
    playlist_rows = list(
        connection.execute(
            """
            SELECT *
            FROM library_playlists
            ORDER BY playlist_id
            """
        )
    )
    remote_playlist_ids = {
        int(row["playlist_id"])
        for row in item_rows
        if is_http_url(str(row["path"]))
    }

    connection.execute("DROP TABLE IF EXISTS library_playlist_items")
    connection.execute("DROP TABLE IF EXISTS library_playlists")
    connection.executescript(LIBRARY_PLAYLIST_SCHEMA)

    now = utc_now_iso()
    for row in playlist_rows:
        keys = set(row.keys())
        playlist_id = int(row["playlist_id"])
        name = str(row["name"] or "Playlist")
        created_at = (
            str(row["created_at"])
            if "created_at" in keys and row["created_at"]
            else str(row["file_created_at"])
            if "file_created_at" in keys and row["file_created_at"]
            else now
        )
        updated_at = (
            str(row["updated_at"])
            if "updated_at" in keys and row["updated_at"]
            else created_at
        )
        source = (
            str(row["source"])
            if "source" in keys and str(row["source"] or "") in {"manual", "file_import"}
            else "file_import"
            if "path" in keys
            else "manual"
        )
        kind = (
            str(row["kind"])
            if "kind" in keys and str(row["kind"] or "") in {"local", "remote"}
            else "remote"
            if playlist_id in remote_playlist_ids
            else "local"
        )
        connection.execute(
            """
            INSERT INTO library_playlists (
                playlist_id,
                name,
                kind,
                source,
                cover_svg,
                cover_mime_type,
                cover_data,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                playlist_id,
                name,
                kind,
                source,
                str(row["cover_svg"] or "") if "cover_svg" in keys else "",
                str(row["cover_mime_type"] or "") if "cover_mime_type" in keys else "",
                row["cover_data"] if "cover_data" in keys else None,
                created_at,
                updated_at,
            ),
        )

    for row in item_rows:
        connection.execute(
            """
            INSERT INTO library_playlist_items (
                playlist_item_id,
                playlist_id,
                position,
                path,
                track_id,
                title,
                duration_seconds,
                duration_is_indeterminate,
                genre,
                cover_url
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(row["playlist_item_id"]),
                int(row["playlist_id"]),
                int(row["position"]),
                str(row["path"]),
                int(row["track_id"]) if row["track_id"] is not None else None,
                row["title"],
                row["duration_seconds"],
                int(row["duration_is_indeterminate"]),
                row["genre"],
                row["cover_url"],
            ),
        )


def is_http_url(value: str) -> bool:
    parsed = urlsplit(value.strip())
    return parsed.scheme.casefold() in {"http", "https"} and bool(parsed.netloc)


def migrate_album_user_state_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS album_user_state (
            album_id TEXT PRIMARY KEY,
            starred_at TEXT
        )
        """
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO album_user_state (album_id, starred_at)
        SELECT album_id, starred_at
        FROM library_albums
        WHERE starred_at IS NOT NULL
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_album_user_state_starred
            ON album_user_state (starred_at DESC, album_id)
            WHERE starred_at IS NOT NULL
        """
    )


def ensure_library_album_artists_artist_index(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type = 'index'
            AND name = 'idx_library_album_artists_artist'
        """
    ).fetchone()
    index_sql = str(row["sql"] or "") if row is not None else ""
    if row is not None and "COLLATE NOCASE" not in index_sql.upper():
        connection.execute("DROP INDEX idx_library_album_artists_artist")
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_library_album_artists_artist
            ON library_album_artists (artist COLLATE NOCASE, album_id)
        """
    )


def table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    return {
        str(row["name"])
        for row in connection.execute(f"PRAGMA table_info({table_name})")
    }


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def backfill_library_file_created_at(connection: sqlite3.Connection) -> None:
    for table_name in ("library_tracks",):
        rows = list(
            connection.execute(
                f"""
                SELECT rowid AS source_rowid, path
                FROM {table_name}
                WHERE COALESCE(file_created_at, '') = ''
                """
            )
        )
        for row in rows:
            created_at = file_created_at(Path(str(row["path"])))
            if not created_at:
                continue
            connection.execute(
                f"UPDATE {table_name} SET file_created_at = ? WHERE rowid = ?",
                (created_at, int(row["source_rowid"])),
            )


def backfill_library_album_file_created_at(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        UPDATE library_albums
        SET file_created_at = COALESCE(
            (
                SELECT MIN(NULLIF(library_tracks.file_created_at, ''))
                FROM library_tracks
                WHERE library_tracks.album_id = library_albums.album_id
            ),
            ''
        )
        WHERE COALESCE(file_created_at, '') = ''
        """
    )


def backfill_library_album_added_at(connection: sqlite3.Connection) -> None:
    fallback_added_at = utc_now_iso()
    connection.execute(
        """
        UPDATE library_albums
        SET added_at = COALESCE(NULLIF(file_created_at, ''), ?)
        WHERE COALESCE(added_at, '') = ''
        """,
        (fallback_added_at,),
    )


def backfill_library_album_artists(connection: sqlite3.Connection) -> None:
    if "artist" not in table_columns(connection, "library_albums"):
        return
    connection.execute(
        """
        INSERT OR IGNORE INTO library_album_artists (album_id, position, artist)
        SELECT albums.album_id, 0, albums.artist
        FROM library_albums AS albums
        WHERE COALESCE(albums.artist, '') != ''
            AND NOT EXISTS (
                SELECT 1
                FROM library_album_artists AS artists
                WHERE artists.album_id = albums.album_id
            )
        """
    )


def migrate_library_album_artist_column(connection: sqlite3.Connection) -> None:
    if "artist" not in table_columns(connection, "library_albums"):
        return
    connection.execute("DROP INDEX IF EXISTS idx_library_albums_artist")
    connection.execute("ALTER TABLE library_albums DROP COLUMN artist")


def migrate_album_musicbrainz_schema(connection: sqlite3.Connection) -> None:
    columns = table_columns(connection, "library_albums")
    has_release_mbid = "musicbrainz_release_mbid" in columns
    has_release_group_mbid = "musicbrainz_release_group_mbid" in columns
    if not has_release_mbid and not has_release_group_mbid:
        return

    release_select = "NULL"
    release_group_select = "NULL"
    where_clauses: list[str] = []
    if has_release_mbid:
        release_select = "NULLIF(TRIM(musicbrainz_release_mbid), '')"
        where_clauses.append("COALESCE(TRIM(musicbrainz_release_mbid), '') != ''")
    if has_release_group_mbid:
        release_group_select = "NULLIF(TRIM(musicbrainz_release_group_mbid), '')"
        where_clauses.append("COALESCE(TRIM(musicbrainz_release_group_mbid), '') != ''")
    connection.execute(
        f"""
        INSERT OR IGNORE INTO album_musicbrainz_links (
            file_album_id,
            release_mbid,
            release_group_mbid
        )
        SELECT
            album_id,
            {release_select},
            {release_group_select}
        FROM library_albums
        WHERE {" OR ".join(where_clauses)}
        """
    )
    connection.execute("DROP INDEX IF EXISTS idx_library_albums_musicbrainz_release")
    connection.execute("DROP INDEX IF EXISTS idx_library_albums_musicbrainz_release_group")
    if has_release_mbid:
        connection.execute("ALTER TABLE library_albums DROP COLUMN musicbrainz_release_mbid")
    if has_release_group_mbid:
        connection.execute("ALTER TABLE library_albums DROP COLUMN musicbrainz_release_group_mbid")


def migrate_album_musicbrainz_link_key_schema(connection: sqlite3.Connection) -> None:
    table_info = list(connection.execute("PRAGMA table_info(album_musicbrainz_links)"))
    columns = {str(row["name"]) for row in table_info}
    if "album_id" not in columns:
        ensure_album_musicbrainz_link_indexes(connection)
        return
    connection.execute("DROP INDEX IF EXISTS idx_album_musicbrainz_links_unique")
    connection.execute("DROP INDEX IF EXISTS idx_album_musicbrainz_links_release")
    connection.execute("DROP INDEX IF EXISTS idx_album_musicbrainz_links_release_group")
    connection.execute("DROP INDEX IF EXISTS idx_album_musicbrainz_links_file_album")
    connection.execute("ALTER TABLE album_musicbrainz_links RENAME TO album_musicbrainz_links_old")
    connection.execute(
        """
        CREATE TABLE album_musicbrainz_links (
            file_album_id TEXT NOT NULL,
            release_mbid TEXT,
            release_group_mbid TEXT,
            CHECK (
                COALESCE(release_mbid, '') != ''
                OR COALESCE(release_group_mbid, '') != ''
            )
        )
        """
    )
    old_columns = table_columns(connection, "album_musicbrainz_links_old")
    old_file_album_column = "file_album_id" if "file_album_id" in old_columns else "album_id"
    connection.execute(
        f"""
        INSERT OR IGNORE INTO album_musicbrainz_links (
            file_album_id,
            release_mbid,
            release_group_mbid
        )
        SELECT
            {old_file_album_column},
            NULLIF(TRIM(release_mbid), ''),
            NULLIF(TRIM(release_group_mbid), '')
        FROM album_musicbrainz_links_old
        WHERE COALESCE(TRIM(release_mbid), '') != ''
            OR COALESCE(TRIM(release_group_mbid), '') != ''
        """
    )
    connection.execute("DROP TABLE album_musicbrainz_links_old")
    ensure_album_musicbrainz_link_indexes(connection)


def ensure_album_musicbrainz_link_indexes(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_album_musicbrainz_links_unique
            ON album_musicbrainz_links (
                file_album_id,
                COALESCE(release_mbid, ''),
                COALESCE(release_group_mbid, '')
            )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_album_musicbrainz_links_file_album
            ON album_musicbrainz_links (file_album_id)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_album_musicbrainz_links_release
            ON album_musicbrainz_links (release_mbid)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_album_musicbrainz_links_release_group
            ON album_musicbrainz_links (release_group_mbid)
        """
    )


def migrate_album_search_schema(connection: sqlite3.Connection) -> None:
    columns = table_columns(connection, "library_album_search")
    if columns == ALBUM_SEARCH_COLUMNS:
        return
    connection.execute("DROP TABLE IF EXISTS library_album_search")
    connection.execute(
        """
        CREATE VIRTUAL TABLE library_album_search USING fts5(
            album_id UNINDEXED,
            artist,
            album,
            tokenize = 'unicode61'
        )
        """
    )
    connection.execute(
        "DELETE FROM app_metadata WHERE key = ?",
        (ALBUM_SEARCH_METADATA_KEY,),
    )


def migrate_itunes_lookup_cache_schema(connection: sqlite3.Connection) -> None:
    columns = table_columns(connection, "itunes_lookup_image_cache")
    if not columns or "result_kind" in columns:
        return
    connection.execute(
        """
        ALTER TABLE itunes_lookup_image_cache
        ADD COLUMN result_kind TEXT NOT NULL DEFAULT 'hit'
        """
    )


def ensure_library_track_sources(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS library_track_sources (
            track_id INTEGER PRIMARY KEY,
            source_kind TEXT NOT NULL,
            root_position INTEGER,
            canonical_path TEXT NOT NULL,
            object_key TEXT,
            etag TEXT,
            version_id TEXT,
            last_modified TEXT,
            content_type TEXT,
            size_bytes INTEGER,
            sidecar_object_key TEXT,
            sidecar_etag TEXT,
            sidecar_version_id TEXT,
            sidecar_last_modified TEXT,
            sidecar_content_type TEXT,
            sidecar_size_bytes INTEGER,
            FOREIGN KEY (track_id) REFERENCES library_tracks (track_id) ON DELETE CASCADE,
            FOREIGN KEY (root_position) REFERENCES library_roots (position) ON DELETE SET NULL
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_library_track_sources_canonical_path
            ON library_track_sources (canonical_path)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_library_track_sources_source
            ON library_track_sources (source_kind, root_position)
        """
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO library_track_sources (
            track_id,
            source_kind,
            root_position,
            canonical_path,
            size_bytes
        )
        SELECT
            track_id,
            ?,
            CASE
                WHEN EXISTS (
                    SELECT 1
                    FROM library_roots
                    WHERE library_roots.position = library_tracks.root_position
                )
                THEN root_position
                ELSE NULL
            END,
            path,
            file_size_bytes
        FROM library_tracks
        """,
        (SOURCE_KIND_LOCAL,),
    )


def backfill_library_track_roots(connection: sqlite3.Connection) -> None:
    roots = [
        (int(row["position"]), str(row["root_path"]), str(row["kind"] or SOURCE_KIND_LOCAL))
        for row in connection.execute(
            """
            SELECT position, root_path, COALESCE(kind, 'local') AS kind
            FROM library_roots
            ORDER BY position
            """
        )
    ]
    if not roots:
        return

    tracks = list(
        connection.execute(
            """
            SELECT track_id, path
            FROM library_tracks
            WHERE root_position IS NULL
            """
        )
    )
    for row in tracks:
        root_position = library_root_position_for_path(str(row["path"]), roots)
        if root_position is None:
            continue
        connection.execute(
            "UPDATE library_tracks SET root_position = ? WHERE track_id = ?",
            (root_position, int(row["track_id"])),
        )


def library_root_position_for_path(
    path: str,
    roots: Iterable[tuple[int, str] | tuple[int, str, str]],
) -> int | None:
    best_position: int | None = None
    best_root_length = -1
    for root in roots:
        position, root_path = root[0], root[1]
        kind = root[2] if len(root) > 2 else SOURCE_KIND_LOCAL
        if not path_is_in_root(path, root_path, kind):
            continue
        root_length = len(root_path)
        if root_length > best_root_length:
            best_position = position
            best_root_length = root_length
    return best_position


def path_is_in_root(
    path: str,
    root_path: str,
    kind: str = SOURCE_KIND_LOCAL,
) -> bool:
    return path_is_in_source(path, root_path, kind)


def seed_runtime_taxonomy(connection: sqlite3.Connection) -> None:
    resource = files("kukicha").joinpath("data/taxonomy.tsv")
    text = resource.read_text()
    digest = hashlib.sha256(text.encode()).hexdigest()
    current = connection.execute(
        "SELECT value FROM app_metadata WHERE key = ?",
        (TAXONOMY_METADATA_KEY,),
    ).fetchone()
    if (
        current is not None
        and str(current["value"]) == digest
        and connection.execute("SELECT 1 FROM taxonomy_genres LIMIT 1").fetchone()
        and connection.execute("SELECT 1 FROM taxonomy_styles LIMIT 1").fetchone()
    ):
        return

    rows = parse_taxonomy_tsv(text, source=str(resource))
    genres: dict[str, str] = {}
    styles: dict[str, tuple[str, str]] = {}
    aliases: dict[tuple[str, str], tuple[str, str, str]] = {}

    for row in rows:
        if row.kind == "genre":
            genres.setdefault(row.name.casefold(), row.name)
        else:
            parent = row.parent.strip()
            if not parent:
                raise ValueError(f"taxonomy style row missing parent genre: {row.name}")
            genres.setdefault(parent.casefold(), parent)
            styles.setdefault(row.name.casefold(), (row.name, parent))

        alias = row.source_term.strip() or row.name
        aliases.setdefault(
            (alias.casefold(), row.kind),
            (alias, row.kind, row.name),
        )

    connection.execute("DELETE FROM taxonomy_aliases")
    connection.execute("DELETE FROM taxonomy_styles")
    connection.execute("DELETE FROM taxonomy_genres")
    connection.executemany(
        "INSERT INTO taxonomy_genres (genre) VALUES (?)",
        [(genre,) for genre in sorted(genres.values(), key=str.casefold)],
    )
    connection.executemany(
        "INSERT INTO taxonomy_styles (style, parent_genre) VALUES (?, ?)",
        sorted(styles.values(), key=lambda item: item[0].casefold()),
    )
    connection.executemany(
        """
        INSERT INTO taxonomy_aliases (alias, canonical_kind, canonical)
        VALUES (?, ?, ?)
        """,
        sorted(aliases.values(), key=lambda item: (item[1], item[0].casefold())),
    )
    connection.execute(
        "INSERT OR REPLACE INTO app_metadata (key, value) VALUES (?, ?)",
        (TAXONOMY_METADATA_KEY, digest),
    )


def ensure_album_search_index(connection: sqlite3.Connection) -> None:
    album_count = int(
        connection.execute(
            "SELECT COUNT(*) AS count FROM library_albums"
        ).fetchone()["count"]
    )
    search_count = int(
        connection.execute(
            "SELECT COUNT(*) AS count FROM library_album_search"
        ).fetchone()["count"]
    )
    search_version = get_metadata(connection, ALBUM_SEARCH_METADATA_KEY)
    if album_count != search_count or search_version != ALBUM_SEARCH_INDEX_VERSION:
        rebuild_album_search_index(connection)


def rebuild_album_search_index(
    connection: sqlite3.Connection,
    album_ids: Iterable[str] | None = None,
) -> None:
    scoped_album_ids = None
    if album_ids is not None:
        scoped_album_ids = tuple(dict.fromkeys(album_id for album_id in album_ids if album_id))
        if not scoped_album_ids:
            return

    if scoped_album_ids is None:
        connection.execute("DELETE FROM library_album_search")
        album_scope_sql = ""
        album_scope_params: list[object] = []
    else:
        placeholders = ", ".join("?" for _album_id in scoped_album_ids)
        connection.execute(
            f"DELETE FROM library_album_search WHERE album_id IN ({placeholders})",
            scoped_album_ids,
        )
        album_scope_sql = f"WHERE albums.album_id IN ({placeholders})"
        album_scope_params = list(scoped_album_ids)

    connection.execute(
        f"""
        WITH ordered_album_artists AS (
            SELECT album_id, artist
            FROM library_album_artists
            WHERE COALESCE(artist, '') != ''
            ORDER BY album_id, position
        ),
        album_artists AS (
            SELECT
                album_id,
                group_concat(artist, ' ') AS artist
            FROM ordered_album_artists
            GROUP BY album_id
        )
        INSERT INTO library_album_search (album_id, artist, album)
        SELECT
            albums.album_id,
            COALESCE(album_artists.artist, ''),
            albums.album
        FROM library_albums AS albums
        LEFT JOIN album_artists
            ON album_artists.album_id = albums.album_id
        {album_scope_sql}
        ORDER BY albums.album_id
        """,
        album_scope_params,
    )
    set_metadata(
        connection,
        ALBUM_SEARCH_METADATA_KEY,
        ALBUM_SEARCH_INDEX_VERSION,
    )


ALBUM_ROLLUP_TABLES = (
    "library_album_roots",
    "library_album_genres",
    "library_album_styles",
    "library_album_genre_styles",
    "library_album_root_genres",
    "library_album_root_styles",
    "library_album_root_genre_styles",
)


def ensure_album_rollups(connection: sqlite3.Connection) -> None:
    album_count = library_album_count(connection)
    rollup_count = int(get_metadata(connection, ALBUM_ROLLUP_COUNT_METADATA_KEY, "-1"))
    rollup_version = get_metadata(connection, ALBUM_ROLLUP_METADATA_KEY)
    if rollup_version != ALBUM_ROLLUP_VERSION or rollup_count != album_count:
        rebuild_album_rollups(connection)


def canonicalize_library_album_artists(connection: sqlite3.Connection) -> None:
    rows = list(
        connection.execute(
            """
            SELECT album_id, position, artist
            FROM library_album_artists
            ORDER BY album_id, position
            """
        )
    )
    canonical_by_key: dict[str, str] = {}
    for row in rows:
        artist = normalized_album_artist_text(str(row["artist"]))
        if not artist:
            continue
        key = normalized_album_artist_key(artist)
        canonical = canonical_by_key.get(key)
        if canonical is None or artist < canonical:
            canonical_by_key[key] = artist

    artists_by_album: dict[str, list[str]] = {}
    seen_by_album: dict[str, set[str]] = {}
    for row in rows:
        album_id = str(row["album_id"])
        key = normalized_album_artist_key(str(row["artist"]))
        artist = canonical_by_key.get(key)
        if not artist:
            continue
        seen = seen_by_album.setdefault(album_id, set())
        if key in seen:
            continue
        seen.add(key)
        artists_by_album.setdefault(album_id, []).append(artist)

    desired_rows = tuple(
        (album_id, position, artist)
        for album_id, artists in artists_by_album.items()
        for position, artist in enumerate(artists)
    )
    current_rows = tuple(
        (str(row["album_id"]), int(row["position"]), str(row["artist"]))
        for row in rows
    )
    if current_rows == desired_rows:
        return

    connection.execute("DELETE FROM library_album_artists")
    for album_id, artists in artists_by_album.items():
        for position, artist in enumerate(artists):
            connection.execute(
                """
                INSERT INTO library_album_artists (album_id, position, artist)
                VALUES (?, ?, ?)
                """,
                (album_id, position, artist),
            )


def normalized_album_artist_text(value: str) -> str:
    return " ".join(value.strip().split())


def normalized_album_artist_key(value: str) -> str:
    return normalized_album_artist_text(value).casefold()


def rebuild_album_rollups(
    connection: sqlite3.Connection,
    album_ids: Iterable[str] | None = None,
) -> None:
    scoped_album_ids = None
    if album_ids is not None:
        scoped_album_ids = tuple(
            dict.fromkeys(album_id for album_id in album_ids if album_id)
        )
        if not scoped_album_ids:
            return

    if scoped_album_ids is None:
        for table_name in ALBUM_ROLLUP_TABLES:
            connection.execute(f"DELETE FROM {table_name}")
        album_scope_sql = ""
        album_scope_params: list[object] = []
        track_scope_sql = """
            WHERE tracks.album_id IS NOT NULL
                AND tracks.album_id != ''
        """
        track_scope_params: list[object] = []
    else:
        placeholders = ", ".join("?" for _ in scoped_album_ids)
        for table_name in ALBUM_ROLLUP_TABLES:
            connection.execute(
                f"DELETE FROM {table_name} WHERE album_id IN ({placeholders})",
                scoped_album_ids,
            )
        album_scope_sql = f"WHERE album_id IN ({placeholders})"
        album_scope_params = list(scoped_album_ids)
        track_scope_sql = f"""
            WHERE tracks.album_id IN ({placeholders})
        """
        track_scope_params = list(scoped_album_ids)

    connection.execute(
        f"""
        UPDATE library_albums
        SET track_count = COALESCE(
                (
                    SELECT COUNT(*)
                    FROM library_tracks AS tracks
                    WHERE tracks.album_id = library_albums.album_id
                ),
                0
            ),
            art_track_id = (
                SELECT MIN(tracks.track_id)
                FROM library_tracks AS tracks
                JOIN library_track_artwork AS artwork
                    ON artwork.track_id = tracks.track_id
                WHERE tracks.album_id = library_albums.album_id
                    AND artwork.height_px = {ALBUM_ARTWORK_HEIGHT}
            ),
            artist_sort_key = LOWER(TRIM(COALESCE(
                (
                    SELECT group_concat(album_artists.artist, ', ')
                    FROM library_album_artists AS album_artists
                    WHERE album_artists.album_id = library_albums.album_id
                        AND COALESCE(album_artists.artist, '') != ''
                ),
                '<unknown artist>'
            ))),
            album_sort_key = LOWER(TRIM(album))
        {album_scope_sql}
        """,
        album_scope_params,
    )
    connection.execute(
        f"""
        INSERT INTO library_album_roots (
            album_id,
            root_position,
            track_count,
            art_track_id
        )
        SELECT
            tracks.album_id,
            tracks.root_position,
            COUNT(*) AS track_count,
            MIN(artwork.track_id) AS art_track_id
        FROM library_tracks AS tracks
        LEFT JOIN (
            SELECT DISTINCT track_id
            FROM library_track_artwork
            WHERE height_px = {ALBUM_ARTWORK_HEIGHT}
        ) AS artwork
            ON artwork.track_id = tracks.track_id
        {track_scope_sql}
            AND tracks.root_position IS NOT NULL
        GROUP BY tracks.album_id, tracks.root_position
        """,
        track_scope_params,
    )
    connection.execute(
        f"""
        INSERT INTO library_album_genres (album_id, genre)
        SELECT DISTINCT tracks.album_id, genres.genre
        FROM library_tracks AS tracks
        JOIN library_track_genres AS genres
            ON genres.track_id = tracks.track_id
        {track_scope_sql}
        """,
        track_scope_params,
    )
    connection.execute(
        f"""
        INSERT INTO library_album_styles (album_id, style)
        SELECT DISTINCT tracks.album_id, styles.style
        FROM library_tracks AS tracks
        JOIN library_track_styles AS styles
            ON styles.track_id = tracks.track_id
        {track_scope_sql}
        """,
        track_scope_params,
    )
    connection.execute(
        f"""
        INSERT INTO library_album_genre_styles (album_id, genre, style)
        SELECT DISTINCT tracks.album_id, genres.genre, styles.style
        FROM library_tracks AS tracks
        JOIN library_track_genres AS genres
            ON genres.track_id = tracks.track_id
        JOIN library_track_styles AS styles
            ON styles.track_id = tracks.track_id
        {track_scope_sql}
        """,
        track_scope_params,
    )
    connection.execute(
        f"""
        INSERT INTO library_album_root_genres (album_id, root_position, genre)
        SELECT DISTINCT tracks.album_id, tracks.root_position, genres.genre
        FROM library_tracks AS tracks
        JOIN library_track_genres AS genres
            ON genres.track_id = tracks.track_id
        {track_scope_sql}
            AND tracks.root_position IS NOT NULL
        """,
        track_scope_params,
    )
    connection.execute(
        f"""
        INSERT INTO library_album_root_styles (album_id, root_position, style)
        SELECT DISTINCT tracks.album_id, tracks.root_position, styles.style
        FROM library_tracks AS tracks
        JOIN library_track_styles AS styles
            ON styles.track_id = tracks.track_id
        {track_scope_sql}
            AND tracks.root_position IS NOT NULL
        """,
        track_scope_params,
    )
    connection.execute(
        f"""
        INSERT INTO library_album_root_genre_styles (
            album_id,
            root_position,
            genre,
            style
        )
        SELECT DISTINCT
            tracks.album_id,
            tracks.root_position,
            genres.genre,
            styles.style
        FROM library_tracks AS tracks
        JOIN library_track_genres AS genres
            ON genres.track_id = tracks.track_id
        JOIN library_track_styles AS styles
            ON styles.track_id = tracks.track_id
        {track_scope_sql}
            AND tracks.root_position IS NOT NULL
        """,
        track_scope_params,
    )
    connection.execute(
        f"""
        UPDATE library_albums
        SET genre_sort_key = COALESCE(
            (
                SELECT MIN(LOWER(NULLIF(TRIM(album_genres.genre), '')))
                FROM library_album_genres AS album_genres
                WHERE album_genres.album_id = library_albums.album_id
            ),
            ''
        )
        {album_scope_sql}
        """,
        album_scope_params,
    )
    connection.execute(
        f"""
        UPDATE library_album_roots
        SET genre_sort_key = COALESCE(
            (
                SELECT MIN(LOWER(NULLIF(TRIM(root_genres.genre), '')))
                FROM library_album_root_genres AS root_genres
                WHERE root_genres.album_id = library_album_roots.album_id
                    AND root_genres.root_position = library_album_roots.root_position
            ),
            ''
        )
        {album_scope_sql}
        """,
        album_scope_params,
    )
    set_metadata(connection, ALBUM_ROLLUP_METADATA_KEY, ALBUM_ROLLUP_VERSION)
    set_metadata(
        connection,
        ALBUM_ROLLUP_COUNT_METADATA_KEY,
        str(library_album_count(connection)),
    )


def library_album_count(connection: sqlite3.Connection) -> int:
    return int(
        connection.execute(
            "SELECT COUNT(*) AS count FROM library_albums"
        ).fetchone()["count"]
    )


def ensure_root_scan_stats(connection: sqlite3.Connection) -> None:
    source_counts = root_scan_stats_source_counts(connection)
    stats_count = int(
        connection.execute(
            "SELECT COUNT(*) AS count FROM library_root_stats"
        ).fetchone()["count"]
    )
    total_stats_count = int(
        connection.execute(
            "SELECT COUNT(*) AS count FROM library_stats"
        ).fetchone()["count"]
    )
    metadata_count_keys = {
        "roots": ROOT_SCAN_STATS_ROOT_COUNT_METADATA_KEY,
        "tracks": ROOT_SCAN_STATS_TRACK_COUNT_METADATA_KEY,
        "albums": ROOT_SCAN_STATS_ALBUM_COUNT_METADATA_KEY,
        "album_roots": ROOT_SCAN_STATS_ALBUM_ROOT_COUNT_METADATA_KEY,
    }
    if (
        get_metadata(connection, ROOT_SCAN_STATS_METADATA_KEY) != ROOT_SCAN_STATS_VERSION
        or stats_count != source_counts["roots"]
        or total_stats_count != 1
        or any(
            get_metadata(connection, metadata_key, "-1") != str(source_counts[count_key])
            for count_key, metadata_key in metadata_count_keys.items()
        )
    ):
        rebuild_root_scan_stats(connection, source_counts=source_counts)


def rebuild_root_scan_stats(
    connection: sqlite3.Connection,
    *,
    source_counts: dict[str, int] | None = None,
) -> None:
    connection.execute("DELETE FROM library_album_artist_stats")
    connection.execute("DELETE FROM library_stats")
    connection.execute("DELETE FROM library_root_album_artist_stats")
    connection.execute("DELETE FROM library_root_stats")
    connection.execute(
        """
        INSERT INTO library_root_stats (
            root_position,
            tracks_scanned,
            albums_scanned
        )
        SELECT
            roots.position,
            COALESCE(track_counts.tracks_scanned, 0) AS tracks_scanned,
            COALESCE(album_counts.albums_scanned, 0) AS albums_scanned
        FROM library_roots AS roots
        LEFT JOIN (
            SELECT root_position, COUNT(*) AS tracks_scanned
            FROM library_tracks
            WHERE root_position IS NOT NULL
            GROUP BY root_position
        ) AS track_counts
            ON track_counts.root_position = roots.position
        LEFT JOIN (
            SELECT root_position, COUNT(*) AS albums_scanned
            FROM library_album_roots
            WHERE root_position IS NOT NULL
            GROUP BY root_position
        ) AS album_counts
            ON album_counts.root_position = roots.position
        ORDER BY roots.position
        """
    )
    connection.execute(
        """
        INSERT INTO library_root_album_artist_stats (
            root_position,
            album_artist,
            tracks_scanned,
            albums_scanned
        )
        SELECT
            album_roots.root_position,
            MIN(artists.artist) AS album_artist,
            SUM(album_roots.track_count) AS tracks_scanned,
            COUNT(*) AS albums_scanned
        FROM library_album_roots AS album_roots
        JOIN library_roots AS roots
            ON roots.position = album_roots.root_position
        JOIN library_album_artists AS artists
            ON artists.album_id = album_roots.album_id
        WHERE album_roots.root_position IS NOT NULL
            AND COALESCE(artists.artist, '') != ''
        GROUP BY album_roots.root_position, artists.artist COLLATE NOCASE
        ORDER BY album_roots.root_position, album_artist COLLATE NOCASE
        """
    )
    connection.execute(
        """
        INSERT INTO library_stats (
            stats_id,
            tracks_scanned,
            albums_scanned
        )
        SELECT
            1 AS stats_id,
            (
                SELECT COUNT(*)
                FROM library_tracks
            ) AS tracks_scanned,
            (
                SELECT COUNT(*)
                FROM library_albums
            ) AS albums_scanned
        """
    )
    connection.execute(
        """
        INSERT INTO library_album_artist_stats (
            album_artist,
            tracks_scanned,
            albums_scanned
        )
        SELECT
            MIN(artists.artist) AS album_artist,
            COUNT(tracks.track_id) AS tracks_scanned,
            COUNT(DISTINCT artists.album_id) AS albums_scanned
        FROM library_album_artists AS artists
        LEFT JOIN library_tracks AS tracks
            ON tracks.album_id = artists.album_id
        WHERE COALESCE(artists.artist, '') != ''
        GROUP BY artists.artist COLLATE NOCASE
        ORDER BY album_artist COLLATE NOCASE
        """
    )
    set_root_scan_stats_metadata(connection, source_counts=source_counts)


def root_scan_stats_source_counts(connection: sqlite3.Connection) -> dict[str, int]:
    return {
        "roots": int(
            connection.execute(
                "SELECT COUNT(*) AS count FROM library_roots"
            ).fetchone()["count"]
        ),
        "tracks": int(
            connection.execute(
                "SELECT COUNT(*) AS count FROM library_tracks"
            ).fetchone()["count"]
        ),
        "albums": int(
            connection.execute(
                "SELECT COUNT(*) AS count FROM library_albums"
            ).fetchone()["count"]
        ),
        "album_roots": int(
            connection.execute(
                "SELECT COUNT(*) AS count FROM library_album_roots"
            ).fetchone()["count"]
        ),
    }


def set_root_scan_stats_metadata(
    connection: sqlite3.Connection,
    *,
    source_counts: dict[str, int] | None = None,
) -> None:
    if source_counts is None:
        source_counts = root_scan_stats_source_counts(connection)
    set_metadata(connection, ROOT_SCAN_STATS_METADATA_KEY, ROOT_SCAN_STATS_VERSION)
    set_metadata(
        connection,
        ROOT_SCAN_STATS_ROOT_COUNT_METADATA_KEY,
        str(source_counts["roots"]),
    )
    set_metadata(
        connection,
        ROOT_SCAN_STATS_TRACK_COUNT_METADATA_KEY,
        str(source_counts["tracks"]),
    )
    set_metadata(
        connection,
        ROOT_SCAN_STATS_ALBUM_COUNT_METADATA_KEY,
        str(source_counts["albums"]),
    )
    set_metadata(
        connection,
        ROOT_SCAN_STATS_ALBUM_ROOT_COUNT_METADATA_KEY,
        str(source_counts["album_roots"]),
    )


def set_metadata(connection: sqlite3.Connection, key: str, value: str) -> None:
    connection.execute(
        "INSERT OR REPLACE INTO app_metadata (key, value) VALUES (?, ?)",
        (key, value),
    )


def get_metadata(connection: sqlite3.Connection, key: str, default: str = "") -> str:
    row = connection.execute("SELECT value FROM app_metadata WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row else default


def clear_library(connection: sqlite3.Connection) -> None:
    connection.execute("DELETE FROM library_album_artist_stats")
    connection.execute("DELETE FROM library_stats")
    connection.execute("DELETE FROM library_root_album_artist_stats")
    connection.execute("DELETE FROM library_root_stats")
    for table_name in ALBUM_ROLLUP_TABLES:
        connection.execute(f"DELETE FROM {table_name}")
    connection.execute("DELETE FROM library_album_artists")
    connection.execute("DELETE FROM library_track_artwork")
    connection.execute("DELETE FROM library_track_styles")
    connection.execute("DELETE FROM library_track_genres")
    connection.execute("DELETE FROM library_track_sources")
    connection.execute("DELETE FROM library_tracks")
    connection.execute("DELETE FROM library_albums")
    connection.execute("DELETE FROM library_album_search")
    connection.execute("DELETE FROM library_roots")
