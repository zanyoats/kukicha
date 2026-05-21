from __future__ import annotations

from datetime import UTC, datetime
import io
from pathlib import Path
import tempfile
from threading import Event, Lock
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from kukicha.discogs import group_library_albums
from kukicha.models import MusicLibrary, TrackRecord, UNKNOWN_METADATA_TAG
from kukicha.library_sources import LibraryRootSource, RemoteRootConfig, canonical_s3_path
from kukicha.models import TrackSourceRecord
from kukicha.scanner import (
    PRIMARY_TAG_FIELDS,
    build_incremental_library_from_sources,
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

    def test_release_variants_split_same_artist_and_album(self) -> None:
        library = MusicLibrary(
            roots=[],
            tracks=[
                TrackRecord(
                    path="/music/ok-computer-us.flac",
                    artist="Radiohead",
                    album_artist="Radiohead",
                    album="OK Computer",
                    title="Airbag",
                    musicbrainz_release_variant="a7f",
                ),
                TrackRecord(
                    path="/music/ok-computer-uk.flac",
                    artist="Radiohead",
                    album_artist="Radiohead",
                    album="OK Computer",
                    title="Airbag",
                    musicbrainz_release_variant="19c",
                ),
                TrackRecord(
                    path="/music/ok-computer-untagged.flac",
                    artist="Radiohead",
                    album_artist="Radiohead",
                    album="OK Computer",
                    title="Airbag",
                ),
            ],
            supported_extensions=[],
            generated_at="2026-04-22T00:00:00+00:00",
        )

        albums = group_library_albums(library)

        self.assertEqual(
            [album.album_id for album in albums],
            [
                "radiohead::ok-computer::a7f",
                "radiohead::ok-computer::19c",
                "radiohead::ok-computer",
            ],
        )

    def test_group_library_albums_keeps_unicode_artist_names(self) -> None:
        library = MusicLibrary(
            roots=[],
            tracks=[
                TrackRecord(
                    path="/music/quiet-forest.flac",
                    artist="吉村弘",
                    album_artist="吉村弘",
                    album="Quiet Forest",
                    title="Sleep",
                ),
            ],
            supported_extensions=[],
            generated_at="2026-05-04T00:00:00+00:00",
        )

        albums = group_library_albums(library)

        self.assertEqual(len(albums), 1)
        self.assertEqual(albums[0].album_id, "吉村弘::quiet-forest")
        self.assertEqual(albums[0].artist, "吉村弘")


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
        self.assertIn(
            "root 1/1 progress: 500 music file(s) checked (500 read, 0 reused)",
            progress_messages,
        )
        self.assertIn(
            f"finished root 1/1: {root.resolve()} "
            "(501 music file(s), 501 read, 0 reused, 0 playlist file(s))",
            progress_messages,
        )
        self.assertEqual(progress_messages[-1], "scanned 501 music files")

    def test_build_library_can_report_new_paths_without_reused_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            keep_path = root / "Keep.flac"
            new_path = root / "New.flac"
            keep_path.write_bytes(b"keep")
            new_path.write_bytes(b"new")
            progress_messages: list[str] = []
            existing = TrackRecord(
                path=str(keep_path),
                file_type="flac",
                artist="Artist",
                album_artist="Artist",
                album="Album",
                title="Keep",
                file_modified_at_ns=keep_path.stat().st_mtime_ns,
                file_size_bytes=keep_path.stat().st_size,
            )

            def fake_scan_track(path: Path) -> TrackRecord:
                return TrackRecord(
                    path=str(path),
                    file_type="flac",
                    artist="Artist",
                    album_artist="Artist",
                    album="Album",
                    title=path.stem,
                )

            with patch("kukicha.scanner.scan_track", side_effect=fake_scan_track):
                build_incremental_library_from_sources(
                    [LibraryRootSource(position=0, path=str(root.resolve()))],
                    existing_tracks_by_path={str(keep_path.resolve()): existing},
                    progress=progress_messages.append,
                    report_new_paths=True,
                )

        joined = "\n".join(progress_messages)
        self.assertIn(f"reading new file: {new_path.resolve()}", joined)
        self.assertNotIn(str(keep_path.resolve()), joined)


class ScannerRemoteS3Test(unittest.TestCase):
    def remote(self) -> RemoteRootConfig:
        return RemoteRootConfig(
            name="Remote",
            endpoint_url="https://s3.example.test",
            bucket="bucket",
            prefix="tracks/",
        )

    def source(self, remote: RemoteRootConfig) -> LibraryRootSource:
        return LibraryRootSource(
            position=0,
            path=remote.root_path,
            kind="s3",
            source_json=remote.source_json,
        )

    def fake_client(self, metadata: dict[str, str] | None = None) -> object:
        audio_metadata = metadata or {}

        class FakeClient:
            def list_objects_v2(self, **_kwargs: object) -> dict[str, object]:
                return {
                    "Contents": [
                        {
                            "Key": "tracks/Album/01.flac",
                            "Size": 12,
                            "LastModified": datetime(2026, 5, 16, 12, tzinfo=UTC),
                            "ETag": '"audio-etag"',
                        },
                        {
                            "Key": "tracks/Album/cover.jpg",
                            "Size": 5,
                            "LastModified": datetime(2026, 5, 16, 13, tzinfo=UTC),
                            "ETag": '"cover-etag"',
                        },
                        {
                            "Key": "tracks/Album/notes.txt",
                            "Size": 1,
                            "LastModified": datetime(2026, 5, 16, 14, tzinfo=UTC),
                        },
                    ]
                }

            def get_object(self, **kwargs: object) -> dict[str, object]:
                key = kwargs["Key"]
                data = b"audio bytes" if key == "tracks/Album/01.flac" else b"cover"
                response: dict[str, object] = {"Body": io.BytesIO(data)}
                if key == "tracks/Album/01.flac":
                    response["Metadata"] = audio_metadata
                return response

        return FakeClient()

    def test_remote_scan_downloads_changed_track_and_cleans_tempdir(self) -> None:
        remote = self.remote()
        temp_dirs: list[Path] = []

        def fake_scan_track(path: Path) -> TrackRecord:
            temp_dirs.append(path.parent)
            self.assertTrue(path.is_file())
            self.assertTrue((path.parent / "cover.jpg").is_file())
            return TrackRecord(
                path=str(path),
                file_type="flac",
                artist="Artist",
                album_artist="Artist",
                album="Album",
                title="Track",
            )

        with patch("kukicha.scanner.scan_track", side_effect=fake_scan_track):
            result = build_incremental_library_from_sources(
                [self.source(remote)],
                existing_tracks_by_path={},
                s3_client_factory=lambda _remote: self.fake_client(),
            )

        track = result.library.tracks[0]
        self.assertEqual(track.path, canonical_s3_path(remote, "tracks/Album/01.flac"))
        self.assertEqual(result.scanned_paths, frozenset({track.path}))
        self.assertEqual(track.file_created_at, "2026-05-16T12:00:00+00:00")
        self.assertEqual(track.file_size_bytes, 12)
        self.assertEqual(track.sidecar_artwork_path, canonical_s3_path(remote, "tracks/Album/cover.jpg"))
        self.assertIsNotNone(track.source)
        self.assertEqual(track.source.object_key, "tracks/Album/01.flac")
        self.assertEqual(track.source.sidecar_object_key, "tracks/Album/cover.jpg")
        self.assertEqual(len(temp_dirs), 1)
        self.assertFalse(temp_dirs[0].exists())

    def test_remote_scan_prefers_uploaded_local_created_at_metadata(self) -> None:
        remote = self.remote()

        def fake_scan_track(path: Path) -> TrackRecord:
            return TrackRecord(
                path=str(path),
                file_type="flac",
                artist="Artist",
                album_artist="Artist",
                album="Album",
                title="Track",
            )

        with patch("kukicha.scanner.scan_track", side_effect=fake_scan_track):
            result = build_incremental_library_from_sources(
                [self.source(remote)],
                existing_tracks_by_path={},
                s3_client_factory=lambda _remote: self.fake_client(
                    {
                        "local-created-at": "2026-05-15T11:00:00+00:00",
                        "local-ctime": "2026-05-14T11:00:00+00:00",
                    }
                ),
            )

        self.assertEqual(
            result.library.tracks[0].file_created_at,
            "2026-05-15T11:00:00+00:00",
        )

    def test_remote_scan_falls_back_to_uploaded_local_ctime_metadata(self) -> None:
        remote = self.remote()

        def fake_scan_track(path: Path) -> TrackRecord:
            return TrackRecord(
                path=str(path),
                file_type="flac",
                artist="Artist",
                album_artist="Artist",
                album="Album",
                title="Track",
            )

        with patch("kukicha.scanner.scan_track", side_effect=fake_scan_track):
            result = build_incremental_library_from_sources(
                [self.source(remote)],
                existing_tracks_by_path={},
                s3_client_factory=lambda _remote: self.fake_client(
                    {
                        "local-created-at": "not a timestamp",
                        "local-ctime": "2026-05-14T11:00:00+00:00",
                    }
                ),
            )

        self.assertEqual(
            result.library.tracks[0].file_created_at,
            "2026-05-14T11:00:00+00:00",
        )

    def test_remote_scan_reuses_unchanged_source_metadata(self) -> None:
        remote = self.remote()
        track_path = canonical_s3_path(remote, "tracks/Album/01.flac")
        progress_messages: list[str] = []
        existing = TrackRecord(
            path=track_path,
            file_type="flac",
            artist="Artist",
            album_artist="Artist",
            album="Album",
            title="Stored",
            source=TrackSourceRecord(
                source_kind="s3",
                root_position=0,
                canonical_path=track_path,
                object_key="tracks/Album/01.flac",
                etag='"audio-etag"',
                last_modified="2026-05-16T12:00:00+00:00",
                size_bytes=12,
                sidecar_object_key="tracks/Album/cover.jpg",
                sidecar_etag='"cover-etag"',
                sidecar_last_modified="2026-05-16T13:00:00+00:00",
                sidecar_size_bytes=5,
            ),
        )

        with patch("kukicha.scanner.scan_track", side_effect=AssertionError("unexpected scan")):
            result = build_incremental_library_from_sources(
                [self.source(remote)],
                existing_tracks_by_path={track_path: existing},
                progress=progress_messages.append,
                report_new_paths=True,
                s3_client_factory=lambda _remote: self.fake_client(),
            )

        self.assertEqual(result.scanned_paths, frozenset())
        self.assertEqual(result.reused_paths, frozenset({track_path}))
        self.assertEqual(result.library.tracks[0].title, "Stored")
        self.assertNotIn("tracks/Album/01.flac", "\n".join(progress_messages))

    def test_remote_scan_reports_listing_and_root_progress(self) -> None:
        remote = self.remote()
        progress_messages: list[str] = []

        class FakeClient:
            def list_objects_v2(self, **kwargs: object) -> dict[str, object]:
                if kwargs.get("ContinuationToken") == "second":
                    return {
                        "Contents": [
                            {
                                "Key": "tracks/Album/02.flac",
                                "Size": 13,
                                "LastModified": datetime(2026, 5, 16, 13, tzinfo=UTC),
                                "ETag": '"audio-2"',
                            },
                        ]
                    }
                return {
                    "Contents": [
                        {
                            "Key": "tracks/Album/01.flac",
                            "Size": 12,
                            "LastModified": datetime(2026, 5, 16, 12, tzinfo=UTC),
                            "ETag": '"audio-1"',
                        },
                        {
                            "Key": "tracks/Album/cover.jpg",
                            "Size": 5,
                            "LastModified": datetime(2026, 5, 16, 14, tzinfo=UTC),
                            "ETag": '"cover"',
                        },
                    ],
                    "IsTruncated": True,
                    "NextContinuationToken": "second",
                }

            def get_object(self, **kwargs: object) -> dict[str, object]:
                key = str(kwargs["Key"])
                data = b"cover" if key.endswith(".jpg") else b"audio"
                return {"Body": io.BytesIO(data)}

        def fake_scan_track(path: Path) -> TrackRecord:
            return TrackRecord(
                path=str(path),
                file_type="flac",
                artist="Artist",
                album_artist="Artist",
                album="Album",
                title=path.stem,
            )

        with patch("kukicha.scanner.scan_track", side_effect=fake_scan_track):
            result = build_incremental_library_from_sources(
                [self.source(remote)],
                existing_tracks_by_path={},
                progress=progress_messages.append,
                progress_every=1,
                report_new_paths=True,
                s3_client_factory=lambda _remote: FakeClient(),
            )

        self.assertEqual(len(result.library.tracks), 2)
        self.assertIn(
            "root 1/1 listing remote objects: starting list for s3://bucket/tracks/",
            progress_messages,
        )
        self.assertIn(
            "root 1/1 listing remote objects: listed 2 object(s) across 1 page(s)",
            progress_messages,
        )
        self.assertIn(
            "root 1/1 listing remote objects: listed 3 object(s) across 2 page(s)",
            progress_messages,
        )
        self.assertIn(
            "root 1/1 found 2 remote music file(s) and 1 sidecar artwork file(s)",
            progress_messages,
        )
        self.assertIn(
            "root 1/1 reading new remote file 1/2: tracks/Album/01.flac",
            progress_messages,
        )
        self.assertIn(
            "root 1/1 progress: 2 music file(s) checked (2 read, 0 reused)",
            progress_messages,
        )
        self.assertIn(
            "finished root 1/1: Remote "
            "(2 music file(s), 2 read, 0 reused, 0 playlist file(s))",
            progress_messages,
        )

    def test_remote_scan_downloads_changed_tracks_in_parallel_and_preserves_order(self) -> None:
        remote = self.remote()

        class ParallelClient:
            def __init__(self) -> None:
                self.lock = Lock()
                self.started = 0
                self.active = 0
                self.max_active = 0
                self.all_started = Event()

            def list_objects_v2(self, **_kwargs: object) -> dict[str, object]:
                return {
                    "Contents": [
                        {
                            "Key": "tracks/Album/01.flac",
                            "Size": 12,
                            "LastModified": datetime(2026, 5, 16, 12, tzinfo=UTC),
                            "ETag": '"audio-1"',
                        },
                        {
                            "Key": "tracks/Album/02.flac",
                            "Size": 13,
                            "LastModified": datetime(2026, 5, 16, 13, tzinfo=UTC),
                            "ETag": '"audio-2"',
                        },
                    ]
                }

            def get_object(self, **kwargs: object) -> dict[str, object]:
                key = str(kwargs["Key"])
                with self.lock:
                    self.started += 1
                    self.active += 1
                    self.max_active = max(self.max_active, self.active)
                    if self.started >= 2:
                        self.all_started.set()
                try:
                    self.all_started.wait(timeout=2)
                    return {"Body": io.BytesIO(key.encode("utf-8"))}
                finally:
                    with self.lock:
                        self.active -= 1

        client = ParallelClient()

        def fake_scan_track(path: Path) -> TrackRecord:
            return TrackRecord(
                path=str(path),
                file_type="flac",
                artist="Artist",
                album_artist="Artist",
                album="Album",
                title=path.stem,
            )

        with patch("kukicha.scanner.scan_track", side_effect=fake_scan_track):
            result = build_incremental_library_from_sources(
                [self.source(remote)],
                existing_tracks_by_path={},
                remote_workers=2,
                s3_client_factory=lambda _remote: client,
            )

        self.assertGreaterEqual(client.max_active, 2)
        self.assertEqual(
            [track.source.object_key for track in result.library.tracks if track.source],
            ["tracks/Album/01.flac", "tracks/Album/02.flac"],
        )
        self.assertEqual([track.title for track in result.library.tracks], ["01", "02"])

    def test_remote_scan_cleans_tempdir_after_scan_error(self) -> None:
        remote = self.remote()
        temp_dirs: list[Path] = []

        def fail_scan(path: Path) -> TrackRecord:
            temp_dirs.append(path.parent)
            raise RuntimeError("boom")

        with patch("kukicha.scanner.scan_track", side_effect=fail_scan):
            with self.assertRaisesRegex(RuntimeError, "boom"):
                build_incremental_library_from_sources(
                    [self.source(remote)],
                    existing_tracks_by_path={},
                    s3_client_factory=lambda _remote: self.fake_client(),
                )

        self.assertEqual(len(temp_dirs), 1)
        self.assertFalse(temp_dirs[0].exists())


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
        self.assertIsNone(playlist.items[0].duration_seconds)
        self.assertTrue(playlist.items[0].duration_is_indeterminate)

    def test_build_library_parses_utf8_m3u_playlist(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            playlist_path = root / "unicode.m3u"
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

        self.assertEqual(len(library.playlists), 1)
        playlist = library.playlists[0]
        self.assertEqual(playlist.name, "Café Streams")
        self.assertEqual(playlist.path, str(playlist_path.resolve()))
        self.assertEqual(len(playlist.items), 1)
        self.assertEqual(playlist.items[0].path, "https://ice6.somafm.com/deepspaceone-128-mp3")
        self.assertEqual(playlist.items[0].title, "SomaFM: Deep Space One")

    def test_build_library_treats_negative_extinf_duration_as_indeterminate(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            playlist_path = root / "streams.m3u8"
            playlist_path.write_text(
                "\n".join(
                    (
                        "#EXTM3U",
                        "#EXTINF:-1,Live Stream",
                        "https://example.test/live",
                    )
                ),
                encoding="utf-8",
            )

            library = build_library([root])

        item = library.playlists[0].items[0]
        self.assertEqual(item.title, "Live Stream")
        self.assertIsNone(item.duration_seconds)
        self.assertTrue(item.duration_is_indeterminate)

    def test_build_library_parses_pls_playlist(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            music_dir = root / "music"
            music_dir.mkdir()
            tracked_path = music_dir / "01 Tracked.flac"
            playlist_path = root / "streams.pls"
            tracked_path.write_bytes(b"not real audio; mutagen is mocked")
            playlist_path.write_text(
                "\n".join(
                    (
                        "[playlist]",
                        "numberofentries=3",
                        "File1=https://ice6.somafm.com/cliqhop-256-mp3",
                        "Title1=SomaFM: cliqhop idm (#1)",
                        "Length1=-1",
                        "File2=music/01 Tracked.flac",
                        "Title2=Tracked Metadata Ignored",
                        "Length2=321",
                        "File3=https://example.test/archive.mp3",
                        "Title3=Archive Track",
                        "Length3=42",
                        "Version=2",
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
        self.assertEqual(playlist.name, "streams")
        self.assertEqual(playlist.path, str(playlist_path.resolve()))
        self.assertEqual(playlist.root_position, 0)
        self.assertIn("streams", playlist.cover_svg)
        self.assertEqual(len(playlist.items), 3)

        stream_item = playlist.items[0]
        self.assertEqual(stream_item.path, "https://ice6.somafm.com/cliqhop-256-mp3")
        self.assertEqual(stream_item.title, "SomaFM: cliqhop idm (#1)")
        self.assertIsNone(stream_item.duration_seconds)
        self.assertTrue(stream_item.duration_is_indeterminate)

        tracked_item = playlist.items[1]
        self.assertEqual(tracked_item.path, str(tracked_path.resolve()))
        self.assertIsNone(tracked_item.track_id)
        self.assertIsNone(tracked_item.title)

        archive_item = playlist.items[2]
        self.assertEqual(archive_item.path, "https://example.test/archive.mp3")
        self.assertEqual(archive_item.title, "Archive Track")
        self.assertEqual(archive_item.duration_seconds, 42.0)
        self.assertFalse(archive_item.duration_is_indeterminate)

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
        self.assertFalse(external_item.duration_is_indeterminate)
        self.assertEqual(external_item.genre, "Electronic")
        self.assertEqual(external_item.cover_url, "https://example.test/cover.jpg")

        tracked_item = playlist.items[1]
        self.assertEqual(tracked_item.path, str(tracked_path.resolve()))
        self.assertIsNone(tracked_item.track_id)
        self.assertIsNone(tracked_item.title)

        url_item = playlist.items[2]
        self.assertEqual(url_item.path, "https://ice6.somafm.com/deepspaceone-128-mp3")
        self.assertEqual(url_item.title, "SomaFM: Deep Space One")
        self.assertIsNone(url_item.duration_seconds)
        self.assertTrue(url_item.duration_is_indeterminate)


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
                genre="Electronic; Ambient",
            )

        self.assertEqual(audio["artist"], ["Old Artist"])
        self.assertEqual(audio["albumartist"], ["New Album Artist"])
        self.assertEqual(audio["album"], ["New Album"])
        self.assertEqual(audio["genre"], ["Electronic", "Ambient"])
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
        self.assertEqual(audio["genre"], ["Electronic", "Score"])
        self.assertTrue(audio.saved)


if __name__ == "__main__":
    unittest.main()
