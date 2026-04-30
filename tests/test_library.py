from __future__ import annotations

from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from kukicha.use_case import AlbumListQuery, LibraryQueries
from kukicha.use_case import connect_database
from kukicha.use_case import (
    ItunesLookupCandidate,
    ItunesLookupClient,
    ItunesLookupStats,
    get_itunes_lookup_image,
)
from kukicha.use_case import resolve_library_cover_art, resolve_library_genres, save_library
from kukicha.models import (
    MusicLibrary,
    PlaylistItemRecord,
    PlaylistRecord,
    TrackArtwork,
    TrackRecord,
)
from kukicha.use_case import delete_library_root


class LibraryAlbumPathQueryTest(unittest.TestCase):
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
            root_b_cover_page = api.list_album_page(
                AlbumListQuery(root_positions=(1,), has_cover=True)
            )
            root_a_cover_page = api.list_album_page(
                AlbumListQuery(root_positions=(0,), has_cover=True)
            )
            root_b_genre_page = api.list_album_page(
                AlbumListQuery(root_positions=(1,), genres=("Jazz",))
            )

        self.assertEqual([album.album for album in root_b_page.items], ["Album"])
        self.assertEqual(root_b_page.items[0].track_count, 1)
        self.assertIsNone(root_b_page.items[0].art_track_id)
        self.assertFalse(hasattr(root_b_page.items[0], "track_ids"))
        self.assertEqual(root_b_cover_page.items, ())
        self.assertEqual([album.album for album in root_a_cover_page.items], ["Album"])
        self.assertEqual([album.album for album in root_b_genre_page.items], ["Album"])

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
                    stat.playlists_scanned,
                )
                for stat in stats
            ],
            [
                (0, 3, 2, 1),
                (1, 1, 1, 1),
                (2, 0, 0, 0),
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
        self.assertEqual(total_stats.playlists_scanned, 2)
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
                        ("berlin-philharmonic-bell-karajan::foo",),
                    )
                ]
            finally:
                connection.close()

            api = LibraryQueries(database)
            filtered_page = api.list_album_page(AlbumListQuery(artists=("Bell",)))
            search_page = api.list_album_page(AlbumListQuery(search="Karajan"))
            stats = api.library_stats()
            root_stats = api.library_root_stats()
            album = api.get_album("berlin-philharmonic-bell-karajan::foo")

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
                "robert-fripp-brian-eno::no-pussyfooting"
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
                        album_id, release_mbid, release_group_mbid
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
                    WHERE album_id = ?
                    """,
                    ("artist::album",),
                ).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(str(row["release_mbid"]), "release-1")
                self.assertEqual(str(row["release_group_mbid"]), "group-1")
            finally:
                connection.close()

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

    def test_delete_library_root_keeps_musicbrainz_links_when_album_is_removed(self) -> None:
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
                        album_id, release_mbid, release_group_mbid
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

            delete_library_root(database, 0)

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
                    WHERE album_id = ?
                    """,
                    ("artist::album",),
                ).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(str(row["release_mbid"]), "release-1")
                self.assertEqual(str(row["release_group_mbid"]), "group-1")
            finally:
                connection.close()

    def test_delete_library_root_keeps_itunes_lookup_cache_when_album_is_removed(self) -> None:
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

            delete_library_root(database, 0)

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
                    WHERE album_id = ?
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

    def test_connect_database_migrates_file_created_date_columns_and_backfills_from_filesystem(self) -> None:
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

            created_dates = {
                "/music/Artist/Album/01.flac": "2026-04-20T12:00:00+00:00",
                "/music/Mix.m3u8": "2026-04-21T12:00:00+00:00",
            }

            with patch(
                "kukicha.use_case.database.file_created_at",
                side_effect=lambda path: created_dates.get(str(path)),
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
                playlist_date = connection.execute(
                    "SELECT file_created_at FROM library_playlists"
                ).fetchone()["file_created_at"]
            finally:
                connection.close()

        self.assertIn("file_created_at", track_columns)
        self.assertIn("file_created_at", album_columns)
        self.assertNotIn("artist", album_columns)
        self.assertIn("file_created_at", playlist_columns)
        self.assertEqual(str(track_date), "2026-04-20T12:00:00+00:00")
        self.assertEqual(str(album_date), "2026-04-20T12:00:00+00:00")
        self.assertEqual(str(playlist_date), "2026-04-21T12:00:00+00:00")


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
                            file_created_at="2026-04-25T12:00:00+00:00",
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
                    SELECT file_created_at
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
        self.assertEqual(
            str(playlist_row["file_created_at"]),
            "2026-04-25T12:00:00+00:00",
        )

    def test_list_album_page_sorts_by_recently_added_by_default_and_can_sort_by_artist_album(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            save_library(
                MusicLibrary(
                    roots=[],
                    tracks=[
                        TrackRecord(
                            path="/music/Zulu/Old/01.flac",
                            file_created_at="2026-04-20T12:00:00+00:00",
                            file_type="flac",
                            artist="Zulu",
                            album_artist="Zulu",
                            album="Old",
                            title="Old Track",
                        ),
                        TrackRecord(
                            path="/music/Alpha/New/01.flac",
                            file_created_at="2026-04-24T12:00:00+00:00",
                            file_type="flac",
                            artist="Alpha",
                            album_artist="Alpha",
                            album="New",
                            title="New Track",
                        ),
                    ],
                    playlists=[
                        PlaylistRecord(
                            path="/music/recent.m3u8",
                            name="Recent Mix",
                            file_created_at="2026-04-25T12:00:00+00:00",
                        )
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-04-25T00:00:00+00:00",
                ),
                database,
            )

            api = LibraryQueries(database)
            recently_added = api.list_album_page(AlbumListQuery()).items
            artist_album = api.list_album_page(AlbumListQuery(sort="artist")).items

        self.assertEqual(
            [item.album for item in recently_added],
            ["Recent Mix", "New", "Old"],
        )
        self.assertEqual(
            [item.album for item in artist_album],
            ["New", "Recent Mix", "Old"],
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
        self.assertEqual(playlist.root_position, 0)
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
        self.assertEqual(playlist.items[1].genre, "Electronic")
        self.assertEqual(playlist.items[1].cover_url, "https://example.test/cover.jpg")

    def test_list_album_page_can_filter_playlist_items(self) -> None:
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
            all_items = api.list_album_page(AlbumListQuery()).items
            playlists = api.list_album_page(AlbumListQuery(is_playlist=True)).items
            albums = api.list_album_page(AlbumListQuery(is_playlist=False)).items

        self.assertEqual([item.album for item in all_items], ["Album", "Road Mix"])
        self.assertEqual([item.album for item in playlists], ["Road Mix"])
        self.assertTrue(playlists[0].is_playlist)
        self.assertEqual(playlists[0].playlist_id, 1)
        self.assertIn("Road Mix", playlists[0].cover_svg)
        self.assertEqual([item.album for item in albums], ["Album"])
        self.assertFalse(albums[0].is_playlist)


class LibraryGenreResolutionTest(unittest.TestCase):
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
                        album_id, release_mbid, release_group_mbid
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


class LibraryCoverArtResolutionTest(unittest.TestCase):
    def test_get_itunes_lookup_image_caches_missing_artwork_results(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            connection = connect_database(Path(tempdir) / "kukicha.sqlite")
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
                        album_id, release_mbid, release_group_mbid
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
                        album_id, release_mbid, release_group_mbid
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
                    "academy-of-st-martin-in-the-fields-and-sir-neville-marriner"
                    "::handel-music-for-the-royal-fireworks-and-water-music"
                )
                new_album_id = (
                    "academy-of-st-martin-in-the-fields-sir-neville-marriner"
                    "::handel-music-for-the-royal-fireworks-and-water-music"
                )
                connection.execute(
                    """
                    INSERT INTO album_musicbrainz_links (
                        album_id, release_mbid, release_group_mbid
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
                    WHERE album_id = ?
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
