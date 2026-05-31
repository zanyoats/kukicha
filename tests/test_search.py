from __future__ import annotations

from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from kukicha.use_case import (
    AlbumListQuery,
    ArtistNotFoundError,
    GenreStyleFilter,
    LibraryQueries,
    LibrarySearchQuery,
    album_where_clause,
)
from kukicha.use_case import (
    ALBUM_SEARCH_METADATA_KEY,
    ALBUM_SEARCH_INDEX_VERSION,
    LIBRARY_SEARCH_METADATA_KEY,
    LIBRARY_SEARCH_INDEX_VERSION,
    UNKNOWN_GENRE_TAG,
    connect_database,
)
from kukicha.use_case import save_library
from kukicha.models import MusicLibrary, TrackArtwork, TrackRecord
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
                tokenize = 'unicode61'
            )
            """
        )
        self.insert_album("eno_hopkins", "Brian Eno", "Small Craft")
        self.insert_album(
            "eno_abrahams",
            "Brian Eno with Jon Hopkins & Leo Abrahams",
            "Small Craft",
        )
        self.insert_album("ok_computer", "Radiohead", "OK Computer")
        self.insert_album(
            "debussy",
            "Claude Debussy",
            "Montreal Recital",
        )

    def tearDown(self) -> None:
        self.connection.close()

    def insert_album(
        self,
        album_id: str,
        artist: str,
        album: str,
    ) -> None:
        self.connection.execute(
            "INSERT INTO library_albums (album_id) VALUES (?)",
            (album_id,),
        )
        self.connection.execute(
            """
                INSERT INTO library_album_search (album_id, artist, album)
                VALUES (?, ?, ?)
                """,
            (album_id, artist, album),
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

    def test_does_not_match_track_level_terms(self) -> None:
        self.assertEqual(self.search("Emerald"), [])
        self.assertEqual(self.search("Suite"), [])


class AlbumSearchIndexTest(unittest.TestCase):
    def test_connect_database_context_manager_closes_connection(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "library.sqlite"
            with connect_database(database) as connection:
                connection.execute("SELECT 1")

            with self.assertRaisesRegex(sqlite3.ProgrammingError, "closed database"):
                connection.execute("SELECT 1")

    def test_saved_library_indexes_album_artist_and_album_only(self) -> None:
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

            api = LibraryQueries(database)
            artist_page = api.list_album_page(AlbumListQuery(search="Rodney"))
            album_page = api.list_album_page(AlbumListQuery(search="Vivaldi"))
            composer_page = api.list_album_page(AlbumListQuery(search="Antonio"))
            title_page = api.list_album_page(AlbumListQuery(search="Allegro"))

        self.assertEqual(
            [album.album for album in artist_page.items],
            ["Vivaldi: The Four Seasons"],
        )
        self.assertEqual(
            [album.album for album in album_page.items],
            ["Vivaldi: The Four Seasons"],
        )
        self.assertEqual([album.album for album in composer_page.items], [])
        self.assertEqual([album.album for album in title_page.items], [])

    def test_connect_database_migrates_legacy_track_level_search_schema(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "library.sqlite"
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    "CREATE TABLE app_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
                )
                connection.execute(
                    """
                    INSERT INTO app_metadata (key, value)
                    VALUES (?, '4')
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
                        composer,
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
        self.assertEqual(search_columns, {"album_id", "artist", "album"})
        self.assertEqual(search_version, ALBUM_SEARCH_INDEX_VERSION)


class LibrarySearchIndexTest(unittest.TestCase):
    def build_database(self) -> Path:
        tempdir = TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        database = Path(tempdir.name) / "library.sqlite"
        save_library(
            MusicLibrary(
                roots=[],
                tracks=[
                    TrackRecord(
                        path="/music/jim-bill/01.flac",
                        file_type="flac",
                        artist="Jim Hall",
                        album_artist="Jim Hall & Bill Evans",
                        album="Undercurrent",
                        title="My Funny Valentine",
                    ),
                    TrackRecord(
                        path="/music/alice/01.flac",
                        file_type="flac",
                        artist="Alice Coltrane",
                        album_artist="Alice Coltrane",
                        album="Journey in Satchidananda",
                        title="Journey in Satchidananda",
                    ),
                    TrackRecord(
                        path="/music/bill/01.flac",
                        file_type="flac",
                        artist="Bill Evans",
                        album_artist="Bill Evans",
                        album="Moon Beams",
                        title="Re: Person I Knew",
                    ),
                ],
                supported_extensions=[".flac"],
                generated_at="test",
            ),
            database,
        )
        return database

    def test_search_uses_entity_scoped_fields_and_split_artists(self) -> None:
        api = LibraryQueries(self.build_database())

        artist_results = api.search(
            LibrarySearchQuery(query="Bill", artist_count=10, album_count=10, song_count=10)
        )
        album_results = api.search(
            LibrarySearchQuery(query="Undercurrent", artist_count=10, album_count=10, song_count=10)
        )
        track_results = api.search(
            LibrarySearchQuery(query="Funny", artist_count=10, album_count=10, song_count=10)
        )

        self.assertEqual(
            [artist.artist for artist in artist_results.artists.items],
            ["Bill Evans"],
        )
        self.assertEqual([album.album for album in artist_results.albums.items], [])
        self.assertEqual([track.title for track in artist_results.songs.items], [])
        self.assertEqual(
            [album.album for album in album_results.albums.items],
            ["Undercurrent"],
        )
        self.assertEqual([track.title for track in album_results.songs.items], [])
        self.assertEqual(
            [track.title for track in track_results.songs.items],
            ["My Funny Valentine"],
        )
        self.assertEqual([album.album for album in track_results.albums.items], [])

    def test_empty_search_uses_raw_order_and_independent_pagination(self) -> None:
        api = LibraryQueries(self.build_database())

        first_page = api.search(
            LibrarySearchQuery(
                query="",
                artist_count=2,
                album_count=2,
                song_count=2,
            )
        )
        second_album_page = api.search(
            LibrarySearchQuery(
                query="",
                artist_count=0,
                album_count=2,
                album_offset=2,
                song_count=0,
            )
        )

        self.assertEqual(
            [artist.artist for artist in first_page.artists.items],
            ["Jim Hall", "Bill Evans"],
        )
        self.assertTrue(first_page.artists.has_next)
        self.assertEqual(
            [album.album for album in first_page.albums.items],
            ["Undercurrent", "Journey in Satchidananda"],
        )
        self.assertEqual(
            [track.title for track in first_page.songs.items],
            ["My Funny Valentine", "Journey in Satchidananda"],
        )
        self.assertEqual(
            [album.album for album in second_album_page.albums.items],
            ["Moon Beams"],
        )
        self.assertEqual(second_album_page.artists.items, ())
        self.assertEqual(second_album_page.songs.items, ())

    def test_non_empty_search_ranks_matches_by_relevance(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "library.sqlite"
            save_library(
                MusicLibrary(
                    roots=[],
                    tracks=[
                        TrackRecord(
                            path="/music/blue/01.flac",
                            file_type="flac",
                            album_artist="First Artist",
                            album="First Album",
                            title="Blue",
                        ),
                        TrackRecord(
                            path="/music/deep-blue/01.flac",
                            file_type="flac",
                            album_artist="Second Artist",
                            album="Second Album",
                            title="Blue Blue Blue",
                        ),
                    ],
                    supported_extensions=[".flac"],
                    generated_at="test",
                ),
                database,
            )

            results = LibraryQueries(database).search(
                LibrarySearchQuery(query="Blue", artist_count=0, album_count=0, song_count=10)
            )

        self.assertEqual(
            [track.title for track in results.songs.items],
            ["Blue Blue Blue", "Blue"],
        )

    def test_connect_database_migrates_library_search_schema(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "library.sqlite"
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    "CREATE TABLE app_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
                )
                connection.execute(
                    """
                    INSERT INTO app_metadata (key, value)
                    VALUES (?, 'old')
                    """,
                    (LIBRARY_SEARCH_METADATA_KEY,),
                )
                connection.execute(
                    """
                    CREATE VIRTUAL TABLE library_search USING fts5(
                        entity_type UNINDEXED,
                        entity_id UNINDEXED,
                        text,
                        tokenize = 'unicode61'
                    )
                    """
                )
                connection.commit()
            finally:
                connection.close()

            with connect_database(database, create=False) as connection:
                search_columns = {
                    str(row["name"])
                    for row in connection.execute("PRAGMA table_info(library_search)")
                }
                search_version = connection.execute(
                    "SELECT value FROM app_metadata WHERE key = ?",
                    (LIBRARY_SEARCH_METADATA_KEY,),
                ).fetchone()["value"]

        self.assertEqual(
            search_columns,
            {"entity_type", "entity_id", "root_position", "sort_order", "text"},
        )
        self.assertEqual(search_version, LIBRARY_SEARCH_INDEX_VERSION)


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

    def test_genre_and_style_queries_are_canonicalized_before_filtering(self) -> None:
        api = LibraryQueries(self.build_database())
        query = AlbumListQuery(genres=("jazz",), styles=("modal",))
        expanded = api.expand_album_list_query(query)

        self.assertEqual(expanded.genres, ("Jazz",))
        self.assertEqual(expanded.styles, ("Modal",))

        page = api.list_album_page(query)
        self.assertEqual([album.album for album in page.items], ["Blue Session"])

    def test_grouped_genre_filters_are_canonicalized_before_filtering(self) -> None:
        api = LibraryQueries(self.build_database())
        query = AlbumListQuery(
            genre_filters=(
                GenreStyleFilter(genre="classical", styles=("baroque",)),
            )
        )
        expanded = api.expand_album_list_query(query)

        self.assertEqual(
            expanded.genre_filters,
            (GenreStyleFilter(genre="Classical"),),
        )

        page = api.list_album_page(query)
        self.assertEqual([album.album for album in page.items], ["Court Dances"])

    def test_genre_style_predicates_use_binary_comparisons(self) -> None:
        plain_sql, _plain_params = album_where_clause(
            AlbumListQuery(genres=("Jazz",), styles=("Modal",))
        )
        grouped_sql, _grouped_params = album_where_clause(
            AlbumListQuery(
                genre_filters=(
                    GenreStyleFilter(genre="Jazz", styles=("Modal",)),
                )
            )
        )

        self.assertNotIn("COLLATE NOCASE", f"{plain_sql}\n{grouped_sql}")


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


class LibraryArtistQueriesTest(unittest.TestCase):
    def save_artist_library(self, database: Path) -> None:
        temp_path = database.parent
        root_a = temp_path / "music-a"
        root_b = temp_path / "music-b"
        save_library(
            MusicLibrary(
                roots=[str(root_a), str(root_b)],
                tracks=[
                    TrackRecord(
                        path=str(root_a / "The Apples" / "Red" / "01.mp3"),
                        root_position=0,
                        file_type="mp3",
                        artist="The Apples",
                        album_artist="The Apples",
                        album="Red",
                        title="Red One",
                        genres=["Rock"],
                        duration_seconds=30.0,
                        album_artwork=TrackArtwork(
                            mime_type="image/png",
                            data=b"apples-cover",
                        ),
                    ),
                    TrackRecord(
                        path=str(root_a / "Brian Eno" / "Ambient 1" / "01.flac"),
                        root_position=0,
                        file_type="flac",
                        artist="Brian Eno",
                        album_artist="Brian Eno",
                        album="Ambient 1",
                        title="1/1",
                        genres=["Ambient"],
                        duration_seconds=70.0,
                    ),
                    TrackRecord(
                        path=str(root_b / "Brian Eno" / "Another Green World" / "01.flac"),
                        root_position=1,
                        file_type="flac",
                        artist="Brian Eno",
                        album_artist="Brian Eno",
                        album="Another Green World",
                        title="Sky Saw",
                        genres=["Electronic"],
                        duration_seconds=80.0,
                        album_artwork=TrackArtwork(
                            mime_type="image/png",
                            data=b"eno-cover",
                        ),
                    ),
                ],
                supported_extensions=[".mp3", ".flac"],
                generated_at="test",
            ),
            database,
        )

    def test_lists_album_artists_globally_and_by_root(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "library.sqlite"
            self.save_artist_library(database)
            api = LibraryQueries(database)

            global_artists = {
                artist.artist: artist
                for artist in api.list_album_artists()
            }
            root_artists = {
                artist.artist: artist
                for artist in api.list_album_artists(root_position=0)
            }

        self.assertEqual(
            set(global_artists),
            {"Brian Eno", "The Apples"},
        )
        self.assertEqual(global_artists["Brian Eno"].album_count, 2)
        self.assertEqual(
            global_artists["Brian Eno"].cover_album_id,
            "brian-eno::another-green-world",
        )
        self.assertEqual(root_artists["The Apples"].album_count, 1)
        self.assertEqual(root_artists["The Apples"].cover_album_id, "the-apples::red")
        self.assertEqual(root_artists["Brian Eno"].album_count, 1)
        self.assertIsNone(root_artists["Brian Eno"].cover_album_id)

    def test_get_album_artist_returns_details_case_insensitively(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "library.sqlite"
            self.save_artist_library(database)

            artist = LibraryQueries(database).get_album_artist("brian eno")

        self.assertEqual(artist.artist, "Brian Eno")
        self.assertEqual(artist.album_count, 2)
        self.assertEqual(artist.cover_album_id, "brian-eno::another-green-world")
        self.assertEqual(
            [album.album_id for album in artist.albums],
            ["brian-eno::ambient-1", "brian-eno::another-green-world"],
        )
        self.assertEqual(artist.albums[0].duration_seconds, 70)
        self.assertEqual(artist.albums[0].genre, "Ambient")
        self.assertFalse(artist.albums[0].has_cover)
        self.assertEqual(artist.albums[1].duration_seconds, 80)
        self.assertEqual(artist.albums[1].genre, "Electronic")
        self.assertTrue(artist.albums[1].has_cover)

    def test_get_album_artist_raises_for_missing_artist(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "library.sqlite"
            self.save_artist_library(database)

            with self.assertRaises(ArtistNotFoundError):
                LibraryQueries(database).get_album_artist("Missing")


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
            page = api.list_album_page(
                AlbumListQuery(genres=(UNKNOWN_GENRE_TAG.casefold(),))
            )

        self.assertIn(
            UNKNOWN_GENRE_TAG,
            [group.genre for group in options.genre_groups],
        )
        self.assertEqual([album.album for album in page.items], ["Test Album"])


if __name__ == "__main__":
    unittest.main()
