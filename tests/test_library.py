from __future__ import annotations

from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
import urllib.error
from unittest.mock import patch

from kukicha.use_case import AlbumListQuery, LibraryQueries
from kukicha.use_case import connect_database
from kukicha.use_case import (
    ALBUM_LIST_SORT_ALBUMS,
    ALBUM_LIST_SORT_ARTIST,
    ALBUM_LIST_SORT_FREQUENT,
    ALBUM_LIST_SORT_GENRE,
    ALBUM_LIST_SORT_RECENT,
    ALBUM_LIST_SORT_RECENTLY_ADDED,
    ALBUM_LIST_SORT_STARRED,
    GenreStyleFilter,
)
from kukicha.use_case import (
    ItunesLookupCandidate,
    ItunesLookupClient,
    ItunesLookupStats,
    get_itunes_lookup_image,
)
from kukicha.use_case import (
    resolve_library_cover_art,
    resolve_library_genres,
    save_library,
    sync_library_roots,
    UNKNOWN_GENRE_TAG,
)
from kukicha.use_case.library import load_rescan_tracks_by_path
from kukicha.use_case.library import save_library_with_options
from kukicha.use_case.library import save_rescanned_library_incremental
from kukicha.use_case.database import clear_library
from kukicha.library_sources import RemoteRootConfig, canonical_s3_path, remote_root_source
from kukicha.use_case.coverartarchive import (
    CoverArtArchiveClient,
    CoverArtArchiveStats,
    front_image_url,
    get_cover_art_archive_entity,
)
from kukicha.models import (
    MusicLibrary,
    PlaylistItemRecord,
    PlaylistRecord,
    TrackArtwork,
    TrackRecord,
    TrackSourceRecord,
)
from kukicha.use_case.queries.library import (
    album_order_by_clause,
    album_sort_columns,
    album_sort_select_sql,
)
from kukicha.use_case.queries.musicbrainz import album_musicbrainz_link
from kukicha.use_case.musicbrainz import store_musicbrainz_entity
from kukicha.use_case.queries.filters import album_where_clause


class LibraryAlbumPathQueryTest(unittest.TestCase):
    def test_save_library_persists_remote_root_and_track_source_metadata(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            remote = RemoteRootConfig(
                name="Remote",
                endpoint_url="https://s3.example.test",
                bucket="bucket",
                prefix="tracks/",
            )
            track_path = canonical_s3_path(remote, "tracks/Album/01.flac")
            save_library_with_options(
                MusicLibrary(
                    roots=[remote.root_path],
                    tracks=[
                        TrackRecord(
                            path=track_path,
                            root_position=0,
                            file_type="flac",
                            artist="Artist",
                            album_artist="Artist",
                            album="Album",
                            title="Track",
                            file_size_bytes=12,
                            source=TrackSourceRecord(
                                source_kind="s3",
                                root_position=0,
                                canonical_path=track_path,
                                object_key="tracks/Album/01.flac",
                                etag='"etag"',
                                last_modified="2026-05-16T12:00:00+00:00",
                                content_type="audio/flac",
                                size_bytes=12,
                            ),
                        )
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-05-16T12:00:00+00:00",
                ),
                database,
                root_rows=[remote_root_source(0, remote)],
            )

            with connect_database(database, create=False) as connection:
                root = connection.execute(
                    """
                    SELECT root_path, kind, source_json
                    FROM library_roots
                    WHERE position = 0
                    """
                ).fetchone()
                source = connection.execute(
                    """
                    SELECT
                        source_kind,
                        root_position,
                        canonical_path,
                        object_key,
                        etag,
                        content_type,
                        size_bytes
                    FROM library_track_sources
                    """
                ).fetchone()
                columns = {
                    str(row["name"])
                    for row in connection.execute("PRAGMA table_info(library_tracks)")
                }

        self.assertEqual(str(root["root_path"]), remote.root_path)
        self.assertEqual(str(root["kind"]), "s3")
        self.assertIn("s3.example.test", str(root["source_json"]))
        self.assertEqual(str(source["source_kind"]), "s3")
        self.assertEqual(int(source["root_position"]), 0)
        self.assertEqual(str(source["canonical_path"]), track_path)
        self.assertEqual(str(source["object_key"]), "tracks/Album/01.flac")
        self.assertEqual(str(source["etag"]), '"etag"')
        self.assertEqual(str(source["content_type"]), "audio/flac")
        self.assertEqual(int(source["size_bytes"]), 12)
        self.assertNotIn("size_bytes", columns)

    def test_connect_database_migrates_legacy_listening_source_columns(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            legacy_connection = sqlite3.connect(database)
            try:
                legacy_connection.executescript(
                    """
                    CREATE TABLE play_events (
                        play_event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        played_at TEXT NOT NULL,
                        playback_id INTEGER,
                        track_key TEXT,
                        album_id TEXT,
                        playlist_key TEXT,
                        snapshot_json TEXT NOT NULL DEFAULT '{}'
                    );
                    INSERT INTO play_events (
                        played_at,
                        playback_id,
                        track_key,
                        album_id,
                        playlist_key,
                        snapshot_json
                    ) VALUES (
                        '2026-05-11T12:00:00+00:00',
                        1,
                        'track-key',
                        'album-key',
                        '',
                        '{}'
                    );
                    CREATE TABLE play_now_playing (
                        session_key TEXT PRIMARY KEY,
                        updated_at TEXT NOT NULL,
                        playback_id INTEGER,
                        track_key TEXT,
                        album_id TEXT,
                        playlist_key TEXT,
                        snapshot_json TEXT NOT NULL DEFAULT '{}'
                    );
                    INSERT INTO play_now_playing (
                        session_key,
                        updated_at,
                        playback_id,
                        track_key,
                        album_id,
                        playlist_key,
                        snapshot_json
                    ) VALUES (
                        'default',
                        '2026-05-11T12:00:00+00:00',
                        1,
                        'track-key',
                        'album-key',
                        '',
                        '{}'
                    );
                    """
                )
                legacy_connection.commit()
            finally:
                legacy_connection.close()

            with connect_database(database) as connection:
                play_event_columns = {
                    str(row["name"])
                    for row in connection.execute("PRAGMA table_info(play_events)")
                }
                now_playing_columns = {
                    str(row["name"])
                    for row in connection.execute("PRAGMA table_info(play_now_playing)")
                }
                play_event_source = connection.execute(
                    "SELECT source FROM play_events"
                ).fetchone()["source"]
                now_playing_source = connection.execute(
                    "SELECT source FROM play_now_playing"
                ).fetchone()["source"]

        self.assertIn("source", play_event_columns)
        self.assertIn("source", now_playing_columns)
        self.assertEqual(play_event_source, "")
        self.assertEqual(now_playing_source, "")

    def test_migrates_album_artist_index_to_nocase(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            legacy_connection = sqlite3.connect(database)
            try:
                legacy_connection.executescript(
                    """
                    CREATE TABLE library_album_artists (
                        album_id TEXT NOT NULL,
                        position INTEGER NOT NULL,
                        artist TEXT NOT NULL,
                        PRIMARY KEY (album_id, position)
                    );
                    CREATE INDEX idx_library_album_artists_artist
                        ON library_album_artists (artist, album_id);
                    """
                )
                legacy_connection.commit()
            finally:
                legacy_connection.close()

            with connect_database(database) as connection:
                columns = list(
                    connection.execute(
                        "PRAGMA index_xinfo(idx_library_album_artists_artist)"
                    )
                )

        artist_column = next(row for row in columns if row["name"] == "artist")
        self.assertEqual(artist_column["coll"], "NOCASE")

    def test_artist_filter_plan_uses_artist_index_then_album_primary_key(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            with connect_database(database) as connection:
                for index in range(1000):
                    album_id = f"album-{index}"
                    artist = "Terekke" if index in {200, 400} else "Other"
                    connection.execute(
                        """
                        INSERT INTO library_albums (
                            album_id,
                            album,
                            year,
                            track_count,
                            file_created_at,
                            artist_sort_key,
                            album_sort_key
                        ) VALUES (?, ?, ?, 1, ?, ?, ?)
                        """,
                        (
                            album_id,
                            f"Album {index}",
                            2026,
                            f"2026-01-{(index % 28) + 1:02d}",
                            artist.casefold(),
                            f"album {index}",
                        ),
                    )
                    connection.execute(
                        """
                        INSERT INTO library_album_artists (album_id, position, artist)
                        VALUES (?, 0, ?)
                        """,
                        (album_id, artist),
                    )

                query = AlbumListQuery(artists=("Terekke",))
                where_sql, params = album_where_clause(query)
                sort_columns = album_sort_columns(query)
                plan_rows = list(
                    connection.execute(
                        f"""
                        EXPLAIN QUERY PLAN
                        SELECT
                            albums.album_id,
                            albums.album,
                            albums.year,
                            albums.track_count,
                            albums.file_created_at,
                            albums.art_track_id
                            {album_sort_select_sql(sort_columns)}
                        FROM library_albums AS albums
                        {where_sql}
                        {album_order_by_clause(sort_columns)}
                        LIMIT ?
                        """,
                        [*params, 201],
                    )
                )

        details = "\n".join(str(row["detail"]) for row in plan_rows)
        self.assertIn("idx_library_album_artists_artist", details)
        self.assertIn("SEARCH albums", details)
        self.assertIn("album_id=?", details)
        self.assertNotIn("SCAN albums", details)

    def test_unfiltered_album_sort_plans_use_sort_indexes(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            with connect_database(database) as connection:
                for index in range(100):
                    connection.execute(
                        """
                        INSERT INTO library_albums (
                            album_id,
                            album,
                            year,
                            track_count,
                            file_created_at,
                            artist_sort_key,
                            album_sort_key,
                            genre_sort_key
                        ) VALUES (?, ?, ?, 1, ?, ?, ?, ?)
                        """,
                        (
                            f"album-{index}",
                            f"Album {index}",
                            None if index % 10 == 0 else 2000 + index % 20,
                            (
                                ""
                                if index % 7 == 0
                                else f"2026-01-{(index % 28) + 1:02d}T12:00:00+00:00"
                            ),
                            f"artist {index % 5}",
                            f"album {index}",
                            "" if index % 6 == 0 else f"genre {index % 3}",
                        ),
                    )

                expected_indexes = {
                    ALBUM_LIST_SORT_RECENTLY_ADDED: "idx_library_albums_recently_added_sort",
                    ALBUM_LIST_SORT_ARTIST: "idx_library_albums_artist_sort",
                    ALBUM_LIST_SORT_ALBUMS: "idx_library_albums_album_sort",
                    ALBUM_LIST_SORT_GENRE: "idx_library_albums_genre_sort",
                    ALBUM_LIST_SORT_STARRED: "idx_library_albums_starred_sort",
                }
                details_by_sort: dict[str, str] = {}
                for sort in expected_indexes:
                    query = AlbumListQuery(sort=sort)
                    where_sql, params = album_where_clause(query)
                    sort_columns = album_sort_columns(query)
                    plan_rows = list(
                        connection.execute(
                            f"""
                            EXPLAIN QUERY PLAN
                            SELECT
                                albums.album_id,
                                albums.album,
                                albums.year,
                                albums.track_count,
                                albums.file_created_at,
                                albums.art_track_id
                                {album_sort_select_sql(sort_columns)}
                            FROM library_albums AS albums
                            {where_sql}
                            {album_order_by_clause(sort_columns)}
                            LIMIT ?
                            """,
                            [*params, 201],
                        )
                    )
                    details_by_sort[sort] = "\n".join(
                        str(row["detail"]) for row in plan_rows
                    )

        for sort, index_name in expected_indexes.items():
            with self.subTest(sort=sort):
                self.assertIn(index_name, details_by_sort[sort])
                self.assertNotIn("USE TEMP B-TREE FOR ORDER BY", details_by_sort[sort])

    def test_album_sort_columns_match_sort_index_expressions(self) -> None:
        sort_expressions = {
            ALBUM_LIST_SORT_RECENTLY_ADDED: [
                "CASE WHEN NULLIF(albums.added_at, '') IS NULL THEN 1 ELSE 0 END",
                "albums.added_at",
                "albums.artist_sort_key",
                "CASE WHEN albums.year IS NULL THEN 1 ELSE 0 END",
                "albums.year",
                "albums.album_sort_key",
                "albums.album_id",
            ],
            ALBUM_LIST_SORT_ARTIST: [
                "albums.artist_sort_key",
                "CASE WHEN albums.year IS NULL THEN 1 ELSE 0 END",
                "albums.year",
                "albums.album_sort_key",
                "albums.album_id",
            ],
            ALBUM_LIST_SORT_ALBUMS: [
                "albums.album_sort_key",
                "albums.artist_sort_key",
                "CASE WHEN albums.year IS NULL THEN 1 ELSE 0 END",
                "albums.year",
                "albums.album_id",
            ],
            ALBUM_LIST_SORT_RECENT: [
                "recent_album_stats.last_played_at",
                "recent_album_stats.play_count",
                "albums.album_id",
            ],
            ALBUM_LIST_SORT_FREQUENT: [
                "frequent_album_stats.play_count",
                "frequent_album_stats.last_played_at",
                "albums.album_id",
            ],
            ALBUM_LIST_SORT_GENRE: [
                "CASE WHEN NULLIF(albums.genre_sort_key, '') IS NULL THEN 1 ELSE 0 END",
                "albums.genre_sort_key",
                "albums.artist_sort_key",
                "CASE WHEN albums.year IS NULL THEN 1 ELSE 0 END",
                "albums.year",
                "albums.album_sort_key",
                "albums.album_id",
            ],
        }

        for sort, expected in sort_expressions.items():
            with self.subTest(sort=sort):
                columns = album_sort_columns(AlbumListQuery(sort=sort))
                self.assertEqual([column.expression for column in columns], expected)

    def test_migrates_null_album_file_created_at_to_empty_text(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            legacy_connection = sqlite3.connect(database)
            try:
                legacy_connection.execute(
                    """
                    CREATE TABLE library_albums (
                        album_id TEXT PRIMARY KEY,
                        album TEXT NOT NULL,
                        year INTEGER,
                        track_count INTEGER NOT NULL,
                        file_created_at TEXT
                    )
                    """
                )
                legacy_connection.execute(
                    """
                    INSERT INTO library_albums (
                        album_id,
                        album,
                        year,
                        track_count,
                        file_created_at
                    ) VALUES ('artist::album', 'Album', 2026, 1, NULL)
                    """
                )
                legacy_connection.commit()
            finally:
                legacy_connection.close()

            with connect_database(database) as connection:
                row = connection.execute(
                    """
                    SELECT file_created_at
                    FROM library_albums
                    WHERE album_id = 'artist::album'
                    """
                ).fetchone()

        self.assertIsNotNone(row)
        self.assertEqual(str(row["file_created_at"]), "")

    def test_migrates_album_added_at_from_file_created_at(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            legacy_connection = sqlite3.connect(database)
            try:
                legacy_connection.execute(
                    """
                    CREATE TABLE library_albums (
                        album_id TEXT PRIMARY KEY,
                        album TEXT NOT NULL,
                        year INTEGER,
                        track_count INTEGER NOT NULL,
                        file_created_at TEXT NOT NULL DEFAULT ''
                    )
                    """
                )
                legacy_connection.execute(
                    """
                    INSERT INTO library_albums (
                        album_id,
                        album,
                        year,
                        track_count,
                        file_created_at
                    ) VALUES ('artist::album', 'Album', 2026, 1, '2026-04-22T12:00:00+00:00')
                    """
                )
                legacy_connection.commit()
            finally:
                legacy_connection.close()

            with (
                patch(
                    "kukicha.use_case.database.utc_now_iso",
                    return_value="2026-05-15T12:00:00+00:00",
                ),
                connect_database(database) as connection,
            ):
                row = connection.execute(
                    """
                    SELECT added_at
                    FROM library_albums
                    WHERE album_id = 'artist::album'
                    """
                ).fetchone()

        self.assertIsNotNone(row)
        self.assertEqual(str(row["added_at"]), "2026-04-22T12:00:00+00:00")

    def test_migrates_album_added_at_to_migration_time_when_file_created_at_is_blank(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            legacy_connection = sqlite3.connect(database)
            try:
                legacy_connection.execute(
                    """
                    CREATE TABLE library_albums (
                        album_id TEXT PRIMARY KEY,
                        album TEXT NOT NULL,
                        year INTEGER,
                        track_count INTEGER NOT NULL,
                        file_created_at TEXT NOT NULL DEFAULT ''
                    )
                    """
                )
                legacy_connection.execute(
                    """
                    INSERT INTO library_albums (
                        album_id,
                        album,
                        year,
                        track_count,
                        file_created_at
                    ) VALUES ('artist::album', 'Album', 2026, 1, '')
                    """
                )
                legacy_connection.commit()
            finally:
                legacy_connection.close()

            with (
                patch(
                    "kukicha.use_case.database.utc_now_iso",
                    return_value="2026-05-15T12:00:00+00:00",
                ),
                connect_database(database) as connection,
            ):
                row = connection.execute(
                    """
                    SELECT added_at
                    FROM library_albums
                    WHERE album_id = 'artist::album'
                    """
                ).fetchone()

        self.assertIsNotNone(row)
        self.assertEqual(str(row["added_at"]), "2026-05-15T12:00:00+00:00")

    def test_migrates_existing_album_stars_to_album_user_state(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            legacy_connection = sqlite3.connect(database)
            try:
                legacy_connection.execute(
                    """
                    CREATE TABLE library_albums (
                        album_id TEXT PRIMARY KEY,
                        album TEXT NOT NULL,
                        year INTEGER,
                        track_count INTEGER NOT NULL,
                        starred_at TEXT
                    )
                    """
                )
                legacy_connection.execute(
                    """
                    INSERT INTO library_albums (
                        album_id,
                        album,
                        year,
                        track_count,
                        starred_at
                    ) VALUES (
                        'artist::album',
                        'Album',
                        2026,
                        1,
                        '2026-05-01T12:00:00+00:00'
                    )
                    """
                )
                legacy_connection.commit()
            finally:
                legacy_connection.close()

            with connect_database(database) as connection:
                row = connection.execute(
                    """
                    SELECT starred_at
                    FROM album_user_state
                    WHERE album_id = 'artist::album'
                    """
                ).fetchone()

        self.assertIsNotNone(row)
        self.assertEqual(str(row["starred_at"]), "2026-05-01T12:00:00+00:00")

    def test_album_details_paths_come_from_tracks_in_case_insensitive_path_order(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            save_library(
                MusicLibrary(
                    roots=["/music"],
                    tracks=[
                        TrackRecord(
                            path="/music/Artist/Album/B.flac",
                            root_position=0,
                            file_type="flac",
                            artist="Artist",
                            album_artist="Artist",
                            album="Album",
                            title="Second",
                        ),
                        TrackRecord(
                            path="/music/artist/album/a.flac",
                            root_position=0,
                            file_type="flac",
                            artist="Artist",
                            album_artist="Artist",
                            album="Album",
                            title="First",
                        ),
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-04-22T00:00:00+00:00",
                ),
                database,
            )

            album = LibraryQueries(database).get_album("artist::album")

        self.assertEqual(
            album.paths,
            (
                "/music/artist/album/a.flac",
                "/music/Artist/Album/B.flac",
            ),
        )

    def test_album_details_sort_alphanumeric_track_numbers_naturally(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            track_numbers = ("B3", "A1", "B1", "B2", "A2", "A3")
            save_library(
                MusicLibrary(
                    roots=["/music"],
                    tracks=[
                        TrackRecord(
                            path=f"/music/Artist/Album/{track_number}.flac",
                            root_position=0,
                            file_type="flac",
                            artist="Artist",
                            album_artist="Artist",
                            album="Album",
                            title=track_number,
                            track_number=track_number,
                        )
                        for track_number in track_numbers
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-04-22T00:00:00+00:00",
                ),
                database,
            )

            album = LibraryQueries(database).get_album("artist::album")

        self.assertEqual(
            [track.track_number for track in album.tracks],
            ["A1", "A2", "A3", "B1", "B2", "B3"],
        )

    def test_album_details_paths_respect_root_filter(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            root_a_track = TrackRecord(
                path="/music/a/Artist/Album/01.flac",
                root_position=0,
                file_type="flac",
                artist="Artist",
                album_artist="Artist",
                album="Album",
                title="Root A",
                work="Root A Work",
                is_compilation=True,
                genres=["Electronic"],
                styles=["Ambient"],
                album_artwork=TrackArtwork(mime_type="image/png", data=b"cover"),
            )
            root_b_track = TrackRecord(
                path="/music/b/Artist/Album/01.flac",
                root_position=1,
                file_type="flac",
                artist="Artist",
                album_artist="Artist",
                album="Album",
                title="Root B",
                genres=["Jazz"],
                styles=["Modal"],
            )
            save_library(
                MusicLibrary(
                    roots=["/music/a", "/music/b"],
                    tracks=[root_a_track, root_b_track],
                    supported_extensions=[".flac"],
                    generated_at="2026-04-22T00:00:00+00:00",
                ),
                database,
            )

            album = LibraryQueries(database).get_album(
                "artist::album",
                root_positions=(1,),
            )

        self.assertEqual(album.paths, ("/music/b/Artist/Album/01.flac",))
        self.assertEqual(album.track_count, 1)
        self.assertEqual(album.genres, ("Jazz",))
        self.assertEqual(album.styles, ("Modal",))
        self.assertFalse(album.has_cover)
        self.assertFalse(album.is_compilation)
        self.assertFalse(album.is_work)
        self.assertIsNone(album.art_track_id)
        self.assertEqual(album.track_ids, (root_b_track.track_id,))
        self.assertEqual([track.path for track in album.tracks], [root_b_track.path])
        self.assertEqual([track.has_cover for track in album.tracks], [False])

    def test_album_page_uses_root_scoped_rollups(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            root_a_track = TrackRecord(
                path="/music/a/Artist/Album/01.flac",
                root_position=0,
                file_type="flac",
                artist="Artist",
                album_artist="Artist",
                album="Album",
                title="Root A",
                work="Root A Work",
                genres=["Electronic"],
                styles=["Ambient"],
                album_artwork=TrackArtwork(mime_type="image/png", data=b"cover"),
            )
            root_b_track = TrackRecord(
                path="/music/b/Artist/Album/01.flac",
                root_position=1,
                file_type="flac",
                artist="Artist",
                album_artist="Artist",
                album="Album",
                title="Root B",
                genres=["Jazz"],
                styles=["Modal"],
            )
            save_library(
                MusicLibrary(
                    roots=["/music/a", "/music/b"],
                    tracks=[root_a_track, root_b_track],
                    supported_extensions=[".flac"],
                    generated_at="2026-04-22T00:00:00+00:00",
                ),
                database,
            )

            api = LibraryQueries(database)
            root_b_page = api.list_album_page(
                AlbumListQuery(root_positions=(1,))
            )
            root_b_genre_page = api.list_album_page(
                AlbumListQuery(root_positions=(1,), genres=("Jazz",))
            )

        self.assertEqual([album.album for album in root_b_page.items], ["Album"])
        self.assertEqual(root_b_page.items[0].track_count, 1)
        self.assertIsNone(root_b_page.items[0].art_track_id)
        self.assertFalse(hasattr(root_b_page.items[0], "track_ids"))
        self.assertEqual([album.album for album in root_b_genre_page.items], ["Album"])

    def test_list_genres_counts_album_genres_and_styles(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            tracks = [
                TrackRecord(
                    path="/music/Artist/Alpha/01.flac",
                    root_position=0,
                    file_type="flac",
                    artist="Artist",
                    album_artist="Artist",
                    album="Alpha",
                    title="One",
                    genres=["Electronic"],
                    styles=["Electronica"],
                ),
                TrackRecord(
                    path="/music/Artist/Alpha/02.flac",
                    root_position=0,
                    file_type="flac",
                    artist="Artist",
                    album_artist="Artist",
                    album="Alpha",
                    title="Two",
                    genres=["Electronic"],
                    styles=["Electronica"],
                ),
                TrackRecord(
                    path="/music/Artist/Beta/01.flac",
                    root_position=0,
                    file_type="flac",
                    artist="Artist",
                    album_artist="Artist",
                    album="Beta",
                    title="One",
                    genres=["Rock"],
                ),
                TrackRecord(
                    path="/music/Artist/Gamma/01.flac",
                    root_position=0,
                    file_type="flac",
                    artist="Artist",
                    album_artist="Artist",
                    album="Gamma",
                    title="One",
                    genres=["Ambient"],
                ),
            ]
            save_library(
                MusicLibrary(
                    roots=["/music"],
                    tracks=tracks,
                    supported_extensions=[".flac"],
                    generated_at="2026-04-22T00:00:00+00:00",
                ),
                database,
            )

            genres = LibraryQueries(database).list_genres()

        self.assertEqual(
            [(genre.value, genre.song_count, genre.album_count) for genre in genres],
            [
                ("Ambient", 1, 1),
                ("Electronic", 2, 1),
                ("Electronica", 2, 1),
                ("Rock", 1, 1),
            ],
        )

    def test_list_genres_returns_empty_for_empty_library(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            save_library(
                MusicLibrary(
                    roots=["/music"],
                    tracks=[],
                    supported_extensions=[".flac"],
                    generated_at="2026-04-22T00:00:00+00:00",
                ),
                database,
            )

            genres = LibraryQueries(database).list_genres()

        self.assertEqual(genres, ())

    def test_album_art_track_id_uses_track_that_has_album_artwork(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            first_track = TrackRecord(
                path="/music/Artist/Album/01.flac",
                root_position=0,
                file_type="flac",
                artist="Artist",
                album_artist="Artist",
                album="Album",
                title="No Cover",
            )
            cover_track = TrackRecord(
                path="/music/Artist/Album/02.flac",
                root_position=0,
                file_type="flac",
                artist="Artist",
                album_artist="Artist",
                album="Album",
                title="Cover",
                album_artwork=TrackArtwork(mime_type="image/png", data=b"cover"),
            )
            save_library(
                MusicLibrary(
                    roots=["/music"],
                    tracks=[first_track, cover_track],
                    supported_extensions=[".flac"],
                    generated_at="2026-04-29T00:00:00+00:00",
                ),
                database,
            )

            api = LibraryQueries(database)
            album = api.get_album("artist::album")
            page = api.list_album_page(AlbumListQuery())

        self.assertTrue(album.has_cover)
        self.assertEqual(album.art_track_id, cover_track.track_id)
        self.assertEqual(page.items[0].art_track_id, cover_track.track_id)

    def test_save_library_writes_root_scan_stats(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            first_track = TrackRecord(
                path="/music/a/Artist A/First/01.flac",
                root_position=0,
                file_type="flac",
                artist="Artist A",
                album_artist="Album Artist A",
                album="First",
                title="One",
            )
            save_library(
                MusicLibrary(
                    roots=["/music/a", "/music/b", "/music/empty"],
                    tracks=[
                        first_track,
                        TrackRecord(
                            path="/music/a/Artist A/First/02.flac",
                            root_position=0,
                            file_type="flac",
                            artist="Artist A",
                            album_artist="Album Artist A",
                            album="First",
                            title="Two",
                        ),
                        TrackRecord(
                            path="/music/a/Artist B/Second/01.flac",
                            root_position=0,
                            file_type="flac",
                            artist="Artist B",
                            album_artist="Album Artist B",
                            album="Second",
                            title="Three",
                        ),
                        TrackRecord(
                            path="/music/b/Artist A/Third/01.flac",
                            root_position=1,
                            file_type="flac",
                            artist="Artist A",
                            album_artist="Album Artist A",
                            album="Third",
                            title="Four",
                        ),
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-04-22T00:00:00+00:00",
                    playlists=[
                        PlaylistRecord(
                            path="/music/a/mix.m3u",
                            name="Root A Mix",
                            root_position=0,
                            items=[PlaylistItemRecord(path=first_track.path)],
                        ),
                        PlaylistRecord(
                            path="/music/b/mix.m3u",
                            name="Root B Mix",
                            root_position=1,
                        ),
                    ],
                ),
                database,
            )

            stats = LibraryQueries(database).library_root_stats()
            total_stats = LibraryQueries(database).library_stats()

        self.assertEqual(
            [
                (
                    stat.root_position,
                    stat.tracks_scanned,
                    stat.albums_scanned,
                )
                for stat in stats
            ],
            [
                (0, 3, 2),
                (1, 1, 1),
                (2, 0, 0),
            ],
        )
        self.assertEqual(
            [
                (
                    artist.album_artist,
                    artist.tracks_scanned,
                    artist.albums_scanned,
                )
                for artist in stats[0].album_artists
            ],
            [
                ("Album Artist A", 2, 1),
                ("Album Artist B", 1, 1),
            ],
        )
        self.assertEqual(
            [
                (
                    artist.album_artist,
                    artist.tracks_scanned,
                    artist.albums_scanned,
                )
                for artist in stats[1].album_artists
            ],
            [("Album Artist A", 1, 1)],
        )
        self.assertEqual(stats[2].album_artists, ())
        self.assertEqual(total_stats.tracks_scanned, 4)
        self.assertEqual(total_stats.albums_scanned, 3)
        self.assertEqual(
            [
                (
                    artist.album_artist,
                    artist.tracks_scanned,
                    artist.albums_scanned,
                )
                for artist in total_stats.album_artists
            ],
            [
                ("Album Artist A", 3, 2),
                ("Album Artist B", 1, 1),
            ],
        )

    def test_album_artist_stats_group_case_variants_like_filters(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            save_library(
                MusicLibrary(
                    roots=["/music"],
                    tracks=[
                        TrackRecord(
                            path="/music/The Sea And Cake/First/01.flac",
                            root_position=0,
                            file_type="flac",
                            artist="The Sea And Cake",
                            album_artist="The Sea And Cake",
                            album="First",
                            title="One",
                        ),
                        TrackRecord(
                            path="/music/The Sea and Cake/Second/01.flac",
                            root_position=0,
                            file_type="flac",
                            artist="The Sea and Cake",
                            album_artist="The Sea and Cake",
                            album="Second",
                            title="Two",
                        ),
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-04-29T00:00:00+00:00",
                ),
                database,
            )

            api = LibraryQueries(database)
            filters = api.filter_options()
            root_stats = api.library_root_stats()
            total_stats = api.library_stats()
            expanded_query = api.expand_album_list_query(
                AlbumListQuery(artists=("The Sea and Cake",))
            )
            filtered_page = api.list_album_page(
                AlbumListQuery(artists=("The Sea and Cake",))
            )
            second_album = api.get_album("the-sea-and-cake::second")
            connection = connect_database(database, create=False)
            try:
                stored_album_artists = tuple(
                    str(row["artist"])
                    for row in connection.execute(
                        """
                        SELECT artist
                        FROM library_album_artists
                        ORDER BY album_id, position
                        """
                    )
                )
                stored_track_album_artists = tuple(
                    str(row["album_artist"])
                    for row in connection.execute(
                        """
                        SELECT album_artist
                        FROM library_tracks
                        ORDER BY album_id
                        """
                    )
                )
            finally:
                connection.close()

        self.assertEqual(filters.artists, ("The Sea And Cake",))
        self.assertEqual(
            stored_album_artists,
            ("The Sea And Cake", "The Sea And Cake"),
        )
        self.assertEqual(
            stored_track_album_artists,
            ("The Sea And Cake", "The Sea and Cake"),
        )
        self.assertEqual(expanded_query.artists, ("The Sea And Cake",))
        self.assertEqual(
            [album.artist for album in filtered_page.items],
            ["The Sea And Cake", "The Sea And Cake"],
        )
        self.assertEqual(second_album.artist, "The Sea And Cake")
        self.assertEqual(second_album.album_artists, ("The Sea And Cake",))
        self.assertEqual(
            [
                (
                    artist.album_artist,
                    artist.tracks_scanned,
                    artist.albums_scanned,
                )
                for artist in root_stats[0].album_artists
            ],
            [("The Sea And Cake", 2, 2)],
        )
        self.assertEqual(
            [
                (
                    artist.album_artist,
                    artist.tracks_scanned,
                    artist.albums_scanned,
                )
                for artist in total_stats.album_artists
            ],
            [("The Sea And Cake", 2, 2)],
        )

    def test_total_album_stats_count_album_spanning_roots_once(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            save_library(
                MusicLibrary(
                    roots=["/music/a", "/music/b"],
                    tracks=[
                        TrackRecord(
                            path="/music/a/Artist/Split Album/01.flac",
                            root_position=0,
                            file_type="flac",
                            artist="Artist",
                            album_artist="Artist",
                            album="Split Album",
                            title="One",
                        ),
                        TrackRecord(
                            path="/music/b/Artist/Split Album/02.flac",
                            root_position=1,
                            file_type="flac",
                            artist="Artist",
                            album_artist="Artist",
                            album="Split Album",
                            title="Two",
                        ),
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-05-08T00:00:00+00:00",
                ),
                database,
            )

            api = LibraryQueries(database)
            root_stats = api.library_root_stats()
            total_stats = api.library_stats()

        self.assertEqual(
            [
                (
                    stat.root_position,
                    stat.tracks_scanned,
                    stat.albums_scanned,
                )
                for stat in root_stats
            ],
            [
                (0, 1, 1),
                (1, 1, 1),
            ],
        )
        self.assertEqual(total_stats.tracks_scanned, 2)
        self.assertEqual(total_stats.albums_scanned, 1)
        self.assertEqual(
            [
                (
                    artist.album_artist,
                    artist.tracks_scanned,
                    artist.albums_scanned,
                )
                for artist in total_stats.album_artists
            ],
            [("Artist", 2, 1)],
        )


class LibraryAlbumArtistMappingTest(unittest.TestCase):
    def save_single_album_artist_mapping(
        self,
        album_artist: str,
        *,
        album_artist_split_patterns: tuple[str | None, ...] | None = None,
    ) -> tuple[str | None, list[str]]:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            save_kwargs = {}
            if album_artist_split_patterns is not None:
                save_kwargs["album_artist_split_patterns"] = album_artist_split_patterns
            save_library(
                MusicLibrary(
                    roots=["/music"],
                    tracks=[
                        TrackRecord(
                            path="/music/mapping-test/01.flac",
                            root_position=0,
                            file_type="flac",
                            artist=album_artist,
                            album_artist=album_artist,
                            album="Mapping Test",
                            title="One",
                        )
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-04-22T00:00:00+00:00",
                ),
                database,
                **save_kwargs,
            )

            connection = connect_database(database, create=False)
            try:
                mapping_row = connection.execute(
                    """
                    SELECT mapped_artists
                    FROM album_artist_split_mappings
                    WHERE album_artist = ?
                    """,
                    (album_artist,),
                ).fetchone()
                album_artists = [
                    str(row["artist"])
                    for row in connection.execute(
                        """
                        SELECT artist
                        FROM library_album_artists
                        ORDER BY album_id, position
                        """
                    )
                ]
            finally:
                connection.close()

        mapping_text = (
            str(mapping_row["mapped_artists"]) if mapping_row is not None else None
        )
        return mapping_text, album_artists

    def test_save_library_maps_split_album_artists_for_queries(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            album_id = "berlin-philharmonic-bell-and-karajan::foo"
            save_library(
                MusicLibrary(
                    roots=["/music"],
                    tracks=[
                        TrackRecord(
                            path="/music/foo/01.flac",
                            root_position=0,
                            file_type="flac",
                            artist="Track Artist",
                            album_artist="Berlin Philharmonic, Bell & Karajan",
                            album="Foo",
                            title="One",
                        )
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-04-22T00:00:00+00:00",
                ),
                database,
            )

            connection = connect_database(database, create=False)
            try:
                mapping_row = connection.execute(
                    """
                    SELECT mapped_artists
                    FROM album_artist_split_mappings
                    WHERE album_artist = ?
                    """,
                    ("Berlin Philharmonic, Bell & Karajan",),
                ).fetchone()
                album_artist_rows = [
                    str(row["artist"])
                    for row in connection.execute(
                        """
                        SELECT artist
                        FROM library_album_artists
                        WHERE album_id = ?
                        ORDER BY position
                        """,
                        (album_id,),
                    )
                ]
            finally:
                connection.close()

            api = LibraryQueries(database)
            filtered_page = api.list_album_page(AlbumListQuery(artists=("Bell",)))
            search_page = api.list_album_page(AlbumListQuery(search="Karajan"))
            stats = api.library_stats()
            root_stats = api.library_root_stats()
            album = api.get_album(album_id)

        self.assertIsNotNone(mapping_row)
        self.assertEqual(
            str(mapping_row["mapped_artists"]),
            "Berlin Philharmonic\nBell\nKarajan",
        )
        self.assertEqual(
            album_artist_rows,
            ["Berlin Philharmonic", "Bell", "Karajan"],
        )
        self.assertEqual([item.album for item in filtered_page.items], ["Foo"])
        self.assertEqual([item.album for item in search_page.items], ["Foo"])
        self.assertEqual(album.artist, "Berlin Philharmonic, Bell, Karajan")
        self.assertEqual(album.album_artists, ("Berlin Philharmonic", "Bell", "Karajan"))
        self.assertIn(
            "Bell",
            {artist.album_artist for artist in stats.album_artists},
        )
        self.assertIn(
            "Karajan",
            {artist.album_artist for artist in root_stats[0].album_artists},
        )

    def test_rescan_reapplies_edited_mapping_without_changing_raw_album_id(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            raw_artist = "Bill Evans And Jim Hall"
            raw_album_id = "bill-evans-and-jim-hall::undercurrent"
            with connect_database(database) as connection:
                connection.execute(
                    """
                    INSERT INTO album_artist_split_mappings (
                        album_artist,
                        mapped_artists
                    ) VALUES (?, ?)
                    """,
                    (raw_artist, "Bill Evans\nJim Hall"),
                )

            save_library(
                MusicLibrary(
                    roots=["/music"],
                    tracks=[
                        TrackRecord(
                            path="/music/Bill Evans/Undercurrent/01.flac",
                            root_position=0,
                            file_type="flac",
                            artist=raw_artist,
                            album_artist=raw_artist,
                            album="Undercurrent",
                            title="My Funny Valentine",
                        )
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-04-22T00:00:00+00:00",
                ),
                database,
            )

            with connect_database(database) as connection:
                self.assertEqual(
                    [
                        str(row["album_id"])
                        for row in connection.execute(
                            "SELECT album_id FROM library_albums"
                        )
                    ],
                    [raw_album_id],
                )
                connection.execute(
                    """
                    UPDATE album_artist_split_mappings
                    SET mapped_artists = ?
                    WHERE album_artist = ?
                    """,
                    (raw_artist, raw_artist),
                )

            rescanned_library = MusicLibrary(
                roots=["/music"],
                tracks=list(load_rescan_tracks_by_path(database).values()),
                supported_extensions=[".flac"],
                generated_at="2026-04-22T00:00:00+00:00",
            )
            save_rescanned_library_incremental(
                rescanned_library,
                database,
                root_rows=[(0, "/music")],
                scanned_paths=[],
            )

            with connect_database(database, create=False) as connection:
                album_ids = [
                    str(row["album_id"])
                    for row in connection.execute(
                        "SELECT album_id FROM library_albums"
                    )
                ]
                album_artists = [
                    str(row["artist"])
                    for row in connection.execute(
                        """
                        SELECT artist
                        FROM library_album_artists
                        WHERE album_id = ?
                        ORDER BY position
                        """,
                        (raw_album_id,),
                    )
                ]

            api = LibraryQueries(database)
            filtered_page = api.list_album_page(AlbumListQuery(artists=(raw_artist,)))
            search_page = api.list_album_page(AlbumListQuery(search="Jim Hall"))
            stats = api.library_stats()
            root_stats = api.library_root_stats()
            album = api.get_album(raw_album_id)

        self.assertEqual(album_ids, [raw_album_id])
        self.assertEqual(album_artists, [raw_artist])
        self.assertEqual(album.album_artists, (raw_artist,))
        self.assertEqual([item.album for item in filtered_page.items], ["Undercurrent"])
        self.assertEqual([item.album for item in search_page.items], ["Undercurrent"])
        self.assertIn(raw_artist, {artist.album_artist for artist in stats.album_artists})
        self.assertIn(
            raw_artist,
            {artist.album_artist for artist in root_stats[0].album_artists},
        )

    def test_save_library_preserves_user_album_artist_mapping(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            connection = connect_database(database)
            try:
                connection.execute(
                    """
                    INSERT INTO album_artist_split_mappings (
                        album_artist,
                        mapped_artists
                    ) VALUES (?, ?)
                    """,
                    ("Brian Eno & Robert Fripp", "Robert Fripp\nBrian Eno"),
                )
                connection.commit()
            finally:
                connection.close()

            save_library(
                MusicLibrary(
                    roots=["/music"],
                    tracks=[
                        TrackRecord(
                            path="/music/no-pussyfooting/01.flac",
                            root_position=0,
                            file_type="flac",
                            artist="Brian Eno",
                            album_artist="Brian Eno & Robert Fripp",
                            album="No Pussyfooting",
                            title="The Heavenly Music Corporation",
                        )
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-04-22T00:00:00+00:00",
                ),
                database,
            )

            connection = connect_database(database, create=False)
            try:
                mapping_text = str(
                    connection.execute(
                        """
                        SELECT mapped_artists
                        FROM album_artist_split_mappings
                        WHERE album_artist = ?
                        """,
                        ("Brian Eno & Robert Fripp",),
                    ).fetchone()["mapped_artists"]
                )
            finally:
                connection.close()

            album = LibraryQueries(database).get_album(
                "brian-eno-and-robert-fripp::no-pussyfooting"
            )

        self.assertEqual(mapping_text, "Robert Fripp\nBrian Eno")
        self.assertEqual(album.album_artists, ("Robert Fripp", "Brian Eno"))

    def test_save_library_does_not_map_unsplit_album_artist(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            save_library(
                MusicLibrary(
                    roots=["/music"],
                    tracks=[
                        TrackRecord(
                            path="/music/radiohead/01.flac",
                            root_position=0,
                            file_type="flac",
                            artist="Radiohead",
                            album_artist="Radiohead",
                            album="Kid A",
                            title="Everything in Its Right Place",
                        )
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-04-22T00:00:00+00:00",
                ),
                database,
            )

            connection = connect_database(database, create=False)
            try:
                mapping_count = int(
                    connection.execute(
                        "SELECT COUNT(*) AS count FROM album_artist_split_mappings"
                    ).fetchone()["count"]
                )
            finally:
                connection.close()

        self.assertEqual(mapping_count, 0)

    def test_save_library_records_default_comma_mapping_unchanged(self) -> None:
        mapping_text, album_artists = self.save_single_album_artist_mapping(
            "Earth, Wind"
        )

        self.assertEqual(mapping_text, "Earth, Wind")
        self.assertEqual(album_artists, ["Earth, Wind"])

    def test_save_library_records_default_word_pattern_mapping_unchanged(self) -> None:
        mapping_text, album_artists = self.save_single_album_artist_mapping(
            "Brian Eno And Roger Eno"
        )

        self.assertEqual(mapping_text, "Brian Eno And Roger Eno")
        self.assertEqual(album_artists, ["Brian Eno And Roger Eno"])

    def test_save_library_records_custom_pattern_mapping_unchanged(self) -> None:
        mapping_text, album_artists = self.save_single_album_artist_mapping(
            "Alice feat. Bob",
            album_artist_split_patterns=("feat.",),
        )

        self.assertEqual(mapping_text, "Alice feat. Bob")
        self.assertEqual(album_artists, ["Alice feat. Bob"])


class LibraryMusicBrainzPersistenceTest(unittest.TestCase):
    def test_save_library_keeps_musicbrainz_links_when_library_is_cleared(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            connection = connect_database(database)
            try:
                connection.execute(
                    """
                    INSERT INTO library_albums (
                        album_id, album, year, track_count
                    ) VALUES (?, ?, ?, ?)
                    """,
                    ("artist::album", "Album", 2000, 1),
                )
                connection.execute(
                    """
                    INSERT INTO album_musicbrainz_links (
                        file_album_id, release_mbid, release_group_mbid
                    ) VALUES (?, ?, ?)
                    """,
                    ("artist::album", "release-1", "group-1"),
                )
                connection.commit()
            finally:
                connection.close()

            save_library(
                MusicLibrary(
                    roots=[],
                    tracks=[],
                    supported_extensions=[],
                    generated_at="2026-04-22T00:00:00+00:00",
                ),
                database,
            )

            connection = connect_database(database)
            try:
                self.assertIsNone(
                    connection.execute(
                        "SELECT 1 FROM library_albums WHERE album_id = ?",
                        ("artist::album",),
                    ).fetchone()
                )
                row = connection.execute(
                    """
                    SELECT release_mbid, release_group_mbid
                    FROM album_musicbrainz_links
                    WHERE file_album_id = ?
                    """,
                    ("artist::album",),
                ).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(str(row["release_mbid"]), "release-1")
                self.assertEqual(str(row["release_group_mbid"]), "group-1")
            finally:
                connection.close()

    def test_save_library_migrates_legacy_split_album_state_and_musicbrainz_links(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            old_album_id = "bill-evans-jim-hall::undercurrent"
            new_album_id = "bill-evans-and-jim-hall::undercurrent"
            track_path = "/music/Bill Evans/Undercurrent/01.flac"
            connection = connect_database(database)
            try:
                connection.execute(
                    """
                    INSERT INTO library_albums (
                        album_id,
                        album,
                        year,
                        track_count,
                        added_at,
                        starred_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        old_album_id,
                        "Undercurrent",
                        1962,
                        1,
                        "2026-05-01T12:00:00+00:00",
                        "2026-05-02T12:00:00+00:00",
                    ),
                )
                connection.executemany(
                    """
                    INSERT INTO library_album_artists (album_id, position, artist)
                    VALUES (?, ?, ?)
                    """,
                    (
                        (old_album_id, 0, "Bill Evans"),
                        (old_album_id, 1, "Jim Hall"),
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO library_tracks (
                        album_id,
                        path,
                        album_artist,
                        artist,
                        album,
                        title
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        old_album_id,
                        track_path,
                        "Bill Evans And Jim Hall",
                        "Bill Evans And Jim Hall",
                        "Undercurrent",
                        "My Funny Valentine",
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO album_user_state (album_id, starred_at)
                    VALUES (?, ?)
                    """,
                    (old_album_id, "2026-05-02T12:00:00+00:00"),
                )
                connection.execute(
                    """
                    INSERT INTO album_musicbrainz_links (
                        file_album_id,
                        release_mbid,
                        release_group_mbid
                    ) VALUES (?, ?, ?)
                    """,
                    (old_album_id, None, "group-1"),
                )
                connection.execute(
                    """
                    INSERT INTO album_musicbrainz_track_links (
                        path,
                        file_album_id,
                        release_mbid,
                        release_group_mbid
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (track_path, old_album_id, None, "group-1"),
                )
                connection.commit()
            finally:
                connection.close()

            save_library(
                MusicLibrary(
                    roots=["/music"],
                    tracks=[
                        TrackRecord(
                            path=track_path,
                            root_position=0,
                            file_type="flac",
                            artist="Bill Evans And Jim Hall",
                            album_artist="Bill Evans And Jim Hall",
                            album="Undercurrent",
                            title="My Funny Valentine",
                        )
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-05-03T00:00:00+00:00",
                ),
                database,
                album_artist_split_patterns=[],
            )

            connection = connect_database(database, create=False)
            try:
                album_row = connection.execute(
                    """
                    SELECT added_at, starred_at
                    FROM library_albums
                    WHERE album_id = ?
                    """,
                    (new_album_id,),
                ).fetchone()
                self.assertIsNotNone(album_row)
                self.assertEqual(
                    str(album_row["added_at"]),
                    "2026-05-01T12:00:00+00:00",
                )
                self.assertEqual(
                    str(album_row["starred_at"]),
                    "2026-05-02T12:00:00+00:00",
                )
                self.assertIsNotNone(
                    connection.execute(
                        """
                        SELECT 1
                        FROM album_user_state
                        WHERE album_id = ?
                            AND starred_at = ?
                        """,
                        (new_album_id, "2026-05-02T12:00:00+00:00"),
                    ).fetchone()
                )
                self.assertIsNone(
                    connection.execute(
                        "SELECT 1 FROM album_user_state WHERE album_id = ?",
                        (old_album_id,),
                    ).fetchone()
                )
                track_link = connection.execute(
                    """
                    SELECT file_album_id, release_group_mbid
                    FROM album_musicbrainz_track_links
                    WHERE path = ?
                    """,
                    (track_path,),
                ).fetchone()
                self.assertIsNotNone(track_link)
                self.assertEqual(str(track_link["file_album_id"]), new_album_id)
                self.assertEqual(str(track_link["release_group_mbid"]), "group-1")
            finally:
                connection.close()

            link = album_musicbrainz_link(database, new_album_id)
            self.assertIsNotNone(link)
            self.assertEqual(link.release_group_mbid, "group-1")

    def test_save_library_uses_musicbrainz_release_fingerprints_for_album_ids(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            release_payloads = {
                "11111111-1111-1111-1111-111111111111": {
                    "country": "US",
                    "date": "1997-05-21",
                    "media": [{"format": "CD"}],
                    "barcode": "724385522921",
                    "release-group": {
                        "id": "33333333-3333-3333-3333-333333333333"
                    },
                },
                "22222222-2222-2222-2222-222222222222": {
                    "country": "GB",
                    "date": "1997",
                    "media": [{"format": "Vinyl"}],
                    "label-info": [{"catalog-number": "NODATA 02"}],
                    "release-group": {
                        "id": "44444444-4444-4444-4444-444444444444"
                    },
                },
            }

            def fake_get_musicbrainz_entity(
                _connection: object,
                _client: object,
                *,
                entity_type: str,
                mbid: str,
            ) -> dict[str, object] | None:
                self.assertEqual(entity_type, "release")
                return release_payloads[mbid]

            library = MusicLibrary(
                roots=[],
                tracks=[
                    TrackRecord(
                        path="/music/us/01.flac",
                        artist="Radiohead",
                        album_artist="Radiohead",
                        album="OK Computer",
                        title="Airbag",
                        musicbrainz_release_mbid="11111111-1111-1111-1111-111111111111",
                    ),
                    TrackRecord(
                        path="/music/uk/01.flac",
                        artist="Radiohead",
                        album_artist="Radiohead",
                        album="OK Computer",
                        title="Airbag",
                        musicbrainz_release_mbid="22222222-2222-2222-2222-222222222222",
                    ),
                    TrackRecord(
                        path="/music/plain/01.flac",
                        artist="Radiohead",
                        album_artist="Radiohead",
                        album="OK Computer",
                        title="Airbag",
                    ),
                ],
                supported_extensions=[],
                generated_at="2026-04-22T00:00:00+00:00",
            )

            with patch(
                "kukicha.use_case.library.get_musicbrainz_entity",
                side_effect=fake_get_musicbrainz_entity,
            ):
                save_library(library, database)

            connection = connect_database(database, create=False)
            try:
                album_ids = [
                    str(row["album_id"])
                    for row in connection.execute(
                        "SELECT album_id FROM library_albums ORDER BY album_id"
                    )
                ]
                self.assertEqual(
                    album_ids,
                    [
                        "radiohead::ok-computer",
                        "radiohead::ok-computer::14e",
                        "radiohead::ok-computer::608",
                    ],
                )
                rows = [
                    (
                        str(row["file_album_id"]),
                        str(row["release_mbid"]),
                        str(row["release_group_mbid"]),
                    )
                    for row in connection.execute(
                        """
                        SELECT file_album_id, release_mbid, release_group_mbid
                        FROM album_musicbrainz_links
                        ORDER BY file_album_id, release_mbid
                        """
                    )
                ]
                self.assertEqual(
                    rows,
                    [
                        (
                            "radiohead::ok-computer",
                            "11111111-1111-1111-1111-111111111111",
                            "33333333-3333-3333-3333-333333333333",
                        ),
                        (
                            "radiohead::ok-computer",
                            "22222222-2222-2222-2222-222222222222",
                            "44444444-4444-4444-4444-444444444444",
                        ),
                    ],
                )
            finally:
                connection.close()

    def test_save_library_reuses_single_suffixed_musicbrainz_link_for_rescan(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            connection = connect_database(database)
            try:
                connection.execute(
                    """
                    INSERT INTO album_musicbrainz_links (
                        file_album_id, release_mbid, release_group_mbid
                    ) VALUES (?, ?, ?)
                    """,
                    (
                        "radiohead::ok-computer::608",
                        "11111111-1111-1111-1111-111111111111",
                        "33333333-3333-3333-3333-333333333333",
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            library = MusicLibrary(
                roots=[],
                tracks=[
                    TrackRecord(
                        path="/music/us/01.flac",
                        artist="Radiohead",
                        album_artist="Radiohead",
                        album="OK Computer",
                        title="Airbag",
                    ),
                ],
                supported_extensions=[],
                generated_at="2026-04-22T00:00:00+00:00",
            )

            with patch(
                "kukicha.use_case.musicbrainz.MusicBrainzClient.fetch_lookup",
                side_effect=AssertionError("unexpected MusicBrainz lookup"),
            ):
                save_library(library, database)
            connection = connect_database(database, create=False)
            try:
                row = connection.execute(
                    "SELECT album_id FROM library_albums"
                ).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(str(row["album_id"]), "radiohead::ok-computer::608")
            finally:
                connection.close()

    def test_save_library_derives_release_album_id_from_file_album_link(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            connection = connect_database(database)
            try:
                connection.execute(
                    """
                    INSERT INTO album_musicbrainz_links (
                        file_album_id, release_mbid, release_group_mbid
                    ) VALUES (?, ?, ?)
                    """,
                    (
                        "radiohead::ok-computer",
                        "11111111-1111-1111-1111-111111111111",
                        "33333333-3333-3333-3333-333333333333",
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            library = MusicLibrary(
                roots=[],
                tracks=[
                    TrackRecord(
                        path="/music/us/01.flac",
                        artist="Radiohead",
                        album_artist="Radiohead",
                        album="OK Computer",
                        title="Airbag",
                    ),
                ],
                supported_extensions=[],
                generated_at="2026-04-22T00:00:00+00:00",
            )
            release_payload = {
                "country": "US",
                "date": "1997-05-21",
                "media": [{"format": "CD"}],
                "barcode": "724385522921",
                "release-group": {
                    "id": "33333333-3333-3333-3333-333333333333"
                },
            }

            with patch(
                "kukicha.use_case.library.get_musicbrainz_entity",
                return_value=release_payload,
            ) as get_musicbrainz_entity:
                save_library(library, database)

            get_musicbrainz_entity.assert_called_once()
            connection = connect_database(database, create=False)
            try:
                row = connection.execute(
                    "SELECT album_id FROM library_albums"
                ).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(str(row["album_id"]), "radiohead::ok-computer::608")
            finally:
                connection.close()

    def test_save_library_uses_track_musicbrainz_links_to_split_untagged_rescan(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            release_payloads = {
                "11111111-1111-1111-1111-111111111111": {
                    "country": "US",
                    "date": "1997-05-21",
                    "media": [{"format": "CD"}],
                    "barcode": "724385522921",
                    "release-group": {
                        "id": "33333333-3333-3333-3333-333333333333"
                    },
                },
                "22222222-2222-2222-2222-222222222222": {
                    "country": "GB",
                    "date": "1997",
                    "media": [{"format": "Vinyl"}],
                    "label-info": [{"catalog-number": "NODATA 02"}],
                    "release-group": {
                        "id": "44444444-4444-4444-4444-444444444444"
                    },
                },
            }
            connection = connect_database(database)
            try:
                for release_mbid, release_group_mbid in (
                    (
                        "11111111-1111-1111-1111-111111111111",
                        "33333333-3333-3333-3333-333333333333",
                    ),
                    (
                        "22222222-2222-2222-2222-222222222222",
                        "44444444-4444-4444-4444-444444444444",
                    ),
                ):
                    connection.execute(
                        """
                        INSERT INTO album_musicbrainz_links (
                            file_album_id, release_mbid, release_group_mbid
                        ) VALUES (?, ?, ?)
                        """,
                        ("radiohead::ok-computer", release_mbid, release_group_mbid),
                    )
                connection.execute(
                    """
                    INSERT INTO album_musicbrainz_track_links (
                        path, file_album_id, release_mbid, release_group_mbid
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        "/music/us/01.flac",
                        "radiohead::ok-computer",
                        "11111111-1111-1111-1111-111111111111",
                        "33333333-3333-3333-3333-333333333333",
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO album_musicbrainz_track_links (
                        path, file_album_id, release_mbid, release_group_mbid
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        "/music/uk/01.flac",
                        "radiohead::ok-computer",
                        "22222222-2222-2222-2222-222222222222",
                        "44444444-4444-4444-4444-444444444444",
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            library = MusicLibrary(
                roots=[],
                tracks=[
                    TrackRecord(
                        path="/music/us/01.flac",
                        artist="Radiohead",
                        album_artist="Radiohead",
                        album="OK Computer",
                        title="Airbag",
                    ),
                    TrackRecord(
                        path="/music/uk/01.flac",
                        artist="Radiohead",
                        album_artist="Radiohead",
                        album="OK Computer",
                        title="Airbag",
                    ),
                ],
                supported_extensions=[],
                generated_at="2026-04-22T00:00:00+00:00",
            )

            with patch(
                "kukicha.use_case.library.get_musicbrainz_entity",
                side_effect=lambda _connection, _client, *, entity_type, mbid: (
                    release_payloads[mbid] if entity_type == "release" else None
                ),
            ):
                save_library(library, database)

            connection = connect_database(database, create=False)
            try:
                album_ids = [
                    str(row["album_id"])
                    for row in connection.execute(
                        "SELECT album_id FROM library_albums ORDER BY album_id"
                    )
                ]
                self.assertEqual(
                    album_ids,
                    [
                        "radiohead::ok-computer::14e",
                        "radiohead::ok-computer::608",
                    ],
                )
                track_rows = [
                    (str(row["path"]), str(row["album_id"]))
                    for row in connection.execute(
                        "SELECT path, album_id FROM library_tracks ORDER BY path"
                    )
                ]
                self.assertEqual(
                    track_rows,
                    [
                        ("/music/uk/01.flac", "radiohead::ok-computer::14e"),
                        ("/music/us/01.flac", "radiohead::ok-computer::608"),
                    ],
                )
            finally:
                connection.close()

    def test_save_library_does_not_overwrite_track_links_with_single_album_link(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            release_payloads = {
                "11111111-1111-1111-1111-111111111111": {
                    "country": "US",
                    "date": "1997-05-21",
                    "media": [{"format": "CD"}],
                    "barcode": "724385522921",
                    "release-group": {
                        "id": "33333333-3333-3333-3333-333333333333"
                    },
                },
                "22222222-2222-2222-2222-222222222222": {
                    "country": "GB",
                    "date": "1997",
                    "media": [{"format": "Vinyl"}],
                    "label-info": [{"catalog-number": "NODATA 02"}],
                    "release-group": {
                        "id": "44444444-4444-4444-4444-444444444444"
                    },
                },
            }
            connection = connect_database(database)
            try:
                connection.execute(
                    """
                    INSERT INTO library_albums (
                        album_id, album, year, track_count
                    ) VALUES (?, ?, ?, ?)
                    """,
                    ("radiohead::ok-computer::608", "OK Computer", 1997, 2),
                )
                connection.execute(
                    """
                    INSERT INTO library_album_artists (album_id, position, artist)
                    VALUES (?, ?, ?)
                    """,
                    ("radiohead::ok-computer::608", 0, "Radiohead"),
                )
                connection.executemany(
                    """
                    INSERT INTO library_tracks (
                        album_id, path, album_artist, artist, album, title
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        (
                            "radiohead::ok-computer::608",
                            "/music/us/01.flac",
                            "Radiohead",
                            "Radiohead",
                            "OK Computer",
                            "Airbag",
                        ),
                        (
                            "radiohead::ok-computer::608",
                            "/music/uk/01.flac",
                            "Radiohead",
                            "Radiohead",
                            "OK Computer",
                            "Airbag",
                        ),
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO album_musicbrainz_links (
                        file_album_id, release_mbid, release_group_mbid
                    ) VALUES (?, ?, ?)
                    """,
                    (
                        "radiohead::ok-computer",
                        "22222222-2222-2222-2222-222222222222",
                        "44444444-4444-4444-4444-444444444444",
                    ),
                )
                connection.executemany(
                    """
                    INSERT INTO album_musicbrainz_track_links (
                        path, file_album_id, release_mbid, release_group_mbid
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        (
                            "/music/us/01.flac",
                            "radiohead::ok-computer",
                            "11111111-1111-1111-1111-111111111111",
                            "33333333-3333-3333-3333-333333333333",
                        ),
                        (
                            "/music/uk/01.flac",
                            "radiohead::ok-computer",
                            "22222222-2222-2222-2222-222222222222",
                            "44444444-4444-4444-4444-444444444444",
                        ),
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            library = MusicLibrary(
                roots=[],
                tracks=[
                    TrackRecord(
                        path="/music/us/01.flac",
                        artist="Radiohead",
                        album_artist="Radiohead",
                        album="OK Computer",
                        title="Airbag",
                    ),
                    TrackRecord(
                        path="/music/uk/01.flac",
                        artist="Radiohead",
                        album_artist="Radiohead",
                        album="OK Computer",
                        title="Airbag",
                    ),
                ],
                supported_extensions=[],
                generated_at="2026-04-22T00:00:00+00:00",
            )

            with patch(
                "kukicha.use_case.library.get_musicbrainz_entity",
                side_effect=lambda _connection, _client, *, entity_type, mbid: (
                    release_payloads[mbid] if entity_type == "release" else None
                ),
            ):
                save_library(library, database)

            connection = connect_database(database, create=False)
            try:
                track_rows = [
                    (str(row["path"]), str(row["album_id"]))
                    for row in connection.execute(
                        "SELECT path, album_id FROM library_tracks ORDER BY path"
                    )
                ]
                self.assertEqual(
                    track_rows,
                    [
                        ("/music/uk/01.flac", "radiohead::ok-computer::14e"),
                        ("/music/us/01.flac", "radiohead::ok-computer::608"),
                    ],
                )
                track_link_counts = [
                    (str(row["release_mbid"]), int(row["count"]))
                    for row in connection.execute(
                        """
                        SELECT release_mbid, COUNT(*) AS count
                        FROM album_musicbrainz_track_links
                        GROUP BY release_mbid
                        ORDER BY release_mbid
                        """
                    )
                ]
                self.assertEqual(
                    track_link_counts,
                    [
                        ("11111111-1111-1111-1111-111111111111", 1),
                        ("22222222-2222-2222-2222-222222222222", 1),
                    ],
                )
            finally:
                connection.close()

    def test_save_library_backfills_track_musicbrainz_links_from_existing_suffixed_albums(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            release_payloads = {
                "11111111-1111-1111-1111-111111111111": {
                    "country": "US",
                    "date": "1997-05-21",
                    "media": [{"format": "CD"}],
                    "barcode": "724385522921",
                },
                "22222222-2222-2222-2222-222222222222": {
                    "country": "GB",
                    "date": "1997",
                    "media": [{"format": "Vinyl"}],
                    "label-info": [{"catalog-number": "NODATA 02"}],
                },
            }
            connection = connect_database(database)
            try:
                for release_mbid, payload in release_payloads.items():
                    store_musicbrainz_entity(
                        connection,
                        entity_type="release",
                        mbid=release_mbid,
                        endpoint_url=f"https://musicbrainz.org/ws/2/release/{release_mbid}",
                        payload=payload,
                    )
                for album_id in (
                    "radiohead::ok-computer::608",
                    "radiohead::ok-computer::14e",
                ):
                    connection.execute(
                        """
                        INSERT INTO library_albums (
                            album_id, album, year, track_count
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (album_id, "OK Computer", 1997, 1),
                    )
                for release_mbid, release_group_mbid in (
                    (
                        "11111111-1111-1111-1111-111111111111",
                        "33333333-3333-3333-3333-333333333333",
                    ),
                    (
                        "22222222-2222-2222-2222-222222222222",
                        "44444444-4444-4444-4444-444444444444",
                    ),
                ):
                    connection.execute(
                        """
                        INSERT INTO album_musicbrainz_links (
                            file_album_id, release_mbid, release_group_mbid
                        ) VALUES (?, ?, ?)
                        """,
                        ("radiohead::ok-computer", release_mbid, release_group_mbid),
                    )
                connection.execute(
                    """
                    INSERT INTO library_tracks (
                        album_id, path, album_artist, artist, album, title
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "radiohead::ok-computer::608",
                        "/music/us/01.flac",
                        "Radiohead",
                        "Radiohead",
                        "OK Computer",
                        "Airbag",
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO library_tracks (
                        album_id, path, album_artist, artist, album, title
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "radiohead::ok-computer::14e",
                        "/music/uk/01.flac",
                        "Radiohead",
                        "Radiohead",
                        "OK Computer",
                        "Airbag",
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            library = MusicLibrary(
                roots=[],
                tracks=[
                    TrackRecord(
                        path="/music/us/01.flac",
                        artist="Radiohead",
                        album_artist="Radiohead",
                        album="OK Computer",
                        title="Airbag",
                    ),
                    TrackRecord(
                        path="/music/uk/01.flac",
                        artist="Radiohead",
                        album_artist="Radiohead",
                        album="OK Computer",
                        title="Airbag",
                    ),
                ],
                supported_extensions=[],
                generated_at="2026-04-22T00:00:00+00:00",
            )

            with patch(
                "kukicha.use_case.musicbrainz.MusicBrainzClient.fetch_lookup",
                side_effect=AssertionError("unexpected MusicBrainz lookup"),
            ):
                save_library(library, database)
            connection = connect_database(database, create=False)
            try:
                album_ids = [
                    str(row["album_id"])
                    for row in connection.execute(
                        "SELECT album_id FROM library_albums ORDER BY album_id"
                    )
                ]
                self.assertEqual(
                    album_ids,
                    [
                        "radiohead::ok-computer::14e",
                        "radiohead::ok-computer::608",
                    ],
                )
                track_link_count = int(
                    connection.execute(
                        """
                        SELECT COUNT(*) AS count
                        FROM album_musicbrainz_track_links
                        """
                    ).fetchone()["count"]
                )
                self.assertEqual(track_link_count, 2)
            finally:
                connection.close()

    def test_album_musicbrainz_link_matches_suffixed_album_id_with_shared_file_album_id(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            connection = connect_database(database)
            try:
                release_payloads = {
                    "11111111-1111-1111-1111-111111111111": {
                        "country": "US",
                        "date": "1997-05-21",
                        "media": [{"format": "CD"}],
                        "barcode": "724385522921",
                    },
                    "22222222-2222-2222-2222-222222222222": {
                        "country": "GB",
                        "date": "1997",
                        "media": [{"format": "Vinyl"}],
                        "label-info": [{"catalog-number": "NODATA 02"}],
                    },
                }
                for release_mbid, payload in release_payloads.items():
                    store_musicbrainz_entity(
                        connection,
                        entity_type="release",
                        mbid=release_mbid,
                        endpoint_url=f"https://musicbrainz.org/ws/2/release/{release_mbid}",
                        payload=payload,
                    )
                connection.execute(
                    """
                    INSERT INTO album_musicbrainz_links (
                        file_album_id, release_mbid, release_group_mbid
                    ) VALUES (?, ?, ?)
                    """,
                    (
                        "radiohead::ok-computer",
                        "11111111-1111-1111-1111-111111111111",
                        "33333333-3333-3333-3333-333333333333",
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO album_musicbrainz_links (
                        file_album_id, release_mbid, release_group_mbid
                    ) VALUES (?, ?, ?)
                    """,
                    (
                        "radiohead::ok-computer",
                        "22222222-2222-2222-2222-222222222222",
                        "44444444-4444-4444-4444-444444444444",
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            link = album_musicbrainz_link(database, "radiohead::ok-computer::14e")
            self.assertIsNotNone(link)
            self.assertEqual(
                link.release_mbid,
                "22222222-2222-2222-2222-222222222222",
            )

    def test_album_musicbrainz_overrides_use_track_links_for_suffixed_album_ids(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            connection = connect_database(database)
            try:
                album_rows = (
                    ("radiohead::ok-computer::608", "OK Computer", 1997, 1),
                    ("radiohead::ok-computer::14e", "OK Computer", 1997, 1),
                )
                connection.executemany(
                    """
                    INSERT INTO library_albums (
                        album_id, album, year, track_count
                    ) VALUES (?, ?, ?, ?)
                    """,
                    album_rows,
                )
                connection.executemany(
                    """
                    INSERT INTO library_album_artists (album_id, position, artist)
                    VALUES (?, ?, ?)
                    """,
                    (
                        ("radiohead::ok-computer::608", 0, "Radiohead"),
                        ("radiohead::ok-computer::14e", 0, "Radiohead"),
                    ),
                )
                connection.executemany(
                    """
                    INSERT INTO library_tracks (
                        album_id, path, album_artist, artist, album, title
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        (
                            "radiohead::ok-computer::608",
                            "/music/us/01.flac",
                            "Radiohead",
                            "Radiohead",
                            "OK Computer",
                            "Airbag",
                        ),
                        (
                            "radiohead::ok-computer::14e",
                            "/music/uk/01.flac",
                            "Radiohead",
                            "Radiohead",
                            "OK Computer",
                            "Airbag",
                        ),
                    ),
                )
                connection.executemany(
                    """
                    INSERT INTO album_musicbrainz_links (
                        file_album_id, release_mbid, release_group_mbid
                    ) VALUES (?, ?, ?)
                    """,
                    (
                        (
                            "radiohead::ok-computer",
                            "11111111-1111-1111-1111-111111111111",
                            "33333333-3333-3333-3333-333333333333",
                        ),
                        (
                            "radiohead::ok-computer",
                            "22222222-2222-2222-2222-222222222222",
                            "44444444-4444-4444-4444-444444444444",
                        ),
                    ),
                )
                connection.executemany(
                    """
                    INSERT INTO album_musicbrainz_track_links (
                        path, file_album_id, release_mbid, release_group_mbid
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        (
                            "/music/us/01.flac",
                            "radiohead::ok-computer",
                            "11111111-1111-1111-1111-111111111111",
                            "33333333-3333-3333-3333-333333333333",
                        ),
                        (
                            "/music/uk/01.flac",
                            "radiohead::ok-computer",
                            "22222222-2222-2222-2222-222222222222",
                            "44444444-4444-4444-4444-444444444444",
                        ),
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            overrides = LibraryQueries(database).album_musicbrainz_overrides()
            album_ids_by_release = {
                override.release_mbid: override.album_id
                for override in overrides
            }

            self.assertEqual(
                album_ids_by_release,
                {
                    "11111111-1111-1111-1111-111111111111": (
                        "radiohead::ok-computer::608"
                    ),
                    "22222222-2222-2222-2222-222222222222": (
                        "radiohead::ok-computer::14e"
                    ),
                },
            )
            self.assertEqual(
                {override.artist for override in overrides},
                {"Radiohead"},
            )

    def test_save_library_keeps_itunes_lookup_cache_when_library_is_cleared(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            connection = connect_database(database)
            try:
                connection.execute(
                    """
                    INSERT INTO itunes_lookup_image_cache (
                        cache_key,
                        lookup_kind,
                        lookup_id,
                        fetched_at,
                        lookup_url,
                        artwork_url,
                        mime_type,
                        data
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "album:440769149",
                        "album",
                        "440769149",
                        "2026-04-23T00:00:00+00:00",
                        "https://itunes.apple.com/lookup?id=440769149&media=music",
                        "https://is1-ssl.mzstatic.com/image/thumb/example/3000x3000bb.jpg",
                        "image/jpeg",
                        b"cached-itunes-art",
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            save_library(
                MusicLibrary(
                    roots=[],
                    tracks=[],
                    supported_extensions=[],
                    generated_at="2026-04-22T00:00:00+00:00",
                ),
                database,
            )

            connection = connect_database(database)
            try:
                row = connection.execute(
                    """
                    SELECT lookup_kind, lookup_id, mime_type, data
                    FROM itunes_lookup_image_cache
                    WHERE cache_key = ?
                    """,
                    ("album:440769149",),
                ).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(str(row["lookup_kind"]), "album")
                self.assertEqual(str(row["lookup_id"]), "440769149")
                self.assertEqual(str(row["mime_type"]), "image/jpeg")
                self.assertEqual(bytes(row["data"]), b"cached-itunes-art")
            finally:
                connection.close()

    def test_sync_empty_roots_keeps_musicbrainz_links_when_album_is_removed(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            connection = connect_database(database)
            try:
                connection.execute(
                    "INSERT INTO library_roots (position, root_path) VALUES (?, ?)",
                    (0, "/music/a"),
                )
                connection.execute(
                    """
                    INSERT INTO library_albums (
                        album_id, album, year, track_count
                    ) VALUES (?, ?, ?, ?)
                    """,
                    ("artist::album", "Album", 2000, 1),
                )
                connection.execute(
                    """
                    INSERT INTO album_musicbrainz_links (
                        file_album_id, release_mbid, release_group_mbid
                    ) VALUES (?, ?, ?)
                    """,
                    ("artist::album", "release-1", "group-1"),
                )
                connection.execute(
                    """
                    INSERT INTO library_tracks (
                        album_id, root_position, path, album_artist, artist, album, title, date
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "artist::album",
                        0,
                        "/music/a/Artist/Album/01.flac",
                        "Artist",
                        "Artist",
                        "Album",
                        "Track",
                        "2000",
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            sync_library_roots(database, ())

            connection = connect_database(database)
            try:
                self.assertEqual(
                    int(
                        connection.execute(
                            "SELECT COUNT(*) AS count FROM library_roots"
                        ).fetchone()["count"]
                    ),
                    0,
                )
                self.assertIsNone(
                    connection.execute(
                        "SELECT 1 FROM library_albums WHERE album_id = ?",
                        ("artist::album",),
                    ).fetchone()
                )
                row = connection.execute(
                    """
                    SELECT release_mbid, release_group_mbid
                    FROM album_musicbrainz_links
                    WHERE file_album_id = ?
                    """,
                    ("artist::album",),
                ).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(str(row["release_mbid"]), "release-1")
                self.assertEqual(str(row["release_group_mbid"]), "group-1")
            finally:
                connection.close()

    def test_sync_empty_roots_keeps_itunes_lookup_cache_when_album_is_removed(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            connection = connect_database(database)
            try:
                connection.execute(
                    "INSERT INTO library_roots (position, root_path) VALUES (?, ?)",
                    (0, "/music/a"),
                )
                connection.execute(
                    """
                    INSERT INTO library_albums (
                        album_id, album, year, track_count
                    ) VALUES (?, ?, ?, ?)
                    """,
                    ("artist::album", "Album", 2000, 1),
                )
                connection.execute(
                    """
                    INSERT INTO itunes_lookup_image_cache (
                        cache_key,
                        lookup_kind,
                        lookup_id,
                        fetched_at,
                        lookup_url,
                        artwork_url,
                        mime_type,
                        data
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "album:440769149",
                        "album",
                        "440769149",
                        "2026-04-23T00:00:00+00:00",
                        "https://itunes.apple.com/lookup?id=440769149&media=music",
                        "https://is1-ssl.mzstatic.com/image/thumb/example/3000x3000bb.jpg",
                        "image/jpeg",
                        b"cached-itunes-art",
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO library_tracks (
                        album_id, root_position, path, album_artist, artist, album, title, date
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "artist::album",
                        0,
                        "/music/a/Artist/Album/01.m4a",
                        "Artist",
                        "Artist",
                        "Album",
                        "Track",
                        "2000",
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            sync_library_roots(database, ())

            connection = connect_database(database)
            try:
                row = connection.execute(
                    """
                    SELECT lookup_kind, lookup_id, mime_type, data
                    FROM itunes_lookup_image_cache
                    WHERE cache_key = ?
                    """,
                    ("album:440769149",),
                ).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(str(row["lookup_kind"]), "album")
                self.assertEqual(str(row["lookup_id"]), "440769149")
                self.assertEqual(str(row["mime_type"]), "image/jpeg")
                self.assertEqual(bytes(row["data"]), b"cached-itunes-art")
            finally:
                connection.close()

    def test_connect_database_migrates_legacy_musicbrainz_columns_out_of_library_albums(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    """
                    CREATE TABLE library_albums (
                        album_id TEXT PRIMARY KEY,
                        artist TEXT NOT NULL,
                        album TEXT NOT NULL,
                        year INTEGER,
                        track_count INTEGER NOT NULL,
                        musicbrainz_release_mbid TEXT,
                        musicbrainz_release_group_mbid TEXT
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE INDEX idx_library_albums_musicbrainz_release
                    ON library_albums (musicbrainz_release_mbid)
                    """
                )
                connection.execute(
                    """
                    CREATE INDEX idx_library_albums_musicbrainz_release_group
                    ON library_albums (musicbrainz_release_group_mbid)
                    """
                )
                connection.execute(
                    """
                    INSERT INTO library_albums (
                        album_id,
                        artist,
                        album,
                        year,
                        track_count,
                        musicbrainz_release_mbid,
                        musicbrainz_release_group_mbid
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    ("artist::album", "Artist", "Album", 2000, 1, "release-1", "group-1"),
                )
                connection.commit()
            finally:
                connection.close()

            connection = connect_database(database, create=False)
            try:
                album_columns = {
                    str(row["name"])
                    for row in connection.execute("PRAGMA table_info(library_albums)")
                }
                self.assertNotIn("artist", album_columns)
                self.assertNotIn("musicbrainz_release_mbid", album_columns)
                self.assertNotIn("musicbrainz_release_group_mbid", album_columns)
                self.assertIsNone(
                    connection.execute(
                        """
                        SELECT 1
                        FROM sqlite_master
                        WHERE type = 'index' AND name = 'idx_library_albums_musicbrainz_release'
                        """
                    ).fetchone()
                )
                self.assertIsNone(
                    connection.execute(
                        """
                        SELECT 1
                        FROM sqlite_master
                        WHERE type = 'index' AND name = 'idx_library_albums_musicbrainz_release_group'
                        """
                    ).fetchone()
                )
                row = connection.execute(
                    """
                    SELECT release_mbid, release_group_mbid
                    FROM album_musicbrainz_links
                    WHERE file_album_id = ?
                    """,
                    ("artist::album",),
                ).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(str(row["release_mbid"]), "release-1")
                self.assertEqual(str(row["release_group_mbid"]), "group-1")
                artist_rows = [
                    str(row["artist"])
                    for row in connection.execute(
                        """
                        SELECT artist
                        FROM library_album_artists
                        WHERE album_id = ?
                        ORDER BY position
                        """,
                        ("artist::album",),
                    )
                ]
                self.assertEqual(artist_rows, ["Artist"])
            finally:
                connection.close()

    def test_connect_database_migrates_album_musicbrainz_link_album_id_to_file_album_id(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    """
                    CREATE TABLE album_musicbrainz_links (
                        album_id TEXT PRIMARY KEY,
                        release_mbid TEXT,
                        release_group_mbid TEXT,
                        CHECK (
                            COALESCE(release_mbid, '') != ''
                            OR COALESCE(release_group_mbid, '') != ''
                        )
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO album_musicbrainz_links (
                        album_id, release_mbid, release_group_mbid
                    ) VALUES (?, ?, ?)
                    """,
                    ("artist::album", "release-1", "group-1"),
                )
                connection.commit()
            finally:
                connection.close()

            connection = connect_database(database, create=False)
            try:
                columns = {
                    str(row["name"])
                    for row in connection.execute("PRAGMA table_info(album_musicbrainz_links)")
                }
                self.assertIn("file_album_id", columns)
                self.assertNotIn("album_id", columns)
                row = connection.execute(
                    """
                    SELECT release_mbid, release_group_mbid
                    FROM album_musicbrainz_links
                    WHERE file_album_id = ?
                    """,
                    ("artist::album",),
                ).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(str(row["release_mbid"]), "release-1")
                self.assertEqual(str(row["release_group_mbid"]), "group-1")
                index_names = {
                    str(row["name"])
                    for row in connection.execute("PRAGMA index_list(album_musicbrainz_links)")
                }
                self.assertIn("idx_album_musicbrainz_links_unique", index_names)
                self.assertIn("idx_album_musicbrainz_links_file_album", index_names)
            finally:
                connection.close()

    def test_connect_database_drops_legacy_library_album_paths_table(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    """
                    CREATE TABLE library_album_paths (
                        album_id TEXT NOT NULL,
                        position INTEGER NOT NULL,
                        path TEXT NOT NULL,
                        PRIMARY KEY (album_id, position)
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO library_album_paths (album_id, position, path)
                    VALUES (?, ?, ?)
                    """,
                    ("artist::album", 0, "/music/Artist/Album/01.flac"),
                )
                connection.commit()
            finally:
                connection.close()

            connection = connect_database(database, create=False)
            try:
                self.assertIsNone(
                    connection.execute(
                        """
                        SELECT 1
                        FROM sqlite_master
                        WHERE type = 'table' AND name = 'library_album_paths'
                        """
                    ).fetchone()
                )
            finally:
                connection.close()

    def test_connect_database_migrates_file_created_date_columns_and_playlist_schema(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    """
                    CREATE TABLE library_albums (
                        album_id TEXT PRIMARY KEY,
                        artist TEXT NOT NULL,
                        album TEXT NOT NULL,
                        year INTEGER,
                        track_count INTEGER NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE library_tracks (
                        track_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        album_id TEXT,
                        root_position INTEGER,
                        path TEXT NOT NULL UNIQUE,
                        album_artist TEXT,
                        artist TEXT,
                        album TEXT,
                        title TEXT,
                        date TEXT
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE library_playlists (
                        playlist_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        root_position INTEGER,
                        path TEXT NOT NULL UNIQUE,
                        name TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO library_albums (
                        album_id, artist, album, year, track_count
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    ("artist::album", "Artist", "Album", 2000, 1),
                )
                connection.execute(
                    """
                    INSERT INTO library_tracks (
                        album_id, root_position, path, album_artist, artist, album, title, date
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "artist::album",
                        0,
                        "/music/Artist/Album/01.flac",
                        "Artist",
                        "Artist",
                        "Album",
                        "Track",
                        "2000",
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO library_playlists (root_position, path, name)
                    VALUES (?, ?, ?)
                    """,
                    (0, "/music/Mix.m3u8", "Mix"),
                )
                connection.commit()
            finally:
                connection.close()

            with patch(
                "kukicha.use_case.database.file_created_at",
                side_effect=lambda path: {
                    "/music/Artist/Album/01.flac": "2026-04-20T12:00:00+00:00",
                }.get(str(path)),
            ), patch(
                "kukicha.use_case.database.utc_now_iso",
                return_value="2026-04-21T12:00:00+00:00",
            ):
                connection = connect_database(database, create=False)
            try:
                track_columns = {
                    str(row["name"])
                    for row in connection.execute("PRAGMA table_info(library_tracks)")
                }
                album_columns = {
                    str(row["name"])
                    for row in connection.execute("PRAGMA table_info(library_albums)")
                }
                playlist_columns = {
                    str(row["name"])
                    for row in connection.execute("PRAGMA table_info(library_playlists)")
                }
                track_date = connection.execute(
                    "SELECT file_created_at FROM library_tracks"
                ).fetchone()["file_created_at"]
                album_date = connection.execute(
                    "SELECT file_created_at FROM library_albums"
                ).fetchone()["file_created_at"]
                playlist = connection.execute(
                    """
                    SELECT name, kind, source, created_at, updated_at
                    FROM library_playlists
                    WHERE playlist_id = 1
                    """
                ).fetchone()
            finally:
                connection.close()

        self.assertIn("file_created_at", track_columns)
        self.assertIn("file_created_at", album_columns)
        self.assertNotIn("artist", album_columns)
        self.assertNotIn("path", playlist_columns)
        self.assertNotIn("file_created_at", playlist_columns)
        self.assertIn("kind", playlist_columns)
        self.assertIn("source", playlist_columns)
        self.assertIn("created_at", playlist_columns)
        self.assertEqual(str(track_date), "2026-04-20T12:00:00+00:00")
        self.assertEqual(str(album_date), "2026-04-20T12:00:00+00:00")
        self.assertEqual(str(playlist["name"]), "Mix")
        self.assertEqual(str(playlist["kind"]), "local")
        self.assertEqual(str(playlist["source"]), "file_import")
        self.assertEqual(str(playlist["created_at"]), "2026-04-21T12:00:00+00:00")
        self.assertEqual(str(playlist["updated_at"]), "2026-04-21T12:00:00+00:00")


class LibraryPlaylistPersistenceTest(unittest.TestCase):
    def test_save_library_stores_file_created_dates_and_rolls_up_album_earliest_date(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            save_library(
                MusicLibrary(
                    roots=[],
                    tracks=[
                        TrackRecord(
                            path="/music/Artist/Album/02.flac",
                            file_created_at="2026-04-24T12:00:00+00:00",
                            file_type="flac",
                            artist="Artist",
                            album_artist="Artist",
                            album="Album",
                            title="Second",
                        ),
                        TrackRecord(
                            path="/music/Artist/Album/01.flac",
                            file_created_at="2026-04-22T12:00:00+00:00",
                            file_type="flac",
                            artist="Artist",
                            album_artist="Artist",
                            album="Album",
                            title="First",
                        ),
                    ],
                    playlists=[
                        PlaylistRecord(
                            path="/music/list.m3u8",
                            name="Mixed",
                            created_at="2026-04-25T12:00:00+00:00",
                        )
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-04-25T00:00:00+00:00",
                ),
                database,
            )

            connection = connect_database(database, create=False)
            try:
                album_row = connection.execute(
                    """
                    SELECT file_created_at
                    FROM library_albums
                    WHERE album_id = ?
                    """,
                    ("artist::album",),
                ).fetchone()
                track_rows = list(
                    connection.execute(
                        """
                        SELECT path, file_created_at
                        FROM library_tracks
                        ORDER BY path
                        """
                    )
                )
                playlist_row = connection.execute(
                    """
                    SELECT created_at, updated_at
                    FROM library_playlists
                    WHERE name = ?
                    """,
                    ("Mixed",),
                ).fetchone()
            finally:
                connection.close()

        self.assertIsNotNone(album_row)
        self.assertEqual(
            str(album_row["file_created_at"]),
            "2026-04-22T12:00:00+00:00",
        )
        self.assertEqual(
            [(str(row["path"]), str(row["file_created_at"])) for row in track_rows],
            [
                ("/music/Artist/Album/01.flac", "2026-04-22T12:00:00+00:00"),
                ("/music/Artist/Album/02.flac", "2026-04-24T12:00:00+00:00"),
            ],
        )
        self.assertIsNotNone(playlist_row)
        self.assertEqual(str(playlist_row["created_at"]), "2026-04-25T12:00:00+00:00")
        self.assertEqual(str(playlist_row["updated_at"]), "2026-04-25T12:00:00+00:00")

    def test_list_album_page_sorts_by_artist_by_default_and_can_sort_by_recently_added(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            save_library(
                MusicLibrary(
                    roots=[],
                    tracks=[
                        TrackRecord(
                            path="/music/Zulu/Old/01.flac",
                            file_created_at="2026-04-26T12:00:00+00:00",
                            file_type="flac",
                            artist="Zulu",
                            album_artist="Zulu",
                            album="Old",
                            title="Old Track",
                            date="1970",
                        ),
                        TrackRecord(
                            path="/music/Alpha/AAA Later/01.flac",
                            file_created_at="2026-04-24T12:00:00+00:00",
                            file_type="flac",
                            artist="Alpha",
                            album_artist="Alpha",
                            album="AAA Later",
                            title="Later Track",
                            date="2001",
                        ),
                        TrackRecord(
                            path="/music/Alpha/ZZZ Original/01.flac",
                            file_created_at="2026-04-24T12:00:00+00:00",
                            file_type="flac",
                            artist="Alpha",
                            album_artist="Alpha",
                            album="ZZZ Original",
                            title="Original Track",
                            date="1984-10-12",
                        ),
                        TrackRecord(
                            path="/music/Alpha/No Date/01.flac",
                            file_created_at="2026-04-24T12:00:00+00:00",
                            file_type="flac",
                            artist="Alpha",
                            album_artist="Alpha",
                            album="No Date",
                            title="Undated Track",
                        ),
                    ],
                    playlists=[
                        PlaylistRecord(
                            path="/music/recent.m3u8",
                            name="Recent Mix",
                            created_at="2026-04-25T12:00:00+00:00",
                        )
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-04-25T00:00:00+00:00",
                ),
                database,
            )
            with connect_database(database) as connection:
                for album, added_at in (
                    ("Old", "2026-04-26T12:00:00+00:00"),
                    ("AAA Later", "2026-04-24T12:00:00+00:00"),
                    ("ZZZ Original", "2026-04-24T12:00:00+00:00"),
                    ("No Date", ""),
                ):
                    connection.execute(
                        "UPDATE library_albums SET added_at = ? WHERE album = ?",
                        (added_at, album),
                    )

            api = LibraryQueries(database)
            default_items = api.list_album_page(AlbumListQuery()).items
            albums_by_title = api.list_album_page(
                AlbumListQuery(sort=ALBUM_LIST_SORT_ALBUMS)
            ).items
            recently_added = api.list_album_page(
                AlbumListQuery(sort=ALBUM_LIST_SORT_RECENTLY_ADDED)
            ).items
            with connect_database(database) as connection:
                connection.execute(
                    """
                    UPDATE library_albums
                    SET starred_at = ?
                    WHERE album = ?
                    """,
                    ("2026-05-01T12:00:00Z", "AAA Later"),
                )
                connection.execute(
                    """
                    UPDATE library_albums
                    SET starred_at = ?
                    WHERE album = ?
                    """,
                    ("2026-05-02T12:00:00Z", "Old"),
                )
            starred = api.list_album_page(
                AlbumListQuery(sort=ALBUM_LIST_SORT_STARRED)
            ).items
            playlists = api.list_album_page(AlbumListQuery(is_playlist=True)).items

        self.assertEqual(
            [item.album for item in default_items],
            ["ZZZ Original", "AAA Later", "No Date", "Old"],
        )
        self.assertEqual(
            [item.album for item in albums_by_title],
            ["AAA Later", "No Date", "Old", "ZZZ Original"],
        )
        self.assertEqual(
            [item.album for item in recently_added],
            ["Old", "ZZZ Original", "AAA Later", "No Date"],
        )
        self.assertEqual(len(recently_added), 4)
        self.assertEqual([item.album for item in starred], ["Old", "AAA Later"])
        self.assertEqual(
            [item.starred_at for item in starred],
            ["2026-05-02T12:00:00Z", "2026-05-01T12:00:00Z"],
        )
        self.assertEqual([item.album for item in playlists], ["Recent Mix"])

    def test_list_album_page_recent_sort_filters_to_played_albums(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            save_library(
                MusicLibrary(
                    roots=[],
                    tracks=[
                        TrackRecord(
                            path="/music/Alpha/First/01.flac",
                            file_type="flac",
                            artist="Alpha",
                            album_artist="Alpha",
                            album="First",
                            title="Song",
                        ),
                        TrackRecord(
                            path="/music/Beta/Second/01.flac",
                            file_type="flac",
                            artist="Beta",
                            album_artist="Beta",
                            album="Second",
                            title="Song",
                        ),
                        TrackRecord(
                            path="/music/Gamma/Third/01.flac",
                            file_type="flac",
                            artist="Gamma",
                            album_artist="Gamma",
                            album="Third",
                            title="Song",
                        ),
                        TrackRecord(
                            path="/music/Delta/Fourth/01.flac",
                            file_type="flac",
                            artist="Delta",
                            album_artist="Delta",
                            album="Fourth",
                            title="Song",
                        ),
                        TrackRecord(
                            path="/music/Epsilon/Unplayed/01.flac",
                            file_type="flac",
                            artist="Epsilon",
                            album_artist="Epsilon",
                            album="Unplayed",
                            title="Song",
                        ),
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-05-01T00:00:00+00:00",
                ),
                database,
            )
            with connect_database(database) as connection:
                album_ids = {
                    str(row["album"]): str(row["album_id"])
                    for row in connection.execute(
                        "SELECT album_id, album FROM library_albums"
                    )
                }
                for album, play_count, last_played_at in (
                    ("First", 1, "2026-05-10T12:00:00+00:00"),
                    ("Second", 1, "2026-05-11T12:00:00+00:00"),
                    ("Third", 5, "2026-05-11T12:00:00+00:00"),
                    ("Fourth", 1, "2026-05-11T12:00:00+00:00"),
                ):
                    connection.execute(
                        """
                        INSERT INTO play_album_stats (
                            album_id,
                            play_count,
                            last_played_at
                        )
                        VALUES (?, ?, ?)
                        """,
                        (album_ids[album], play_count, last_played_at),
                    )

            recent = LibraryQueries(database).list_album_page(
                AlbumListQuery(sort=ALBUM_LIST_SORT_RECENT)
            )

        self.assertEqual(
            [item.album for item in recent.items],
            ["Third", "Second", "Fourth", "First"],
        )
        self.assertNotIn("Unplayed", [item.album for item in recent.items])

    def test_list_album_page_frequent_sort_filters_to_played_albums(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            save_library(
                MusicLibrary(
                    roots=[],
                    tracks=[
                        TrackRecord(
                            path="/music/Alpha/First/01.flac",
                            file_type="flac",
                            artist="Alpha",
                            album_artist="Alpha",
                            album="First",
                            title="Song",
                        ),
                        TrackRecord(
                            path="/music/Beta/Second/01.flac",
                            file_type="flac",
                            artist="Beta",
                            album_artist="Beta",
                            album="Second",
                            title="Song",
                        ),
                        TrackRecord(
                            path="/music/Gamma/Third/01.flac",
                            file_type="flac",
                            artist="Gamma",
                            album_artist="Gamma",
                            album="Third",
                            title="Song",
                        ),
                        TrackRecord(
                            path="/music/Delta/Fourth/01.flac",
                            file_type="flac",
                            artist="Delta",
                            album_artist="Delta",
                            album="Fourth",
                            title="Song",
                        ),
                        TrackRecord(
                            path="/music/Epsilon/Unplayed/01.flac",
                            file_type="flac",
                            artist="Epsilon",
                            album_artist="Epsilon",
                            album="Unplayed",
                            title="Song",
                        ),
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-05-01T00:00:00+00:00",
                ),
                database,
            )
            with connect_database(database) as connection:
                album_ids = {
                    str(row["album"]): str(row["album_id"])
                    for row in connection.execute(
                        "SELECT album_id, album FROM library_albums"
                    )
                }
                for album, play_count, last_played_at in (
                    ("First", 1, "2026-05-12T12:00:00+00:00"),
                    ("Second", 5, "2026-05-10T12:00:00+00:00"),
                    ("Third", 5, "2026-05-11T12:00:00+00:00"),
                    ("Fourth", 5, "2026-05-11T12:00:00+00:00"),
                ):
                    connection.execute(
                        """
                        INSERT INTO play_album_stats (
                            album_id,
                            play_count,
                            last_played_at
                        )
                        VALUES (?, ?, ?)
                        """,
                        (album_ids[album], play_count, last_played_at),
                    )

            frequent = LibraryQueries(database).list_album_page(
                AlbumListQuery(sort=ALBUM_LIST_SORT_FREQUENT)
            )

        tied_albums = sorted(
            ("Third", "Fourth"),
            key=lambda album: album_ids[album],
        )
        self.assertEqual(
            [item.album for item in frequent.items],
            [*tied_albums, "Second", "First"],
        )
        self.assertNotIn("Unplayed", [item.album for item in frequent.items])

    def test_save_library_preserves_album_added_at_and_stamps_new_album_ids(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            with patch(
                "kukicha.use_case.library.utc_now_iso",
                return_value="2026-05-01T12:00:00+00:00",
            ):
                save_library(
                    MusicLibrary(
                        roots=[],
                        tracks=[
                            TrackRecord(
                                path="/music/Artist/Album/01.flac",
                                file_created_at="2026-04-01T12:00:00+00:00",
                                file_type="flac",
                                artist="Artist",
                                album_artist="Artist",
                                album="Album",
                                title="Track",
                            ),
                        ],
                        supported_extensions=[".flac"],
                        generated_at="2026-05-01T12:00:00+00:00",
                    ),
                    database,
                )
            with patch(
                "kukicha.use_case.library.utc_now_iso",
                return_value="2026-05-02T12:00:00+00:00",
            ):
                save_library(
                    MusicLibrary(
                        roots=[],
                        tracks=[
                            TrackRecord(
                                path="/music/Artist/Album/01.flac",
                                file_created_at="2026-04-10T12:00:00+00:00",
                                file_type="flac",
                                artist="Artist",
                                album_artist="Artist",
                                album="Album",
                                title="Track",
                            ),
                            TrackRecord(
                                path="/music/Artist/New Album/01.flac",
                                file_created_at="2025-01-01T12:00:00+00:00",
                                file_type="flac",
                                artist="Artist",
                                album_artist="Artist",
                                album="New Album",
                                title="New Track",
                            ),
                        ],
                        supported_extensions=[".flac"],
                        generated_at="2026-05-02T12:00:00+00:00",
                    ),
                    database,
                )
            with connect_database(database) as connection:
                rows = connection.execute(
                    """
                    SELECT album, file_created_at, added_at
                    FROM library_albums
                    ORDER BY album
                    """
                ).fetchall()

        self.assertEqual(
            [
                (str(row["album"]), str(row["file_created_at"]), str(row["added_at"]))
                for row in rows
            ],
            [
                (
                    "Album",
                    "2026-04-10T12:00:00+00:00",
                    "2026-05-01T12:00:00+00:00",
                ),
                (
                    "New Album",
                    "2025-01-01T12:00:00+00:00",
                    "2026-05-02T12:00:00+00:00",
                ),
            ],
        )

    def test_save_library_preserves_starred_at_for_stable_album_ids(self) -> None:
        library = MusicLibrary(
            roots=[],
            tracks=[
                TrackRecord(
                    path="/music/Artist/Album/01.flac",
                    file_type="flac",
                    artist="Artist",
                    album_artist="Artist",
                    album="Album",
                    title="First Track",
                )
            ],
            supported_extensions=[".flac"],
            generated_at="2026-04-25T00:00:00+00:00",
        )
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            save_library(library, database)
            with connect_database(database) as connection:
                connection.execute(
                    """
                    UPDATE library_albums
                    SET starred_at = ?
                    WHERE album = ?
                    """,
                    ("2026-05-01T12:00:00Z", "Album"),
                )

            save_library(library, database)
            album = LibraryQueries(database).list_album_page(
                AlbumListQuery(sort=ALBUM_LIST_SORT_STARRED)
            ).items[0]

        self.assertEqual(album.album, "Album")
        self.assertEqual(album.starred_at, "2026-05-01T12:00:00Z")

    def test_save_library_reattaches_album_star_after_library_clear(self) -> None:
        first_library = MusicLibrary(
            roots=[],
            tracks=[
                TrackRecord(
                    path="/music/Artist/Album/01.flac",
                    file_type="flac",
                    artist="Artist",
                    album_artist="Artist",
                    album="Album",
                    title="First Track",
                )
            ],
            supported_extensions=[".flac"],
            generated_at="2026-04-25T00:00:00+00:00",
        )
        rescanned_library = MusicLibrary(
            roots=[],
            tracks=[
                TrackRecord(
                    path="/archive/Artist/Album/01.flac",
                    file_type="flac",
                    artist="Artist",
                    album_artist="Artist",
                    album="Album",
                    title="First Track",
                )
            ],
            supported_extensions=[".flac"],
            generated_at="2026-04-26T00:00:00+00:00",
        )
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            save_library(first_library, database)
            with connect_database(database) as connection:
                connection.execute(
                    """
                    INSERT INTO album_user_state (album_id, starred_at)
                    VALUES (?, ?)
                    """,
                    ("artist::album", "2026-05-01T12:00:00Z"),
                )
                clear_library(connection)

            save_library(rescanned_library, database)
            album = LibraryQueries(database).list_album_page(
                AlbumListQuery(sort=ALBUM_LIST_SORT_STARRED)
            ).items[0]

        self.assertEqual(album.album, "Album")
        self.assertEqual(album.starred_at, "2026-05-01T12:00:00Z")

    def test_list_album_page_can_sort_by_genre_then_artist_year_album(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            save_library(
                MusicLibrary(
                    roots=[],
                    tracks=[
                        TrackRecord(
                            path="/music/Zed/Multi Genre/01.flac",
                            file_type="flac",
                            artist="Zed",
                            album_artist="Zed",
                            album="Multi Genre",
                            title="Multi Genre Track",
                            date="2020",
                            genres=["Rock", "Ambient"],
                        ),
                        TrackRecord(
                            path="/music/Beta/AAA Later/01.flac",
                            file_type="flac",
                            artist="Beta",
                            album_artist="Beta",
                            album="AAA Later",
                            title="Later Track",
                            date="2001",
                            genres=["Jazz"],
                        ),
                        TrackRecord(
                            path="/music/Beta/ZZZ Earlier/01.flac",
                            file_type="flac",
                            artist="Beta",
                            album_artist="Beta",
                            album="ZZZ Earlier",
                            title="Earlier Track",
                            date="1984",
                            genres=["Jazz"],
                        ),
                        TrackRecord(
                            path="/music/Alpha/Rock Album/01.flac",
                            file_type="flac",
                            artist="Alpha",
                            album_artist="Alpha",
                            album="Rock Album",
                            title="Rock Track",
                            date="1990",
                            genres=["Rock"],
                        ),
                    ],
                    playlists=[
                        PlaylistRecord(
                            path="/music/mix.m3u8",
                            name="Mix",
                            created_at="2026-04-25T12:00:00+00:00",
                        )
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-04-25T00:00:00+00:00",
                ),
                database,
            )

            api = LibraryQueries(database)
            genre_sorted = api.list_album_page(AlbumListQuery(sort="genre")).items
            playlists = api.list_album_page(
                AlbumListQuery(sort="genre", is_playlist=True)
            ).items

        self.assertEqual(
            [item.album for item in genre_sorted],
            ["Multi Genre", "ZZZ Earlier", "AAA Later", "Rock Album"],
        )
        self.assertEqual([item.album for item in playlists], ["Mix"])

    def test_list_album_page_genre_sort_uses_root_scoped_genres(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            save_library(
                MusicLibrary(
                    roots=["/music/a", "/music/b"],
                    tracks=[
                        TrackRecord(
                            path="/music/a/Shared/01.flac",
                            root_position=0,
                            file_type="flac",
                            artist="Root Alpha",
                            album_artist="Root Alpha",
                            album="Shared",
                            title="Root A Track",
                            genres=["Ambient"],
                        ),
                        TrackRecord(
                            path="/music/b/Shared/01.flac",
                            root_position=1,
                            file_type="flac",
                            artist="Root Alpha",
                            album_artist="Root Alpha",
                            album="Shared",
                            title="Root B Track",
                            genres=["Zzz"],
                        ),
                        TrackRecord(
                            path="/music/b/Classical/01.flac",
                            root_position=1,
                            file_type="flac",
                            artist="Root Beta",
                            album_artist="Root Beta",
                            album="Classical",
                            title="Classical Track",
                            genres=["Classical"],
                        ),
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-04-25T00:00:00+00:00",
                ),
                database,
            )

            genre_sorted = LibraryQueries(database).list_album_page(
                AlbumListQuery(root_positions=(1,), sort="genre")
            ).items

        self.assertEqual(
            [item.album for item in genre_sorted],
            ["Classical", "Shared"],
        )

    def test_save_library_stores_playlists_and_links_tracked_items_by_path(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            tracked_path = "/music/Artist/Album/01.flac"
            external_path = "/outside/External.m4a"
            save_library(
                MusicLibrary(
                    roots=["/music"],
                    tracks=[
                        TrackRecord(
                            path=tracked_path,
                            root_position=0,
                            file_type="flac",
                            artist="Artist",
                            album_artist="Artist",
                            album="Album",
                            title="Tracked",
                        )
                    ],
                    playlists=[
                        PlaylistRecord(
                            path="/music/lists/mixed.m3u8",
                            root_position=0,
                            name="Mixed",
                            items=[
                                PlaylistItemRecord(path=tracked_path),
                                PlaylistItemRecord(
                                    path=external_path,
                                    title="External",
                                    duration_seconds=91.0,
                                    genre="Electronic",
                                    cover_url="https://example.test/cover.jpg",
                                ),
                            ],
                        )
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-04-25T00:00:00+00:00",
                ),
                database,
            )

            playlist = LibraryQueries(database).get_playlist(1)

        self.assertEqual(playlist.name, "Mixed")
        self.assertIn("Mixed", playlist.cover_svg)
        self.assertEqual(len(playlist.items), 2)
        self.assertEqual(playlist.items[0].path, tracked_path)
        self.assertEqual(playlist.items[0].track_id, 1)
        self.assertIsNotNone(playlist.items[0].track)
        self.assertEqual(playlist.items[0].track.title, "Tracked")
        self.assertEqual(playlist.items[1].path, external_path)
        self.assertIsNone(playlist.items[1].track_id)
        self.assertEqual(playlist.items[1].title, "External")
        self.assertEqual(playlist.items[1].duration_seconds, 91.0)
        self.assertFalse(playlist.items[1].duration_is_indeterminate)
        self.assertEqual(playlist.items[1].genre, "Electronic")
        self.assertEqual(playlist.items[1].cover_url, "https://example.test/cover.jpg")

    def test_load_rescan_tracks_by_path_keeps_reusable_track_state_only(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            tracked_path = "/music/Artist/Album/01.flac"
            save_library(
                MusicLibrary(
                    roots=["/music"],
                    tracks=[
                        TrackRecord(
                            path=tracked_path,
                            root_position=0,
                            file_type="flac",
                            artist="Artist",
                            album_artist="Artist",
                            album="Album",
                            title="Tracked",
                            genres=["Electronic"],
                            album_artwork=TrackArtwork(
                                mime_type="image/png",
                                data=b"cover",
                            ),
                        )
                    ],
                    playlists=[
                        PlaylistRecord(
                            path="/music/lists/mixed.m3u8",
                            root_position=0,
                            name="Mixed",
                            items=[PlaylistItemRecord(path=tracked_path)],
                        )
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-04-25T00:00:00+00:00",
                ),
                database,
            )

            tracks_by_path = load_rescan_tracks_by_path(database)

        self.assertEqual(list(tracks_by_path), [tracked_path])
        track = tracks_by_path[tracked_path]
        self.assertEqual(track.title, "Tracked")
        self.assertEqual(track.genres, ["Electronic"])
        self.assertEqual(track.track_id, 1)
        self.assertIsNotNone(track.source)
        self.assertEqual(track.source.source_kind, "local")
        self.assertEqual(track.album_artists, ())
        self.assertFalse(track.has_cover)
        self.assertIsNone(track.album_artwork)

    def test_playlist_items_preserve_indeterminate_duration(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            save_library(
                MusicLibrary(
                    roots=[],
                    tracks=[],
                    playlists=[
                        PlaylistRecord(
                            path="/music/streams.m3u8",
                            name="Streams",
                            items=[
                                PlaylistItemRecord(
                                    path="https://example.test/live",
                                    title="Live",
                                    duration_seconds=0.0,
                                    duration_is_indeterminate=True,
                                )
                            ],
                        )
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-04-25T00:00:00+00:00",
                ),
                database,
            )

            playlist = LibraryQueries(database).get_playlist(1)
            with connect_database(database, create=False) as connection:
                row = connection.execute(
                    """
                    SELECT duration_seconds, duration_is_indeterminate
                    FROM library_playlist_items
                    """
                ).fetchone()

        item = playlist.items[0]
        self.assertIsNone(item.duration_seconds)
        self.assertTrue(item.duration_is_indeterminate)
        self.assertIsNone(row["duration_seconds"])
        self.assertEqual(int(row["duration_is_indeterminate"]), 1)

    def test_list_album_page_separates_album_and_playlist_items(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            save_library(
                MusicLibrary(
                    roots=[],
                    tracks=[
                        TrackRecord(
                            path="/music/Artist/Album/01.flac",
                            file_type="flac",
                            artist="Artist",
                            album_artist="Artist",
                            album="Album",
                            title="Track",
                        )
                    ],
                    playlists=[
                        PlaylistRecord(
                            path="/music/list.m3u8",
                            name="Road Mix",
                            items=[
                                PlaylistItemRecord(
                                    path="https://example.test/stream",
                                    title="Stream",
                                )
                            ],
                        )
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-04-25T00:00:00+00:00",
                ),
                database,
            )

            api = LibraryQueries(database)
            default_items = api.list_album_page(AlbumListQuery()).items
            playlists = api.list_album_page(AlbumListQuery(is_playlist=True)).items
            albums = api.list_album_page(AlbumListQuery(is_playlist=False)).items

        self.assertEqual([item.album for item in default_items], ["Album"])
        self.assertEqual([item.album for item in playlists], ["Road Mix"])
        self.assertTrue(playlists[0].is_playlist)
        self.assertEqual(playlists[0].playlist_id, 1)
        self.assertIn("Road Mix", playlists[0].cover_svg)
        self.assertEqual([item.album for item in albums], ["Album"])
        self.assertFalse(albums[0].is_playlist)


class LibraryAlbumOffsetPaginationTest(unittest.TestCase):
    def test_list_album_page_uses_offset_pagination_for_each_album_sort(self) -> None:
        expected_by_sort = {
            ALBUM_LIST_SORT_RECENTLY_ADDED: [
                "Beta New",
                "Alpha Original",
                "Alpha Later",
                "Alpha No Date",
                "Zulu Old",
            ],
            ALBUM_LIST_SORT_ARTIST: [
                "Alpha Original",
                "Alpha Later",
                "Alpha No Date",
                "Beta New",
                "Zulu Old",
            ],
            ALBUM_LIST_SORT_ALBUMS: [
                "Alpha Later",
                "Alpha No Date",
                "Alpha Original",
                "Beta New",
                "Zulu Old",
            ],
            ALBUM_LIST_SORT_GENRE: [
                "Alpha No Date",
                "Beta New",
                "Alpha Original",
                "Alpha Later",
                "Zulu Old",
            ],
            ALBUM_LIST_SORT_STARRED: [
                "Beta New",
                "Zulu Old",
                "Alpha Original",
                "Alpha Later",
                "Alpha No Date",
            ],
        }

        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            save_library(album_offset_library(), database)
            with connect_database(database) as connection:
                for album, added_at in (
                    ("Beta New", "2026-04-26T12:00:00+00:00"),
                    ("Alpha Original", "2026-04-24T12:00:00+00:00"),
                    ("Alpha Later", "2026-04-24T12:00:00+00:00"),
                    ("Alpha No Date", "2026-04-24T12:00:00+00:00"),
                    ("Zulu Old", "2026-04-20T12:00:00+00:00"),
                ):
                    connection.execute(
                        "UPDATE library_albums SET added_at = ? WHERE album = ?",
                        (added_at, album),
                    )
                for album, starred_at in (
                    ("Beta New", "2026-05-05T12:00:00Z"),
                    ("Zulu Old", "2026-05-04T12:00:00Z"),
                    ("Alpha Original", "2026-05-03T12:00:00Z"),
                    ("Alpha Later", "2026-05-02T12:00:00Z"),
                    ("Alpha No Date", "2026-05-01T12:00:00Z"),
                ):
                    connection.execute(
                        "UPDATE library_albums SET starred_at = ? WHERE album = ?",
                        (starred_at, album),
                    )
            api = LibraryQueries(database)

            for sort, expected in expected_by_sort.items():
                with self.subTest(sort=sort):
                    first = api.list_album_page(AlbumListQuery(sort=sort, size=2))
                    second = api.list_album_page(
                        AlbumListQuery(
                            sort=sort,
                            size=2,
                            offset=2,
                        )
                    )
                    third = api.list_album_page(
                        AlbumListQuery(
                            sort=sort,
                            size=2,
                            offset=4,
                        )
                    )
                    beyond_last = api.list_album_page(
                        AlbumListQuery(
                            sort=sort,
                            size=2,
                            offset=6,
                        )
                    )

                    self.assertEqual(
                        [item.album for item in (*first.items, *second.items, *third.items)],
                        expected,
                    )
                    self.assertFalse(first.has_previous)
                    self.assertTrue(first.has_next)
                    self.assertEqual(first.size, 2)
                    self.assertEqual(first.offset, 0)
                    self.assertTrue(second.has_previous)
                    self.assertTrue(second.has_next)
                    self.assertEqual(second.size, 2)
                    self.assertEqual(second.offset, 2)
                    self.assertTrue(third.has_previous)
                    self.assertFalse(third.has_next)
                    self.assertEqual(third.size, 2)
                    self.assertEqual(third.offset, 4)
                    self.assertEqual([item.album for item in beyond_last.items], [])
                    self.assertTrue(beyond_last.has_previous)
                    self.assertFalse(beyond_last.has_next)

    def test_album_offset_pagination_respects_search_root_and_genre_filters(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            save_library(
                MusicLibrary(
                    roots=["/music/a", "/music/b"],
                    tracks=[
                        TrackRecord(
                            path="/music/a/Alpha/Ambient One/01.flac",
                            root_position=0,
                            file_type="flac",
                            artist="Alpha",
                            album_artist="Alpha",
                            album="Ambient One",
                            title="First",
                            genres=["Ambient"],
                            album_artwork=TrackArtwork(mime_type="image/png", data=b"cover"),
                        ),
                        TrackRecord(
                            path="/music/a/Alpha/Ambient Two/01.flac",
                            root_position=0,
                            file_type="flac",
                            artist="Alpha",
                            album_artist="Alpha",
                            album="Ambient Two",
                            title="Second",
                            genres=["Ambient"],
                            album_artwork=TrackArtwork(mime_type="image/png", data=b"cover"),
                        ),
                        TrackRecord(
                            path="/music/b/Alpha/Ambient Three/01.flac",
                            root_position=1,
                            file_type="flac",
                            artist="Alpha",
                            album_artist="Alpha",
                            album="Ambient Three",
                            title="Third",
                            genres=["Ambient"],
                        ),
                        TrackRecord(
                            path="/music/a/Alpha/Jazz One/01.flac",
                            root_position=0,
                            file_type="flac",
                            artist="Alpha",
                            album_artist="Alpha",
                            album="Jazz One",
                            title="Fourth",
                            genres=["Jazz"],
                            album_artwork=TrackArtwork(mime_type="image/png", data=b"cover"),
                        ),
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-04-29T00:00:00+00:00",
                ),
                database,
            )
            api = LibraryQueries(database)

            search_first = api.list_album_page(
                AlbumListQuery(search="Ambient", sort=ALBUM_LIST_SORT_ARTIST, size=2)
            )
            search_second = api.list_album_page(
                AlbumListQuery(
                    search="Ambient",
                    sort=ALBUM_LIST_SORT_ARTIST,
                    size=2,
                    offset=2,
                )
            )
            filtered_first = api.list_album_page(
                AlbumListQuery(
                    root_positions=(0,),
                    genre_filters=(GenreStyleFilter(genre="Ambient"),),
                    sort=ALBUM_LIST_SORT_GENRE,
                    size=1,
                )
            )
            filtered_second = api.list_album_page(
                AlbumListQuery(
                    root_positions=(0,),
                    genre_filters=(GenreStyleFilter(genre="Ambient"),),
                    sort=ALBUM_LIST_SORT_GENRE,
                    size=1,
                    offset=1,
                )
            )

        self.assertEqual(
            [item.album for item in (*search_first.items, *search_second.items)],
            ["Ambient One", "Ambient Three", "Ambient Two"],
        )
        self.assertEqual(
            [item.album for item in (*filtered_first.items, *filtered_second.items)],
            ["Ambient One", "Ambient Two"],
        )

    def test_album_sort_key_columns_and_indexes_are_created_and_populated(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            save_library(album_offset_library(), database)
            connection = connect_database(database)
            try:
                album_columns = {
                    str(row["name"])
                    for row in connection.execute("PRAGMA table_info(library_albums)")
                }
                root_columns = {
                    str(row["name"])
                    for row in connection.execute("PRAGMA table_info(library_album_roots)")
                }
                album_indexes = {
                    str(row["name"])
                    for row in connection.execute("PRAGMA index_list(library_albums)")
                }
                starred_index_row = connection.execute(
                    """
                    SELECT sql
                    FROM sqlite_master
                    WHERE type = 'index'
                        AND name = 'idx_library_albums_starred_sort'
                    """
                ).fetchone()
                recently_added_index_row = connection.execute(
                    """
                    SELECT sql
                    FROM sqlite_master
                    WHERE type = 'index'
                        AND name = 'idx_library_albums_recently_added_sort'
                    """
                ).fetchone()
                album_sort_index_row = connection.execute(
                    """
                    SELECT sql
                    FROM sqlite_master
                    WHERE type = 'index'
                        AND name = 'idx_library_albums_album_sort'
                    """
                ).fetchone()
                recent_listening_index_row = connection.execute(
                    """
                    SELECT sql
                    FROM sqlite_master
                    WHERE type = 'index'
                        AND name = 'idx_play_album_stats_recent'
                    """
                ).fetchone()
                frequent_listening_index_row = connection.execute(
                    """
                    SELECT sql
                    FROM sqlite_master
                    WHERE type = 'index'
                        AND name = 'idx_play_album_stats_frequent'
                    """
                ).fetchone()
                root_indexes = {
                    str(row["name"])
                    for row in connection.execute("PRAGMA index_list(library_album_roots)")
                }
                album_row = connection.execute(
                    """
                    SELECT artist_sort_key, album_sort_key, genre_sort_key
                    FROM library_albums
                    WHERE album = ?
                    """,
                    ("Alpha Original",),
                ).fetchone()
                root_row = connection.execute(
                    """
                    SELECT genre_sort_key
                    FROM library_album_roots
                    WHERE album_id = ? AND root_position = 0
                    """,
                    ("alpha::alpha-original",),
                ).fetchone()
            finally:
                connection.close()

        self.assertLessEqual(
            {"artist_sort_key", "album_sort_key", "genre_sort_key", "starred_at", "added_at"},
            album_columns,
        )
        self.assertTrue(
            {"has_cover", "is_compilation", "is_work"}.isdisjoint(album_columns)
        )
        self.assertIn("genre_sort_key", root_columns)
        self.assertTrue(
            {"has_cover", "is_compilation", "is_work"}.isdisjoint(root_columns)
        )
        self.assertLessEqual(
            {
                "idx_library_albums_recently_added_sort",
                "idx_library_albums_added_at",
                "idx_library_albums_artist_sort",
                "idx_library_albums_album_sort",
                "idx_library_albums_genre_sort",
                "idx_library_albums_starred_sort",
            },
            album_indexes,
        )
        self.assertIsNotNone(starred_index_row)
        self.assertIsNotNone(recently_added_index_row)
        self.assertIsNotNone(album_sort_index_row)
        self.assertIsNotNone(recent_listening_index_row)
        self.assertIsNotNone(frequent_listening_index_row)
        self.assertIn("added_at DESC", str(recently_added_index_row["sql"]))
        self.assertIn("album_sort_key", str(album_sort_index_row["sql"]))
        self.assertIn("last_played_at DESC", str(recent_listening_index_row["sql"]))
        self.assertIn(
            "WHERE album_id IS NOT NULL AND album_id != ''",
            str(recent_listening_index_row["sql"]),
        )
        self.assertIn("play_count DESC", str(frequent_listening_index_row["sql"]))
        self.assertIn("last_played_at DESC", str(frequent_listening_index_row["sql"]))
        self.assertIn(
            "(play_count DESC, last_played_at DESC, album_id)",
            str(frequent_listening_index_row["sql"]),
        )
        self.assertIn(
            "WHERE album_id IS NOT NULL AND album_id != ''",
            str(frequent_listening_index_row["sql"]),
        )
        self.assertIn(
            "WHERE starred_at IS NOT NULL",
            str(starred_index_row["sql"]),
        )
        self.assertTrue(
            {
                "idx_library_albums_has_cover",
                "idx_library_albums_is_compilation",
                "idx_library_albums_is_work",
            }.isdisjoint(album_indexes)
        )
        self.assertIn("idx_library_album_roots_genre_sort", root_indexes)
        self.assertIsNotNone(album_row)
        self.assertEqual(str(album_row["artist_sort_key"]), "alpha")
        self.assertEqual(str(album_row["album_sort_key"]), "alpha original")
        self.assertEqual(str(album_row["genre_sort_key"]), "jazz")
        self.assertIsNotNone(root_row)
        self.assertEqual(str(root_row["genre_sort_key"]), "jazz")


def album_offset_library() -> MusicLibrary:
    return MusicLibrary(
        roots=["/music"],
        tracks=[
            TrackRecord(
                path="/music/Zulu/Old/01.flac",
                root_position=0,
                file_created_at="2026-04-20T12:00:00+00:00",
                file_type="flac",
                artist="Zulu",
                album_artist="Zulu",
                album="Zulu Old",
                title="Old Track",
                date="1970",
                genres=["Rock"],
            ),
            TrackRecord(
                path="/music/Alpha/Later/01.flac",
                root_position=0,
                file_created_at="2026-04-24T12:00:00+00:00",
                file_type="flac",
                artist="Alpha",
                album_artist="Alpha",
                album="Alpha Later",
                title="Later Track",
                date="2001",
                genres=["Jazz"],
            ),
            TrackRecord(
                path="/music/Alpha/Original/01.flac",
                root_position=0,
                file_created_at="2026-04-24T12:00:00+00:00",
                file_type="flac",
                artist="Alpha",
                album_artist="Alpha",
                album="Alpha Original",
                title="Original Track",
                date="1984",
                genres=["Jazz"],
            ),
            TrackRecord(
                path="/music/Alpha/No Date/01.flac",
                root_position=0,
                file_created_at="2026-04-24T12:00:00+00:00",
                file_type="flac",
                artist="Alpha",
                album_artist="Alpha",
                album="Alpha No Date",
                title="Undated Track",
                genres=["Ambient"],
            ),
            TrackRecord(
                path="/music/Beta/New/01.flac",
                root_position=0,
                file_created_at="2026-04-26T12:00:00+00:00",
                file_type="flac",
                artist="Beta",
                album_artist="Beta",
                album="Beta New",
                title="New Track",
                date="2020",
                genres=["Electronic"],
            ),
        ],
        supported_extensions=[".flac"],
        generated_at="2026-04-29T00:00:00+00:00",
    )


class LibraryGenreResolutionTest(unittest.TestCase):
    def test_resolve_library_genres_sets_unknown_when_audio_has_no_genre_data(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            connection = connect_database(database)
            try:
                library = MusicLibrary(
                    roots=[],
                    tracks=[
                        TrackRecord(
                            path="/music/Artist/Album/01.flac",
                            album_artist="Artist",
                            album="Album",
                            title="Track",
                            genres=[],
                        )
                    ],
                    supported_extensions=[],
                    generated_at="2026-04-23T00:00:00+00:00",
                )

                stats = resolve_library_genres(library, database, connection=connection)
            finally:
                connection.close()

        self.assertEqual(library.tracks[0].genres, [UNKNOWN_GENRE_TAG])
        self.assertEqual(library.tracks[0].styles, [])
        self.assertEqual(stats.unmatched, 0)
        self.assertEqual(stats.unknown_albums, 1)
        self.assertEqual(stats.unknown_tracks, 1)

    def test_resolve_library_genres_treats_explicit_unknown_as_missing_marker(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            connection = connect_database(database)
            try:
                library = MusicLibrary(
                    roots=[],
                    tracks=[
                        TrackRecord(
                            path="/music/Artist/Album/01.flac",
                            album_artist="Artist",
                            album="Album",
                            title="Track",
                            genres=[UNKNOWN_GENRE_TAG],
                        )
                    ],
                    supported_extensions=[],
                    generated_at="2026-04-23T00:00:00+00:00",
                )

                stats = resolve_library_genres(library, database, connection=connection)
            finally:
                connection.close()

        self.assertEqual(library.tracks[0].genres, [UNKNOWN_GENRE_TAG])
        self.assertEqual(library.tracks[0].styles, [])
        self.assertEqual(stats.unmatched, 0)
        self.assertEqual(stats.unknown_albums, 1)
        self.assertEqual(stats.unknown_tracks, 1)

    def test_resolve_library_genres_ignores_unknown_marker_when_real_genre_matches(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            connection = connect_database(database)
            try:
                library = MusicLibrary(
                    roots=[],
                    tracks=[
                        TrackRecord(
                            path="/music/Artist/Album/01.flac",
                            album_artist="Artist",
                            album="Album",
                            title="One",
                            genres=[UNKNOWN_GENRE_TAG],
                        ),
                        TrackRecord(
                            path="/music/Artist/Album/02.flac",
                            album_artist="Artist",
                            album="Album",
                            title="Two",
                            genres=[UNKNOWN_GENRE_TAG, "Electronic"],
                        ),
                    ],
                    supported_extensions=[],
                    generated_at="2026-04-23T00:00:00+00:00",
                )

                stats = resolve_library_genres(library, database, connection=connection)
            finally:
                connection.close()

        self.assertEqual(library.tracks[0].genres, ["Electronic"])
        self.assertEqual(library.tracks[1].genres, ["Electronic"])
        self.assertEqual(stats.exact_genre_matches, 1)
        self.assertEqual(stats.unmatched, 0)
        self.assertEqual(stats.unknown_albums, 0)
        self.assertEqual(stats.unknown_tracks, 0)

    def test_resolve_library_genres_uses_supplied_connection_for_taxonomy(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            connection = connect_database(database)
            try:
                library = MusicLibrary(
                    roots=[],
                    tracks=[
                        TrackRecord(
                            path="/music/track.flac",
                            artist="Artist",
                            album_artist="Artist",
                            album="Album",
                            title="Track",
                            genres=["Electronic"],
                        )
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-04-29T00:00:00+00:00",
                )

                with patch(
                    "kukicha.use_case.library.connect_database",
                    side_effect=AssertionError("unexpected nested connection"),
                ):
                    stats = resolve_library_genres(
                        library,
                        database,
                        connection=connection,
                    )
            finally:
                connection.close()

        self.assertEqual(stats.exact_genre_matches, 1)

    def test_musicbrainz_empty_genres_fall_back_to_audio_tags(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            connection = connect_database(database)
            try:
                connection.execute(
                    """
                    INSERT INTO album_musicbrainz_links (
                        file_album_id, release_mbid, release_group_mbid
                    ) VALUES (?, ?, ?)
                    """,
                    ("artist::album", None, "11111111-1111-1111-1111-111111111111"),
                )
                connection.commit()

                library = MusicLibrary(
                    roots=[],
                    tracks=[
                        TrackRecord(
                            path="/music/Artist/Album/01.flac",
                            album_artist="Artist",
                            album="Album",
                            title="Track",
                            genres=["Electronic"],
                        )
                    ],
                    supported_extensions=[],
                    generated_at="2026-04-23T00:00:00+00:00",
                )

                with patch("kukicha.use_case.library.get_musicbrainz_entity", return_value={"genres": []}):
                    stats = resolve_library_genres(library, database, connection=connection)

                self.assertEqual(library.tracks[0].genres, ["Electronic"])
                self.assertEqual(library.tracks[0].styles, [])
                self.assertEqual(stats.musicbrainz_album_overrides, 0)
                self.assertEqual(stats.unknown_albums, 0)
            finally:
                connection.close()

    def test_musicbrainz_genres_ignore_count_one_when_stronger_choices_exist(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            connection = connect_database(database)
            try:
                connection.execute(
                    """
                    INSERT INTO album_musicbrainz_links (
                        file_album_id, release_mbid, release_group_mbid
                    ) VALUES (?, ?, ?)
                    """,
                    ("artist::album", None, "11111111-1111-1111-1111-111111111111"),
                )
                connection.commit()

                library = MusicLibrary(
                    roots=[],
                    tracks=[
                        TrackRecord(
                            path="/music/Artist/Album/01.flac",
                            album_artist="Artist",
                            album="Album",
                            title="Track",
                            genres=["Electronic"],
                        )
                    ],
                    supported_extensions=[],
                    generated_at="2026-04-23T00:00:00+00:00",
                )

                with patch(
                    "kukicha.use_case.library.get_musicbrainz_entity",
                    return_value={
                        "genres": [
                            {"name": "jazz", "count": 1},
                            {"name": "rock", "count": 7},
                        ],
                    },
                ):
                    stats = resolve_library_genres(library, database, connection=connection)

                self.assertEqual(library.tracks[0].genres, ["Rock"])
                self.assertEqual(library.tracks[0].styles, [])
                self.assertEqual(stats.musicbrainz_album_overrides, 1)
            finally:
                connection.close()

    def test_musicbrainz_genres_keep_count_one_when_they_are_the_only_choices(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            connection = connect_database(database)
            try:
                connection.execute(
                    """
                    INSERT INTO album_musicbrainz_links (
                        file_album_id, release_mbid, release_group_mbid
                    ) VALUES (?, ?, ?)
                    """,
                    ("artist::album", None, "11111111-1111-1111-1111-111111111111"),
                )
                connection.commit()

                library = MusicLibrary(
                    roots=[],
                    tracks=[
                        TrackRecord(
                            path="/music/Artist/Album/01.flac",
                            album_artist="Artist",
                            album="Album",
                            title="Track",
                            genres=["Electronic"],
                        )
                    ],
                    supported_extensions=[],
                    generated_at="2026-04-23T00:00:00+00:00",
                )

                with patch(
                    "kukicha.use_case.library.get_musicbrainz_entity",
                    return_value={"genres": [{"name": "jazz", "count": 1}]},
                ):
                    stats = resolve_library_genres(library, database, connection=connection)

                self.assertEqual(library.tracks[0].genres, ["Jazz"])
                self.assertEqual(library.tracks[0].styles, [])
                self.assertEqual(stats.musicbrainz_album_overrides, 1)
            finally:
                connection.close()


class LibraryCoverArtResolutionTest(unittest.TestCase):
    def test_cover_art_archive_caches_missing_metadata(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            connection = connect_database(database)
            try:
                stats = CoverArtArchiveStats()
                client = CoverArtArchiveClient(stats=stats)
                error = urllib.error.HTTPError(
                    "https://coverartarchive.org/release/release-1/",
                    404,
                    "Not Found",
                    hdrs=None,
                    fp=None,
                )

                with patch(
                    "kukicha.use_case.coverartarchive.urllib.request.urlopen",
                    side_effect=error,
                ):
                    payload = get_cover_art_archive_entity(
                        connection,
                        client,
                        entity_type="release",
                        mbid="release-1",
                    )

                self.assertEqual(payload, {"images": []})
                self.assertIsNone(front_image_url(payload))
                self.assertEqual(stats.metadata_api_calls, 1)
                self.assertEqual(stats.metadata_cached_calls, 0)
                self.assertEqual(stats.missing_art, 1)

                cached_stats = CoverArtArchiveStats()
                cached_client = CoverArtArchiveClient(stats=cached_stats)
                with patch(
                    "kukicha.use_case.coverartarchive.urllib.request.urlopen",
                    side_effect=AssertionError("unexpected Cover Art Archive lookup"),
                ):
                    cached_payload = get_cover_art_archive_entity(
                        connection,
                        cached_client,
                        entity_type="release",
                        mbid="release-1",
                    )

                self.assertEqual(cached_payload, {"images": []})
                self.assertEqual(cached_stats.metadata_api_calls, 0)
                self.assertEqual(cached_stats.metadata_cached_calls, 1)
                self.assertEqual(cached_stats.missing_art, 0)
            finally:
                connection.close()

    def test_get_itunes_lookup_image_caches_missing_artwork_results(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            connection = connect_database(database)
            try:
                client = ItunesLookupClient(stats=ItunesLookupStats())
                candidate = ItunesLookupCandidate(lookup_kind="album", lookup_id="440769149")

                with patch.object(
                    client,
                    "fetch_lookup",
                    return_value=({"results": [{"wrapperType": "collection"}]}, "https://itunes.apple.com/lookup?id=440769149&media=music"),
                ):
                    artwork = get_itunes_lookup_image(connection, client, candidate=candidate)

                self.assertIsNone(artwork)
                row = connection.execute(
                    """
                    SELECT result_kind, lookup_url, artwork_url, mime_type, data
                    FROM itunes_lookup_image_cache
                    WHERE cache_key = ?
                    """,
                    (candidate.cache_key,),
                ).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(str(row["result_kind"]), "missing")
                self.assertEqual(str(row["lookup_url"]), "https://itunes.apple.com/lookup?id=440769149&media=music")
                self.assertEqual(str(row["artwork_url"]), "")
                self.assertEqual(str(row["mime_type"]), "")
                self.assertEqual(bytes(row["data"]), b"")

                with patch.object(client, "fetch_lookup", side_effect=AssertionError("unexpected lookup")):
                    artwork = get_itunes_lookup_image(connection, client, candidate=candidate)

                self.assertIsNone(artwork)
                self.assertEqual(client.stats.lookup_cached_calls, 1)
            finally:
                connection.close()

    def test_resolve_library_cover_art_uses_cached_itunes_artwork_before_musicbrainz(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            connection = connect_database(database)
            try:
                connection.execute(
                    """
                    INSERT INTO album_musicbrainz_links (
                        file_album_id, release_mbid, release_group_mbid
                    ) VALUES (?, ?, ?)
                    """,
                    ("artist::album", "release-1", None),
                )
                connection.execute(
                    """
                    INSERT INTO itunes_lookup_image_cache (
                        cache_key,
                        lookup_kind,
                        lookup_id,
                        fetched_at,
                        lookup_url,
                        artwork_url,
                        mime_type,
                        data
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "album:440769149",
                        "album",
                        "440769149",
                        "2026-04-23T00:00:00+00:00",
                        "https://itunes.apple.com/lookup?id=440769149&media=music",
                        "https://is1-ssl.mzstatic.com/image/thumb/example/3000x3000bb.jpg",
                        "image/jpeg",
                        b"cached-itunes-art",
                    ),
                )
                connection.commit()

                library = MusicLibrary(
                    roots=[],
                    tracks=[
                        TrackRecord(
                            path="/music/Artist/Album/01.m4a",
                            file_type="m4a",
                            album_artist="Artist",
                            album="Album",
                            title="Track",
                            itunes_store_album_id="440769149",
                        )
                    ],
                    supported_extensions=[],
                    generated_at="2026-04-23T00:00:00+00:00",
                )
                artwork_by_height = {
                    32: TrackArtwork(mime_type="image/jpeg", data=b"track-art"),
                    250: TrackArtwork(mime_type="image/jpeg", data=b"album-art"),
                }

                with (
                    patch("kukicha.use_case.library.thumbnail_artworks", return_value=artwork_by_height),
                    patch("kukicha.use_case.library.cover_art_archive_artworks_for_album") as mb_artwork,
                ):
                    stats = resolve_library_cover_art(library, database, connection=connection)

                self.assertEqual(library.tracks[0].artwork, artwork_by_height[32])
                self.assertEqual(library.tracks[0].album_artwork, artwork_by_height[250])
                self.assertEqual(stats.itunes_lookup_api_calls, 0)
                self.assertEqual(stats.itunes_lookup_cached_calls, 1)
                self.assertEqual(stats.album_cover_overrides, 1)
                self.assertEqual(stats.tracks_updated, 1)
                mb_artwork.assert_not_called()
            finally:
                connection.close()

    def test_resolve_library_cover_art_falls_back_to_musicbrainz_when_itunes_cache_marks_missing(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            connection = connect_database(database)
            try:
                connection.execute(
                    """
                    INSERT INTO album_musicbrainz_links (
                        file_album_id, release_mbid, release_group_mbid
                    ) VALUES (?, ?, ?)
                    """,
                    ("artist::album", "release-1", None),
                )
                connection.execute(
                    """
                    INSERT INTO itunes_lookup_image_cache (
                        cache_key,
                        lookup_kind,
                        lookup_id,
                        result_kind,
                        fetched_at,
                        lookup_url,
                        artwork_url,
                        mime_type,
                        data
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "album:440769149",
                        "album",
                        "440769149",
                        "missing",
                        "2026-04-23T00:00:00+00:00",
                        "https://itunes.apple.com/lookup?id=440769149&media=music",
                        "",
                        "",
                        b"",
                    ),
                )
                connection.commit()

                library = MusicLibrary(
                    roots=[],
                    tracks=[
                        TrackRecord(
                            path="/music/Artist/Album/01.m4a",
                            file_type="m4a",
                            album_artist="Artist",
                            album="Album",
                            title="Track",
                            itunes_store_album_id="440769149",
                        )
                    ],
                    supported_extensions=[],
                    generated_at="2026-04-23T00:00:00+00:00",
                )
                mb_artwork = TrackArtwork(mime_type="image/jpeg", data=b"musicbrainz-art")
                artwork_by_height = {
                    32: TrackArtwork(mime_type="image/jpeg", data=b"track-art"),
                    250: TrackArtwork(mime_type="image/jpeg", data=b"album-art"),
                }

                with (
                    patch("kukicha.use_case.library.cover_art_archive_artworks_for_album", return_value={"release": mb_artwork}) as caa_lookup,
                    patch("kukicha.use_case.library.thumbnail_artworks", return_value=artwork_by_height),
                ):
                    stats = resolve_library_cover_art(library, database, connection=connection)

                self.assertEqual(library.tracks[0].artwork, artwork_by_height[32])
                self.assertEqual(library.tracks[0].album_artwork, artwork_by_height[250])
                self.assertEqual(stats.itunes_lookup_api_calls, 0)
                self.assertEqual(stats.itunes_lookup_cached_calls, 1)
                self.assertEqual(stats.album_cover_overrides, 1)
                self.assertEqual(stats.tracks_updated, 1)
                caa_lookup.assert_called_once()
            finally:
                connection.close()

    def test_resolve_library_cover_art_reuses_legacy_split_artist_musicbrainz_link(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            connection = connect_database(database)
            try:
                old_album_id = (
                    "academy-of-st-martin-in-the-fields-sir-neville-marriner"
                    "::handel-music-for-the-royal-fireworks-and-water-music"
                )
                new_album_id = (
                    "academy-of-st-martin-in-the-fields-and-sir-neville-marriner"
                    "::handel-music-for-the-royal-fireworks-and-water-music"
                )
                connection.execute(
                    """
                    INSERT INTO album_musicbrainz_links (
                        file_album_id, release_mbid, release_group_mbid
                    ) VALUES (?, ?, ?)
                    """,
                    (old_album_id, "release-1", "group-1"),
                )
                connection.commit()

                library = MusicLibrary(
                    roots=[],
                    tracks=[
                        TrackRecord(
                            path="/music/Academy/Handel/01.m4a",
                            file_type="m4a",
                            album_artist=(
                                "Academy of St Martin in the Fields "
                                "& Sir Neville Marriner"
                            ),
                            album="Handel: Music for the Royal Fireworks & Water Music",
                            title="Track",
                        )
                    ],
                    supported_extensions=[],
                    generated_at="2026-04-23T00:00:00+00:00",
                )
                mb_artwork = TrackArtwork(mime_type="image/jpeg", data=b"musicbrainz-art")
                artwork_by_height = {
                    32: TrackArtwork(mime_type="image/jpeg", data=b"track-art"),
                    250: TrackArtwork(mime_type="image/jpeg", data=b"album-art"),
                }

                with (
                    patch(
                        "kukicha.use_case.library.cover_art_archive_artworks_for_album",
                        return_value={"release": mb_artwork},
                    ) as caa_lookup,
                    patch(
                        "kukicha.use_case.library.thumbnail_artworks",
                        return_value=artwork_by_height,
                    ),
                ):
                    stats = resolve_library_cover_art(
                        library,
                        database,
                        connection=connection,
                    )

                self.assertEqual(library.tracks[0].artwork, artwork_by_height[32])
                self.assertEqual(library.tracks[0].album_artwork, artwork_by_height[250])
                self.assertEqual(stats.album_cover_overrides, 1)
                self.assertEqual(stats.tracks_updated, 1)
                caa_lookup.assert_called_once()
                self.assertEqual(caa_lookup.call_args.args[1].album_id, new_album_id)

                row = connection.execute(
                    """
                    SELECT release_mbid, release_group_mbid
                    FROM album_musicbrainz_links
                    WHERE file_album_id = ?
                    """,
                    (new_album_id,),
                ).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(str(row["release_mbid"]), "release-1")
                self.assertEqual(str(row["release_group_mbid"]), "group-1")
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
