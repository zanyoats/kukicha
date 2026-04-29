from __future__ import annotations

import hashlib
import sqlite3
from importlib.resources import files
from pathlib import Path
from types import TracebackType
from typing import Iterable

from ..file_metadata import file_created_at
from ..taxonomy_data import parse_taxonomy_tsv

TAXONOMY_METADATA_KEY = "taxonomy_tsv_sha256"
UNKNOWN_GENRE_TAG = "__Unknown"
ALBUM_SEARCH_METADATA_KEY = "album_search_index_version"
ALBUM_SEARCH_INDEX_VERSION = "2"
ALBUM_SEARCH_TRACK_SEPARATOR = " kukichatrackboundarytoken "

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
    root_path TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS player_actions (
    action_id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT NOT NULL,
    context_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_player_actions_created_at
    ON player_actions (created_at DESC, action_id DESC);

CREATE TABLE IF NOT EXISTS library_albums (
    album_id TEXT PRIMARY KEY,
    artist TEXT NOT NULL,
    album TEXT NOT NULL,
    year INTEGER,
    track_count INTEGER NOT NULL,
    file_created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_library_albums_artist ON library_albums (artist);
CREATE INDEX IF NOT EXISTS idx_library_albums_album ON library_albums (album);

CREATE TABLE IF NOT EXISTS album_musicbrainz_links (
    album_id TEXT PRIMARY KEY,
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

CREATE TABLE IF NOT EXISTS library_playlists (
    playlist_id INTEGER PRIMARY KEY AUTOINCREMENT,
    root_position INTEGER,
    path TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    cover_svg TEXT NOT NULL DEFAULT '',
    file_created_at TEXT,
    FOREIGN KEY (root_position) REFERENCES library_roots (position) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_library_playlists_root_position
    ON library_playlists (root_position);
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

CREATE TABLE IF NOT EXISTS library_tracks (
    track_id INTEGER PRIMARY KEY AUTOINCREMENT,
    album_id TEXT,
    root_position INTEGER,
    path TEXT NOT NULL UNIQUE,
    file_created_at TEXT,
    file_type TEXT,
    scan_error TEXT,
    artist TEXT,
    album_artist TEXT,
    composer TEXT,
    album TEXT,
    title TEXT,
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
    title,
    composer,
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
        migrate_library_schema(connection)
        migrate_album_musicbrainz_schema(connection)
        migrate_album_search_schema(connection)
        migrate_itunes_lookup_cache_schema(connection)
        ensure_album_search_index(connection)
        seed_runtime_taxonomy(connection)
    except Exception:
        connection.close()
        raise
    return connection


def migrate_library_schema(connection: sqlite3.Connection) -> None:
    columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(library_tracks)")
    }
    created_track_file_created_at = False
    if "root_position" not in columns:
        connection.execute("ALTER TABLE library_tracks ADD COLUMN root_position INTEGER")
        backfill_library_track_roots(connection)
    if "composer" not in columns:
        connection.execute("ALTER TABLE library_tracks ADD COLUMN composer TEXT")
    if "file_created_at" not in columns:
        connection.execute("ALTER TABLE library_tracks ADD COLUMN file_created_at TEXT")
        created_track_file_created_at = True

    album_columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(library_albums)")
    }
    created_album_file_created_at = False
    if album_columns and "file_created_at" not in album_columns:
        connection.execute("ALTER TABLE library_albums ADD COLUMN file_created_at TEXT")
        created_album_file_created_at = True

    playlist_columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(library_playlists)")
    }
    created_playlist_file_created_at = False
    if playlist_columns and "cover_svg" not in playlist_columns:
        connection.execute(
            "ALTER TABLE library_playlists ADD COLUMN cover_svg TEXT NOT NULL DEFAULT ''"
        )
    if playlist_columns and "file_created_at" not in playlist_columns:
        connection.execute("ALTER TABLE library_playlists ADD COLUMN file_created_at TEXT")
        created_playlist_file_created_at = True

    if created_track_file_created_at or created_playlist_file_created_at:
        backfill_library_file_created_at(connection)
    if created_album_file_created_at or created_track_file_created_at:
        backfill_library_album_file_created_at(connection)

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
        CREATE INDEX IF NOT EXISTS idx_library_playlists_file_created_at
            ON library_playlists (file_created_at)
        """
    )
    connection.execute("DROP TABLE IF EXISTS library_album_paths")


def backfill_library_file_created_at(connection: sqlite3.Connection) -> None:
    for table_name in ("library_tracks", "library_playlists"):
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
        SET file_created_at = (
            SELECT MIN(NULLIF(library_tracks.file_created_at, ''))
            FROM library_tracks
            WHERE library_tracks.album_id = library_albums.album_id
        )
        WHERE COALESCE(file_created_at, '') = ''
        """
    )


def migrate_album_musicbrainz_schema(connection: sqlite3.Connection) -> None:
    columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(library_albums)")
    }
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
        INSERT INTO album_musicbrainz_links (
            album_id,
            release_mbid,
            release_group_mbid
        )
        SELECT
            album_id,
            {release_select},
            {release_group_select}
        FROM library_albums
        WHERE {" OR ".join(where_clauses)}
        ON CONFLICT(album_id) DO UPDATE SET
            release_mbid = CASE
                WHEN COALESCE(album_musicbrainz_links.release_mbid, '') = ''
                THEN excluded.release_mbid
                ELSE album_musicbrainz_links.release_mbid
            END,
            release_group_mbid = CASE
                WHEN COALESCE(album_musicbrainz_links.release_group_mbid, '') = ''
                THEN excluded.release_group_mbid
                ELSE album_musicbrainz_links.release_group_mbid
            END
        """
    )
    connection.execute("DROP INDEX IF EXISTS idx_library_albums_musicbrainz_release")
    connection.execute("DROP INDEX IF EXISTS idx_library_albums_musicbrainz_release_group")
    if has_release_mbid:
        connection.execute("ALTER TABLE library_albums DROP COLUMN musicbrainz_release_mbid")
    if has_release_group_mbid:
        connection.execute("ALTER TABLE library_albums DROP COLUMN musicbrainz_release_group_mbid")


def migrate_album_search_schema(connection: sqlite3.Connection) -> None:
    columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(library_album_search)")
    }
    if "composer" in columns:
        return
    connection.execute("DROP TABLE IF EXISTS library_album_search")
    connection.execute(
        """
        CREATE VIRTUAL TABLE library_album_search USING fts5(
            album_id UNINDEXED,
            artist,
            album,
            title,
            composer,
            tokenize = 'unicode61'
        )
        """
    )
    connection.execute(
        "DELETE FROM app_metadata WHERE key = ?",
        (ALBUM_SEARCH_METADATA_KEY,),
    )


def migrate_itunes_lookup_cache_schema(connection: sqlite3.Connection) -> None:
    columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(itunes_lookup_image_cache)")
    }
    if not columns or "result_kind" in columns:
        return
    connection.execute(
        """
        ALTER TABLE itunes_lookup_image_cache
        ADD COLUMN result_kind TEXT NOT NULL DEFAULT 'hit'
        """
    )


def backfill_library_track_roots(connection: sqlite3.Connection) -> None:
    roots = [
        (int(row["position"]), str(row["root_path"]))
        for row in connection.execute(
            "SELECT position, root_path FROM library_roots ORDER BY position"
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
    roots: Iterable[tuple[int, str]],
) -> int | None:
    best_position: int | None = None
    best_root_length = -1
    for position, root_path in roots:
        if not path_is_in_root(path, root_path):
            continue
        root_length = len(root_path)
        if root_length > best_root_length:
            best_position = position
            best_root_length = root_length
    return best_position


def path_is_in_root(path: str, root_path: str) -> bool:
    try:
        return Path(path).is_relative_to(Path(root_path))
    except ValueError:
        return False


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


def rebuild_album_search_index(connection: sqlite3.Connection) -> None:
    connection.execute("DELETE FROM library_album_search")
    connection.execute(
        """
        WITH ordered_track_titles AS (
            SELECT album_id, title
            FROM library_tracks
            WHERE album_id IS NOT NULL
                AND album_id != ''
                AND COALESCE(title, '') != ''
            ORDER BY album_id, track_id
        ),
        track_titles AS (
            SELECT
                album_id,
                group_concat(title, ?) AS title
            FROM ordered_track_titles
            GROUP BY album_id
        ),
        ordered_track_composers AS (
            SELECT DISTINCT album_id, composer
            FROM library_tracks
            WHERE album_id IS NOT NULL
                AND album_id != ''
                AND COALESCE(composer, '') != ''
            ORDER BY album_id, composer COLLATE NOCASE
        ),
        track_composers AS (
            SELECT
                album_id,
                group_concat(composer, ?) AS composer
            FROM ordered_track_composers
            GROUP BY album_id
        )
        INSERT INTO library_album_search (album_id, artist, album, title, composer)
        SELECT
            albums.album_id,
            albums.artist,
            albums.album,
            COALESCE(track_titles.title, ''),
            COALESCE(track_composers.composer, '')
        FROM library_albums AS albums
        LEFT JOIN track_titles
            ON track_titles.album_id = albums.album_id
        LEFT JOIN track_composers
            ON track_composers.album_id = albums.album_id
        ORDER BY albums.album_id
        """,
        (ALBUM_SEARCH_TRACK_SEPARATOR, ALBUM_SEARCH_TRACK_SEPARATOR),
    )
    set_metadata(
        connection,
        ALBUM_SEARCH_METADATA_KEY,
        ALBUM_SEARCH_INDEX_VERSION,
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
    connection.execute("DELETE FROM library_playlist_items")
    connection.execute("DELETE FROM library_playlists")
    connection.execute("DELETE FROM library_track_artwork")
    connection.execute("DELETE FROM library_track_styles")
    connection.execute("DELETE FROM library_track_genres")
    connection.execute("DELETE FROM library_tracks")
    connection.execute("DELETE FROM library_albums")
    connection.execute("DELETE FROM library_album_search")
    connection.execute("DELETE FROM library_roots")
