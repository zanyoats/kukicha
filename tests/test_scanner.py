from __future__ import annotations

from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from kukicha.discogs import group_library_albums
from kukicha.models import TrackRecord, UNKNOWN_METADATA_TAG
from kukicha.scanner import (
    PRIMARY_TAG_FIELDS,
    build_library,
    clear_external_artwork_caches,
    first_value,
    normalize_tags,
    scan_track,
    write_album_audio_tags,
    write_track_audio_tags,
)


class ScannerTagNormalizationTest(unittest.TestCase):
    def test_mp4_composer_atom_is_available_as_artist_fallback(self) -> None:
        tags = normalize_tags({"\xa9wrt": ["Antonio Vivaldi"]})

        self.assertEqual(tags["composer"], ["Antonio Vivaldi"])
        self.assertEqual(
            first_value(tags, PRIMARY_TAG_FIELDS["artist"]),
            "Antonio Vivaldi",
        )

    def test_raw_id3_composer_frame_is_available_as_artist_fallback(self) -> None:
        tags = normalize_tags({"TCOM": ["Antonio Vivaldi"]})

        self.assertEqual(tags["composer"], ["Antonio Vivaldi"])
        self.assertEqual(
            first_value(tags, PRIMARY_TAG_FIELDS["artist"]),
            "Antonio Vivaldi",
        )

    def test_original_date_is_preferred_over_release_date(self) -> None:
        tags = normalize_tags({"DATE": ["1999-05-04"], "ORIGINALDATE": ["1984-10-12"]})

        self.assertEqual(
            first_value(tags, PRIMARY_TAG_FIELDS["date"]),
            "1984-10-12",
        )

    def test_original_year_is_preferred_over_release_date(self) -> None:
        tags = normalize_tags({"TDRC": ["1999-05-04"], "TORY": ["1984"]})

        self.assertEqual(
            first_value(tags, PRIMARY_TAG_FIELDS["date"]),
            "1984",
        )

    def test_scan_track_captures_itunes_store_identifiers_from_mp4_tags(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "01 Test.m4a"
            path.write_bytes(b"not real audio; mutagen is mocked")

            audio = SimpleNamespace(
                tags={
                    "\xa9ART": ["Test Artist"],
                    "aART": ["Test Album Artist"],
                    "\xa9alb": ["Test Album"],
                    "\xa9nam": ["Test Title"],
                    "cnID": [440769234],
                    "plID": [440769149],
                },
                info=SimpleNamespace(length=123.456, bitrate=256000),
            )

            with patch("kukicha.scanner.MutagenFile", return_value=audio):
                track = scan_track(path)

        self.assertEqual(track.file_type, "m4a")
        self.assertEqual(track.itunes_store_track_id, "440769234")
        self.assertEqual(track.itunes_store_album_id, "440769149")

    def test_opus_files_are_scanned_with_vorbis_comment_tags(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            path = root / "01 Test.opus"
            path.write_bytes(b"not real audio; mutagen is mocked")

            audio = SimpleNamespace(
                tags={
                    "ARTIST": ["Test Artist"],
                    "ALBUMARTIST": ["Test Album Artist"],
                    "ALBUM": ["Test Album"],
                    "TITLE": ["Test Title"],
                    "DATE": ["2026"],
                    "GENRE": ["Electronic; Ambient"],
                    "TRACKNUMBER": ["1/8"],
                },
                info=SimpleNamespace(length=123.456, bitrate=128000),
            )

            with patch("kukicha.scanner.MutagenFile", return_value=audio) as mutagen_file:
                library = build_library([root])

        self.assertIn(".opus", library.supported_extensions)
        self.assertEqual(len(library.tracks), 1)
        track = library.tracks[0]
        self.assertEqual(track.path, str(path.resolve()))
        self.assertEqual(track.file_type, "opus")
        self.assertEqual(track.artist, "Test Artist")
        self.assertEqual(track.album_artist, "Test Album Artist")
        self.assertEqual(track.album, "Test Album")
        self.assertEqual(track.title, "Test Title")
        self.assertEqual(track.date, "2026")
        self.assertEqual(track.genres, ["Ambient", "Electronic"])
        self.assertEqual(track.track_number, "1/8")
        self.assertEqual(track.duration_seconds, 123.456)
        self.assertEqual(track.bitrate, 128000)
        self.assertEqual(mutagen_file.call_count, 2)

    def test_readable_file_without_tags_uses_unknown_album_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            path = root / "01 Mystery.flac"
            path.write_bytes(b"not real audio; mutagen is mocked")

            audio = SimpleNamespace(
                tags=None,
                info=SimpleNamespace(length=42.0, bitrate=128000),
            )

            with patch("kukicha.scanner.MutagenFile", return_value=audio):
                library = build_library([root])

        self.assertEqual(len(library.tracks), 1)
        track = library.tracks[0]
        self.assertIsNone(track.artist)
        self.assertEqual(track.album_artist, UNKNOWN_METADATA_TAG)
        self.assertEqual(track.album, UNKNOWN_METADATA_TAG)
        self.assertEqual(track.title, "01 Mystery")
        self.assertEqual(track.genres, [UNKNOWN_METADATA_TAG])
        self.assertEqual(track.duration_seconds, 42.0)

    def test_empty_tag_values_use_unknown_album_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "02 Empty Tags.flac"
            path.write_bytes(b"not real audio; mutagen is mocked")

            audio = SimpleNamespace(
                tags={
                    "ARTIST": "",
                    "ALBUMARTIST": "",
                    "ALBUM": "",
                    "TITLE": "",
                    "GENRE": "",
                },
                info=SimpleNamespace(length=12.0, bitrate=128000),
            )

            with patch("kukicha.scanner.MutagenFile", return_value=audio):
                track = scan_track(path)

        self.assertEqual(track.album_artist, UNKNOWN_METADATA_TAG)
        self.assertEqual(track.album, UNKNOWN_METADATA_TAG)
        self.assertEqual(track.title, "02 Empty Tags")
        self.assertEqual(track.genres, [UNKNOWN_METADATA_TAG])

    def test_composer_artist_fallback_is_not_replaced_by_unknown_album_artist(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "03 Composer.flac"
            path.write_bytes(b"not real audio; mutagen is mocked")

            audio = SimpleNamespace(
                tags={
                    "TCOM": ["Antonio Vivaldi"],
                    "ALBUM": ["The Four Seasons"],
                    "TITLE": ["Spring"],
                },
                info=SimpleNamespace(length=123.0, bitrate=128000),
            )

            with patch("kukicha.scanner.MutagenFile", return_value=audio):
                track = scan_track(path)

        self.assertEqual(track.artist, "Antonio Vivaldi")
        self.assertIsNone(track.album_artist)
        self.assertEqual(track.album, "The Four Seasons")
        self.assertEqual(track.title, "Spring")
        self.assertEqual(track.genres, [UNKNOWN_METADATA_TAG])

    def test_album_year_uses_original_date_before_release_date(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            path = root / "01 Test.opus"
            path.write_bytes(b"not real audio; mutagen is mocked")

            audio = SimpleNamespace(
                tags={
                    "ARTIST": ["Test Artist"],
                    "ALBUMARTIST": ["Test Album Artist"],
                    "ALBUM": ["Test Album"],
                    "TITLE": ["Test Title"],
                    "DATE": ["1999-05-04"],
                    "ORIGINALDATE": ["1984-10-12"],
                },
                info=SimpleNamespace(length=123.456, bitrate=128000),
            )

            with patch("kukicha.scanner.MutagenFile", return_value=audio):
                library = build_library([root])

        self.assertEqual(len(library.tracks), 1)
        self.assertEqual(library.tracks[0].date, "1984-10-12")

        albums = group_library_albums(library)

        self.assertEqual(len(albums), 1)
        self.assertEqual(albums[0].album_id, "test-album-artist::test-album")
        self.assertEqual(albums[0].year, 1984)


class ScannerProgressTest(unittest.TestCase):
    def test_build_library_reports_progress_every_500_music_files(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            paths = [root / f"{index:03d}.flac" for index in range(501)]
            progress_messages: list[str] = []

            def fake_scan_track(path: Path) -> TrackRecord:
                return TrackRecord(
                    path=str(path),
                    file_type="flac",
                    artist="Artist",
                    album_artist="Artist",
                    album="Album",
                    title=path.stem,
                )

            with (
                patch("kukicha.scanner.iter_music_files", return_value=paths),
                patch("kukicha.scanner.scan_track", side_effect=fake_scan_track),
            ):
                library = build_library([root], progress=progress_messages.append)

        self.assertEqual(len(library.tracks), 501)
        self.assertEqual(progress_messages[0], f"scanning root 1/1: {root.resolve()}")
        self.assertIn("scanned 500 music files", progress_messages)
        self.assertEqual(progress_messages[-1], "scanned 501 music files")


class ScannerPlaylistTest(unittest.TestCase):
    def test_build_library_parses_ascii_m3u_playlist(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            playlist_path = root / "streams.m3u"
            playlist_path.write_text(
                "\n".join(
                    (
                        "#EXTM3U",
                        "#PLAYLIST:ASCII Streams",
                        "#EXTINF:0,SomaFM: Deep Space One",
                        "https://ice6.somafm.com/deepspaceone-128-mp3",
                    )
                ),
                encoding="ascii",
            )

            library = build_library([root])

        self.assertEqual(len(library.playlists), 1)
        playlist = library.playlists[0]
        self.assertEqual(playlist.name, "ASCII Streams")
        self.assertEqual(playlist.path, str(playlist_path.resolve()))
        self.assertIn("ASCII Streams", playlist.cover_svg)
        self.assertEqual(len(playlist.items), 1)
        self.assertEqual(playlist.items[0].path, "https://ice6.somafm.com/deepspaceone-128-mp3")
        self.assertEqual(playlist.items[0].title, "SomaFM: Deep Space One")

    def test_build_library_skips_non_ascii_m3u_playlist(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            playlist_path = root / "legacy.m3u"
            playlist_path.write_bytes(
                "\n".join(
                    (
                        "#EXTM3U",
                        "#PLAYLIST:Café Streams",
                        "#EXTINF:0,SomaFM: Deep Space One",
                        "https://ice6.somafm.com/deepspaceone-128-mp3",
                    )
                ).encode("utf-8")
            )

            library = build_library([root])

        self.assertEqual(library.playlists, [])

    def test_build_library_parses_mixed_m3u8_playlist_after_tracks(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir) / "root"
            music_dir = root / "music"
            playlists_dir = root / "playlists"
            external_dir = Path(tempdir) / "external"
            music_dir.mkdir(parents=True)
            playlists_dir.mkdir()
            external_dir.mkdir()
            tracked_path = music_dir / "01 Tracked.flac"
            external_path = external_dir / "External.m4a"
            playlist_path = playlists_dir / "mixed.m3u8"
            tracked_path.write_bytes(b"not real audio; mutagen is mocked")
            playlist_path.write_text(
                "\n".join(
                    (
                        "#EXTM3U",
                        "#PLAYLIST:Awesome Music Playlist!!!",
                        "#EXTINF:123,External Title",
                        "#EXTGENRE:Electronic",
                        "#EXTALBUMARTURL:https://example.test/cover.jpg",
                        "../../external/External.m4a",
                        "#EXTINF:321,Tracked Metadata Ignored",
                        "../music/01 Tracked.flac",
                        "#EXTINF:0,SomaFM: Deep Space One",
                        "https://ice6.somafm.com/deepspaceone-128-mp3",
                    )
                ),
                encoding="utf-8",
            )

            audio = SimpleNamespace(
                tags={
                    "ARTIST": ["Track Artist"],
                    "ALBUMARTIST": ["Track Artist"],
                    "ALBUM": ["Track Album"],
                    "TITLE": ["Tracked Title"],
                },
                info=SimpleNamespace(length=321.0, bitrate=128000),
            )

            with patch("kukicha.scanner.MutagenFile", return_value=audio):
                library = build_library([root])

        self.assertEqual(len(library.tracks), 1)
        self.assertEqual(len(library.playlists), 1)
        playlist = library.playlists[0]
        self.assertEqual(playlist.name, "Awesome Music Playlist!!!")
        self.assertEqual(playlist.path, str(playlist_path.resolve()))
        self.assertEqual(playlist.root_position, 0)
        self.assertIn("Awesome Music Playlist", playlist.cover_svg)
        self.assertEqual(len(playlist.items), 3)

        external_item = playlist.items[0]
        self.assertEqual(external_item.path, str(external_path.resolve()))
        self.assertIsNone(external_item.track_id)
        self.assertEqual(external_item.title, "External Title")
        self.assertEqual(external_item.duration_seconds, 123.0)
        self.assertEqual(external_item.genre, "Electronic")
        self.assertEqual(external_item.cover_url, "https://example.test/cover.jpg")

        tracked_item = playlist.items[1]
        self.assertEqual(tracked_item.path, str(tracked_path.resolve()))
        self.assertIsNone(tracked_item.track_id)
        self.assertIsNone(tracked_item.title)

        url_item = playlist.items[2]
        self.assertEqual(url_item.path, "https://ice6.somafm.com/deepspaceone-128-mp3")
        self.assertEqual(url_item.title, "SomaFM: Deep Space One")
        self.assertEqual(url_item.duration_seconds, 0.0)


class ScannerExternalArtworkTest(unittest.TestCase):
    def test_build_library_picks_up_new_sidecar_cover_after_previous_scan(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            path = root / "01 Test.m4a"
            cover = root / "cover.jpg"
            path.write_bytes(b"not real audio; mutagen is mocked")

            audio = SimpleNamespace(
                tags={
                    "\xa9ART": ["Test Artist"],
                    "aART": ["Test Album Artist"],
                    "\xa9alb": ["Test Album"],
                    "\xa9nam": ["Test Title"],
                },
                info=SimpleNamespace(length=123.456, bitrate=256000),
            )

            def fake_thumbnail_artworks(artwork: object, *, heights: object) -> dict[int, object]:
                return {
                    int(height): artwork
                    for height in heights
                }

            clear_external_artwork_caches()
            try:
                with (
                    patch("kukicha.scanner.MutagenFile", return_value=audio),
                    patch("kukicha.scanner.thumbnail_artworks", side_effect=fake_thumbnail_artworks),
                ):
                    first_library = build_library([root])
                    self.assertEqual(len(first_library.tracks), 1)
                    self.assertIsNone(first_library.tracks[0].album_artwork)

                    cover.write_bytes(b"fake sidecar artwork bytes")

                    second_library = build_library([root])
                    self.assertEqual(len(second_library.tracks), 1)
                    self.assertIsNotNone(second_library.tracks[0].album_artwork)
            finally:
                clear_external_artwork_caches()


class ScannerTagWriteTest(unittest.TestCase):
    def test_write_album_audio_tags_only_updates_album_fields(self) -> None:
        class FakeEasyAudio(dict[str, list[str]]):
            def __init__(self) -> None:
                super().__init__(
                    {
                        "artist": ["Old Artist"],
                        "albumartist": ["Old Album Artist"],
                        "album": ["Old Album"],
                        "tracknumber": ["1"],
                        "title": ["Old Title"],
                        "genre": ["Old Genre"],
                    }
                )
                self.tags = self
                self.saved = False

            def save(self) -> None:
                self.saved = True

        audio = FakeEasyAudio()

        with patch("kukicha.scanner.MutagenFile", return_value=audio):
            write_album_audio_tags(
                Path("/tmp/test.mp3"),
                album_artist="New Album Artist",
                album="New Album",
                genre="Electronic",
            )

        self.assertEqual(audio["artist"], ["Old Artist"])
        self.assertEqual(audio["albumartist"], ["New Album Artist"])
        self.assertEqual(audio["album"], ["New Album"])
        self.assertEqual(audio["genre"], ["Electronic"])
        self.assertTrue(audio.saved)

    def test_write_track_audio_tags_sets_and_clears_easy_tags(self) -> None:
        class FakeEasyAudio(dict[str, list[str]]):
            def __init__(self) -> None:
                super().__init__(
                    {
                        "artist": ["Old Artist"],
                        "albumartist": ["Old Album Artist"],
                        "album": ["Old Album"],
                        "genre": ["Old Genre"],
                    }
                )
                self.tags = self
                self.saved = False

            def save(self) -> None:
                self.saved = True

        audio = FakeEasyAudio()

        with patch("kukicha.scanner.MutagenFile", return_value=audio):
            write_track_audio_tags(
                Path("/tmp/test.mp3"),
                artist="New Artist",
                album_artist="",
                album="New Album",
                track_number="7",
                title="New Title",
                genre="Electronic; Score",
            )

        self.assertEqual(audio["artist"], ["New Artist"])
        self.assertNotIn("albumartist", audio)
        self.assertEqual(audio["album"], ["New Album"])
        self.assertEqual(audio["tracknumber"], ["7"])
        self.assertEqual(audio["title"], ["New Title"])
        self.assertEqual(audio["genre"], ["Electronic; Score"])
        self.assertTrue(audio.saved)


if __name__ == "__main__":
    unittest.main()
