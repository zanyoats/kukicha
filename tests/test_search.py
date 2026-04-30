from __future__ import annotations

from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from kukicha.use_case import AlbumListQuery, GenreStyleFilter, LibraryQueries, album_where_clause
from kukicha.use_case import (
    ALBUM_SEARCH_METADATA_KEY,
    ALBUM_SEARCH_INDEX_VERSION,
    UNKNOWN_GENRE_TAG,
    connect_database,
)
from kukicha.use_case import save_library
from kukicha.models import MusicLibrary, TrackRecord
from kukicha.search import parse_album_search_query


class SearchParserTest(unittest.TestCase):
    def test_parses_or_and_not_groups(self) -> None:
        groups = parse_album_search_query("radiohead computer; debussy montreal")

        self.assertEqual(
            [
                [(factor.match_query, factor.negated) for factor in group]
                for group in groups
            ],
            [
                [('"radiohead"', False), ('"computer"', False)],
                [('"debussy"', False), ('"montreal"', False)],
            ],
        )

    def test_parses_quoted_phrase_and_negated_token(self) -> None:
        groups = parse_album_search_query('"Brian Eno" -abrahams')

        self.assertEqual(
            [
                [(factor.match_query, factor.negated) for factor in group]
                for group in groups
            ],
            [[('"Brian Eno"', False), ('"abrahams"', True)]],
        )


class AlbumSearchPredicateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("CREATE TABLE library_albums (album_id TEXT PRIMARY KEY)")
        self.connection.execute(
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
        self.insert_album("eno_hopkins", "Brian Eno", "Small Craft", "Emerald Rush")
        self.insert_album(
            "eno_abrahams",
            "Brian Eno with Jon Hopkins & Leo Abrahams",
            "Small Craft",
            "Slow Ice",
        )
        self.insert_album("ok_computer", "Radiohead", "OK Computer", "No Surprises")
        self.insert_album(
            "debussy",
            "Claude Debussy",
            "Montreal Recital",
            "Suite bergamasque",
        )

    def tearDown(self) -> None:
        self.connection.close()

    def insert_album(
        self,
        album_id: str,
        artist: str,
        album: str,
        title: str,
        composer: str = "",
    ) -> None:
        self.connection.execute(
            "INSERT INTO library_albums (album_id) VALUES (?)",
            (album_id,),
        )
        self.connection.execute(
            """
                INSERT INTO library_album_search (album_id, artist, album, title, composer)
                VALUES (?, ?, ?, ?, ?)
                """,
            (album_id, artist, album, title, composer),
        )

    def search(self, value: str) -> list[str]:
        where_sql, params = album_where_clause(AlbumListQuery(search=value))
        rows = self.connection.execute(
            f"""
            SELECT album_id
            FROM library_albums AS albums
            {where_sql}
            ORDER BY album_id
            """,
            params,
        )
        return [str(row["album_id"]) for row in rows]

    def test_semicolon_is_or(self) -> None:
        self.assertEqual(
            self.search('"Brian Eno"; "OK Computer"'),
            ["eno_abrahams", "eno_hopkins", "ok_computer"],
        )

    def test_whitespace_is_and_inside_or_groups(self) -> None:
        self.assertEqual(
            self.search("radiohead computer; debussy montreal"),
            ["debussy", "ok_computer"],
        )

    def test_minus_excludes_matches(self) -> None:
        self.assertEqual(self.search("eno -abrahams"), ["eno_hopkins"])

    def test_matches_composer_column(self) -> None:
        self.insert_album(
            "vivaldi_four_seasons",
            "Itzhak Perlman, London Philharmonic Orchestra & Rodney Friend",
            "Vivaldi: The Four Seasons",
            "The Four Seasons, Concerto No. 1 in E Major",
            "Antonio Vivaldi",
        )

        self.assertEqual(self.search("Antonio"), ["vivaldi_four_seasons"])


class AlbumComposerSearchIndexTest(unittest.TestCase):
    def test_connect_database_context_manager_closes_connection(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "library.sqlite"
            with connect_database(database) as connection:
                connection.execute("SELECT 1")

            with self.assertRaisesRegex(sqlite3.ProgrammingError, "closed database"):
                connection.execute("SELECT 1")

    def test_saved_library_indexes_track_composer(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "library.sqlite"
            save_library(
                MusicLibrary(
                    roots=[],
                    tracks=[
                        TrackRecord(
                            path="/music/01 The Four Seasons.m4a",
                            file_type="m4a",
                            artist="London Philharmonic Orchestra, Itzhak Perlman & Rodney Friend",
                            album_artist=(
                                "Itzhak Perlman, London Philharmonic Orchestra & Rodney Friend"
                            ),
                            composer="Antonio Vivaldi",
                            album="Vivaldi: The Four Seasons",
                            title=(
                                'The Four Seasons, Concerto No. 1 in E Major, '
                                'RV 269 "Spring": I. Allegro'
                            ),
                        )
                    ],
                    supported_extensions=[".m4a"],
                    generated_at="test",
                ),
                database,
            )

            page = LibraryQueries(database).list_album_page(AlbumListQuery(search="Antonio"))

        self.assertEqual(
            [album.album for album in page.items],
            ["Vivaldi: The Four Seasons"],
        )

    def test_connect_database_migrates_legacy_composer_search_schema(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "library.sqlite"
            connection = sqlite3.connect(database)
            try:
                connection.execute("CREATE TABLE app_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                connection.execute(
                    """
                    INSERT INTO app_metadata (key, value)
                    VALUES (?, '1')
                    """,
                    (ALBUM_SEARCH_METADATA_KEY,),
                )
                connection.execute(
                    """
                    CREATE TABLE library_tracks (
                        track_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        album_id TEXT,
                        root_position INTEGER,
                        path TEXT NOT NULL UNIQUE,
                        file_type TEXT,
                        scan_error TEXT,
                        artist TEXT,
                        album_artist TEXT,
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
                        bitrate INTEGER
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE VIRTUAL TABLE library_album_search USING fts5(
                        album_id UNINDEXED,
                        artist,
                        album,
                        title,
                        tokenize = 'unicode61'
                    )
                    """
                )
                connection.commit()
            finally:
                connection.close()

            with connect_database(database, create=False) as connection:
                track_columns = {
                    str(row["name"])
                    for row in connection.execute("PRAGMA table_info(library_tracks)")
                }
                search_columns = {
                    str(row["name"])
                    for row in connection.execute("PRAGMA table_info(library_album_search)")
                }
                search_version = connection.execute(
                    "SELECT value FROM app_metadata WHERE key = ?",
                    (ALBUM_SEARCH_METADATA_KEY,),
                ).fetchone()["value"]

        self.assertIn("composer", track_columns)
        self.assertIn("composer", search_columns)
        self.assertEqual(search_version, ALBUM_SEARCH_INDEX_VERSION)


class AlbumFacetFilterSemanticsTest(unittest.TestCase):
    def build_database(self) -> Path:
        tempdir = TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        database = Path(tempdir.name) / "library.sqlite"
        save_library(
            MusicLibrary(
                roots=[],
                tracks=[
                    TrackRecord(
                        path="/music/blue-session/01.m4a",
                        file_type="m4a",
                        artist="Alice Quartet",
                        album_artist="Alice Quartet",
                        album="Blue Session",
                        title="Blue Session",
                        genres=["Jazz"],
                        styles=["Modal"],
                    ),
                    TrackRecord(
                        path="/music/mist-forms/01.m4a",
                        file_type="m4a",
                        artist="Brian Drift",
                        album_artist="Brian Drift",
                        album="Mist Forms",
                        title="Mist Forms",
                        genres=["Electronic"],
                        styles=["Drone"],
                    ),
                    TrackRecord(
                        path="/music/night-pulse/01.m4a",
                        file_type="m4a",
                        artist="Cinder Unit",
                        album_artist="Cinder Unit",
                        album="Night Pulse",
                        title="Night Pulse",
                        genres=["Electronic"],
                        styles=["Dub"],
                    ),
                    TrackRecord(
                        path="/music/court-dances/01.m4a",
                        file_type="m4a",
                        artist="Dorian Ensemble",
                        album_artist="Dorian Ensemble",
                        album="Court Dances",
                        title="Court Dances",
                        genres=["Classical"],
                        styles=["Baroque"],
                    ),
                ],
                supported_extensions=[".m4a"],
                generated_at="test",
            ),
            database,
        )
        return database

    def test_multiple_genres_use_or_semantics_within_facet(self) -> None:
        page = LibraryQueries(self.build_database()).list_album_page(
            AlbumListQuery(genres=("Jazz", "Electronic"))
        )

        self.assertEqual(
            [album.album for album in page.items],
            ["Blue Session", "Mist Forms", "Night Pulse"],
        )

    def test_multiple_styles_use_or_semantics_within_facet(self) -> None:
        page = LibraryQueries(self.build_database()).list_album_page(
            AlbumListQuery(styles=("Modal", "Drone"))
        )

        self.assertEqual(
            [album.album for album in page.items],
            ["Blue Session", "Mist Forms"],
        )

    def test_distinct_facets_use_and_semantics_across_facets(self) -> None:
        page = LibraryQueries(self.build_database()).list_album_page(
            AlbumListQuery(genres=("Jazz", "Electronic"), styles=("Drone",))
        )

        self.assertEqual([album.album for album in page.items], ["Mist Forms"])

    def test_styles_expand_to_their_parent_genres(self) -> None:
        page = LibraryQueries(self.build_database()).list_album_page(
            AlbumListQuery(
                genre_filters=(
                    GenreStyleFilter(genre="Classical", styles=("Baroque",)),
                )
            )
        )

        self.assertEqual([album.album for album in page.items], ["Court Dances"])


class AlbumExpandedGenreSelectionTest(unittest.TestCase):
    def build_database(self) -> Path:
        tempdir = TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        database = Path(tempdir.name) / "library.sqlite"
        save_library(
            MusicLibrary(
                roots=[],
                tracks=[
                    TrackRecord(
                        path="/music/blue-session/01.m4a",
                        file_type="m4a",
                        artist="Alice Quartet",
                        album_artist="Alice Quartet",
                        album="Blue Session",
                        title="Blue Session",
                        genres=["Jazz"],
                        styles=["Modal"],
                    ),
                    TrackRecord(
                        path="/music/late-set/01.m4a",
                        file_type="m4a",
                        artist="Alice Quartet",
                        album_artist="Alice Quartet",
                        album="Late Set",
                        title="Late Set",
                        genres=["Jazz"],
                        styles=["Cool Jazz"],
                    ),
                    TrackRecord(
                        path="/music/bare-bones/01.m4a",
                        file_type="m4a",
                        artist="Alice Quartet",
                        album_artist="Alice Quartet",
                        album="Bare Bones",
                        title="Bare Bones",
                        genres=["Jazz"],
                        styles=[],
                    ),
                    TrackRecord(
                        path="/music/mist-forms/01.m4a",
                        file_type="m4a",
                        artist="Brian Drift",
                        album_artist="Brian Drift",
                        album="Mist Forms",
                        title="Mist Forms",
                        genres=["Electronic"],
                        styles=["Drone"],
                    ),
                    TrackRecord(
                        path="/music/court-dances/01.m4a",
                        file_type="m4a",
                        artist="Dorian Ensemble",
                        album_artist="Dorian Ensemble",
                        album="Court Dances",
                        title="Court Dances",
                        genres=["Classical"],
                        styles=["Baroque"],
                    ),
                    TrackRecord(
                        path="/music/string-works/01.m4a",
                        file_type="m4a",
                        artist="Dorian Ensemble",
                        album_artist="Dorian Ensemble",
                        album="String Works",
                        title="String Works",
                        genres=["Classical"],
                        styles=[],
                    ),
                ],
                supported_extensions=[".m4a"],
                generated_at="test",
            ),
            database,
        )
        return database

    def test_all_selected_styles_collapse_to_the_explicit_parent_genre(self) -> None:
        page = LibraryQueries(self.build_database()).list_album_page(
            AlbumListQuery(
                genre_filters=(
                    GenreStyleFilter(genre="Jazz", styles=("Modal", "Cool Jazz")),
                )
            )
        )

        self.assertEqual(
            [album.album for album in page.items],
            ["Bare Bones", "Blue Session", "Late Set"],
        )

    def test_style_only_query_stays_style_specific(self) -> None:
        page = LibraryQueries(self.build_database()).list_album_page(
            AlbumListQuery(styles=("Baroque",))
        )

        self.assertEqual([album.album for album in page.items], ["Court Dances"])


class AlbumGroupedGenreSelectionTest(unittest.TestCase):
    def build_database(self) -> Path:
        tempdir = TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        database = Path(tempdir.name) / "library.sqlite"
        save_library(
            MusicLibrary(
                roots=[],
                tracks=[
                    TrackRecord(
                        path="/music/modal-jazz/01.m4a",
                        file_type="m4a",
                        artist="Alice Quartet",
                        album_artist="Alice Quartet",
                        album="Modal Jazz",
                        title="Modal Jazz",
                        genres=["Jazz"],
                        styles=["Modal"],
                    ),
                    TrackRecord(
                        path="/music/unstyled-jazz/01.m4a",
                        file_type="m4a",
                        artist="Alice Quartet",
                        album_artist="Alice Quartet",
                        album="Unstyled Jazz",
                        title="Unstyled Jazz",
                        genres=["Jazz"],
                    ),
                    TrackRecord(
                        path="/music/cool-jazz/01.m4a",
                        file_type="m4a",
                        artist="Alice Quartet",
                        album_artist="Alice Quartet",
                        album="Cool Jazz",
                        title="Cool Jazz",
                        genres=["Jazz"],
                        styles=["Cool Jazz"],
                    ),
                    TrackRecord(
                        path="/music/mismatched-drone/01.m4a",
                        file_type="m4a",
                        artist="Alice Quartet",
                        album_artist="Alice Quartet",
                        album="Mismatched Drone",
                        title="Mismatched Drone",
                        genres=["Jazz"],
                        styles=["Drone"],
                    ),
                    TrackRecord(
                        path="/music/electronic-drone/01.m4a",
                        file_type="m4a",
                        artist="Brian Drift",
                        album_artist="Brian Drift",
                        album="Electronic Drone",
                        title="Electronic Drone",
                        genres=["Electronic"],
                        styles=["Drone"],
                    ),
                ],
                supported_extensions=[".m4a"],
                generated_at="test",
            ),
            database,
        )
        return database

    def test_grouped_styles_must_match_their_parent_genre(self) -> None:
        page = LibraryQueries(self.build_database()).list_album_page(
            AlbumListQuery(
                genre_filters=(
                    GenreStyleFilter(genre="Jazz", styles=("Modal",)),
                    GenreStyleFilter(genre="Electronic", styles=("Drone",)),
                )
            )
        )

        self.assertEqual(
            [album.album for album in page.items],
            ["Modal Jazz", "Electronic Drone"],
        )

    def test_all_selected_group_styles_collapse_to_parent_genre(self) -> None:
        page = LibraryQueries(self.build_database()).list_album_page(
            AlbumListQuery(
                genre_filters=(
                    GenreStyleFilter(genre="Jazz", styles=("Modal", "Cool Jazz")),
                )
            )
        )

        self.assertEqual(
            [album.album for album in page.items],
            ["Cool Jazz", "Mismatched Drone", "Modal Jazz", "Unstyled Jazz"],
        )

    def test_parent_only_group_filter_matches_genre_with_no_styles_argument(self) -> None:
        page = LibraryQueries(self.build_database()).list_album_page(
            AlbumListQuery(
                genre_filters=(
                    GenreStyleFilter(genre="Jazz"),
                )
            )
        )

        self.assertEqual(
            [album.album for album in page.items],
            ["Cool Jazz", "Mismatched Drone", "Modal Jazz", "Unstyled Jazz"],
        )


class AlbumTrackIdsTest(unittest.TestCase):
    def test_get_album_includes_album_track_ids_in_playback_order(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "library.sqlite"
            save_library(
                MusicLibrary(
                    roots=[],
                    tracks=[
                        TrackRecord(
                            path="/music/ordered-album/02.m4a",
                            file_type="m4a",
                            artist="Test Artist",
                            album_artist="Test Artist",
                            album="Ordered Album",
                            title="Second",
                            track_number="2",
                        ),
                        TrackRecord(
                            path="/music/ordered-album/01.m4a",
                            file_type="m4a",
                            artist="Test Artist",
                            album_artist="Test Artist",
                            album="Ordered Album",
                            title="First",
                            track_number="1",
                        ),
                    ],
                    supported_extensions=[".m4a"],
                    generated_at="test",
                ),
                database,
            )

            album = LibraryQueries(database).get_album("test-artist::ordered-album")

        self.assertEqual(album.track_ids, (2, 1))

    def test_list_album_page_does_not_include_album_track_ids(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "library.sqlite"
            save_library(
                MusicLibrary(
                    roots=[],
                    tracks=[
                        TrackRecord(
                            path="/music/ordered-album/02.m4a",
                            file_type="m4a",
                            artist="Test Artist",
                            album_artist="Test Artist",
                            album="Ordered Album",
                            title="Second",
                            track_number="2",
                        ),
                        TrackRecord(
                            path="/music/ordered-album/01.m4a",
                            file_type="m4a",
                            artist="Test Artist",
                            album_artist="Test Artist",
                            album="Ordered Album",
                            title="First",
                            track_number="1",
                        ),
                    ],
                    supported_extensions=[".m4a"],
                    generated_at="test",
                ),
                database,
            )

            page = LibraryQueries(database).list_album_page(AlbumListQuery())

        self.assertFalse(hasattr(page.items[0], "track_ids"))


class AlbumGenreFilterOptionsTest(unittest.TestCase):
    def test_unknown_genre_is_available_as_filter_option(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "library.sqlite"
            save_library(
                MusicLibrary(
                    roots=[],
                    tracks=[
                        TrackRecord(
                            path="/music/01 Unknown Genre.m4a",
                            file_type="m4a",
                            artist="Test Artist",
                            album_artist="Test Artist",
                            album="Test Album",
                            title="Unknown Genre",
                            genres=[UNKNOWN_GENRE_TAG],
                        )
                    ],
                    supported_extensions=[".m4a"],
                    generated_at="test",
                ),
                database,
            )

            api = LibraryQueries(database)
            options = api.filter_options()
            page = api.list_album_page(AlbumListQuery(genres=(UNKNOWN_GENRE_TAG,)))

        self.assertIn(
            UNKNOWN_GENRE_TAG,
            [group.genre for group in options.genre_groups],
        )
        self.assertEqual([album.album for album in page.items], ["Test Album"])


if __name__ == "__main__":
    unittest.main()
