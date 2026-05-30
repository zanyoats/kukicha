from __future__ import annotations

import io
from pathlib import Path
from tempfile import TemporaryDirectory, gettempdir
from threading import Event, Lock
import unittest
from unittest.mock import call, patch

from kukicha.cli import build_parser
from kukicha.commands.tools import (
    CopyToRemoteResult,
    bulk_tag_edit,
    copy_to_remote,
    format_copy_to_remote_summary,
    format_bulk_tag_edit_summary,
    remote_worker_source,
    run_bulk_tag_edit,
    run_copy_to_remote,
)
from kukicha.library_sources import RemoteRootConfig, canonical_s3_path
from kukicha.commands.youtube_audio import (
    YoutubeAudioDownloadResult,
    YoutubeAudioTools,
    download_and_split_chapters,
    download_playlist_audio_items,
    download_video_audio_file,
    download_youtube_audio,
    parse_chapters_file,
    require_youtube_download_destination,
    resolve_youtube_audio_tools,
    run_youtube_download_audio,
)
from kukicha.player_config import PlayerServerOptions
from kukicha.player_errors import PlayerConfigError


class BulkTagEditCommandTest(unittest.TestCase):
    def test_cli_accepts_bulk_tag_edit_subcommand(self) -> None:
        parser = build_parser()

        args = parser.parse_args(
            [
                "tools",
                "bulk-tag-edit",
                "--folder",
                "/tmp/music",
                "--album-artist",
                "Richard David James",
                "--album",
                "Soundcloud",
                "--genre",
                "Electronic",
            ]
        )

        self.assertEqual(args.folder, Path("/tmp/music"))
        self.assertEqual(args.album_artist, "Richard David James")
        self.assertEqual(args.album, "Soundcloud")
        self.assertEqual(args.genre, "Electronic")
        self.assertIs(args.func, run_bulk_tag_edit)

    def test_bulk_tag_edit_updates_supported_files_recursively(self) -> None:
        with TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            paths = [
                root / "01 Intro.mp3",
                root / "disc 2" / "02 Outro.flac",
            ]
            for path in paths:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"not real audio; mutagen is mocked")
            (root / "notes.txt").write_text("ignore me", encoding="utf-8")

            with patch("kukicha.commands.tools.write_album_audio_tags") as write_tags:
                result = bulk_tag_edit(
                    root,
                    album_artist="Richard David James",
                    album="Soundcloud",
                    genre="Electronic",
                )

        self.assertEqual(result.files_found, 2)
        self.assertEqual(result.files_updated, 2)
        self.assertEqual(result.files_failed, 0)
        self.assertEqual(
            write_tags.call_args_list,
            [
                call(
                    paths[0].resolve(),
                    album_artist="Richard David James",
                    album="Soundcloud",
                    genre="Electronic",
                ),
                call(
                    paths[1].resolve(),
                    album_artist="Richard David James",
                    album="Soundcloud",
                    genre="Electronic",
                ),
            ],
        )

    def test_run_bulk_tag_edit_reports_failures_and_returns_nonzero(self) -> None:
        with TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            good_path = root / "01 Good.mp3"
            bad_path = root / "02 Bad.mp3"
            good_path.write_bytes(b"not real audio; mutagen is mocked")
            bad_path.write_bytes(b"not real audio; mutagen is mocked")

            def fake_write(path: Path, **_kwargs: object) -> None:
                if path == bad_path.resolve():
                    raise OSError("failed to update tags")

            args = build_parser().parse_args(
                [
                    "tools",
                    "bulk-tag-edit",
                    "--folder",
                    str(root),
                    "--album-artist",
                    "Richard David James",
                    "--album",
                    "Soundcloud",
                    "--genre",
                    "Electronic",
                ]
            )

            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                patch("kukicha.commands.tools.write_album_audio_tags", side_effect=fake_write),
                patch("sys.stdout", stdout),
                patch("sys.stderr", stderr),
            ):
                exit_code = args.func(args)

        self.assertEqual(exit_code, 1)
        self.assertIn("music files found: 2", stdout.getvalue())
        self.assertIn("files updated: 1", stdout.getvalue())
        self.assertIn("files failed: 1", stdout.getvalue())
        self.assertIn(str(bad_path.resolve()), stderr.getvalue())
        self.assertIn("failed to update tags", stderr.getvalue())

    def test_format_bulk_tag_edit_summary(self) -> None:
        with TemporaryDirectory() as tempdir:
            result = bulk_tag_edit(
                Path(tempdir),
                album_artist="Richard David James",
                album="Soundcloud",
                genre="Electronic",
            )

        self.assertEqual(
            format_bulk_tag_edit_summary(result),
            "\n".join(
                [
                    f"folder: {Path(tempdir).resolve()}",
                    "music files found: 0",
                    "files updated: 0",
                    "files failed: 0",
                ]
            ),
        )


class CopyToRemoteCommandTest(unittest.TestCase):
    def test_cli_accepts_copy_to_remote_subcommand(self) -> None:
        parser = build_parser()

        args = parser.parse_args(
            [
                "tools",
                "copy-to-remote",
                "--remote",
                "archive",
                "--source",
                "/tmp/Album",
                "--source-children",
                "--destination-prefix",
                "/Compilations/",
                "--delete-source",
                "--remote-workers",
                "3",
            ]
        )

        self.assertEqual(args.remote, "archive")
        self.assertEqual(args.source, Path("/tmp/Album"))
        self.assertTrue(args.source_children)
        self.assertEqual(args.destination_prefix, "Compilations/")
        self.assertTrue(args.delete_source)
        self.assertEqual(args.remote_workers, 3)
        self.assertIs(args.func, run_copy_to_remote)

    def test_copy_to_remote_uploads_source_folder_under_remote_prefix(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            album = temp_path / "Album"
            album.mkdir()
            audio = album / "01.flac"
            cover = album / "cover.jpg"
            notes = album / "notes.bin"
            audio.write_bytes(b"audio")
            cover.write_bytes(b"cover")
            notes.write_bytes(b"notes")
            client = FakeS3Client()

            with (
                patch(
                    "kukicha.commands.tools.file_created_at",
                    return_value="2026-05-16T10:00:00+00:00",
                ),
                patch("kukicha.audio_types.mimetypes.guess_type", return_value=(None, None)),
            ):
                result = copy_to_remote(
                    album,
                    remote_name="archive",
                    options=self.make_options(temp_path),
                    s3_client_factory=lambda _remote: client,
                )

        self.assertEqual(result.files_found, 3)
        self.assertEqual(result.files_uploaded, 3)
        self.assertEqual(result.files_failed, 0)
        puts_by_key = {str(item["Key"]): item for item in client.puts}
        self.assertEqual(
            sorted(puts_by_key),
            sorted([
                "tracks/Album/01.flac",
                "tracks/Album/cover.jpg",
                "tracks/Album/notes.bin",
            ]),
        )
        self.assertEqual(puts_by_key["tracks/Album/01.flac"]["Body"], b"audio")
        self.assertEqual(
            puts_by_key["tracks/Album/01.flac"]["ContentType"],
            "audio/flac",
        )
        self.assertEqual(
            puts_by_key["tracks/Album/cover.jpg"]["ContentType"],
            "image/jpeg",
        )
        self.assertEqual(
            puts_by_key["tracks/Album/01.flac"]["Metadata"]["local-created-at"],
            "2026-05-16T10:00:00+00:00",
        )
        self.assertIn("local-ctime", puts_by_key["tracks/Album/01.flac"]["Metadata"])
        self.assertEqual(
            puts_by_key["tracks/Album/notes.bin"]["ContentType"],
            "application/octet-stream",
        )

    def test_copy_to_remote_uploads_source_folder_under_destination_prefix(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            album = temp_path / "Compilations" / "Lanquidity (Definitive Edition)"
            album.mkdir(parents=True)
            (album / "01.flac").write_bytes(b"audio")
            client = FakeS3Client()

            result = copy_to_remote(
                album,
                remote_name="archive",
                options=self.make_options(temp_path),
                destination_prefix="Compilations",
                s3_client_factory=lambda _remote: client,
            )

        self.assertEqual(result.destination_prefix, "Compilations/")
        self.assertEqual(
            [item["Key"] for item in client.puts],
            ["tracks/Compilations/Lanquidity (Definitive Edition)/01.flac"],
        )

    def test_copy_to_remote_skips_linux_and_macos_filesystem_junk(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            album = temp_path / "Album"
            album.mkdir()
            (album / "01.flac").write_bytes(b"audio")
            for filename in (
                ".DS_Store",
                "._01.flac",
                ".localized",
                "Icon\r",
                ".directory",
                ".nfs0000000000000001",
                ".fuse_hidden00000001",
            ):
                (album / filename).write_bytes(b"junk")
            for dirname in (
                ".Trashes",
                ".Spotlight-V100",
                ".fseventsd",
                ".TemporaryItems",
                ".AppleDouble",
                "__MACOSX",
                "lost+found",
                ".Trash",
                ".Trash-1000",
            ):
                junk_dir = album / dirname
                junk_dir.mkdir()
                (junk_dir / "02.flac").write_bytes(b"junk")
            client = FakeS3Client()

            result = copy_to_remote(
                album,
                remote_name="archive",
                options=self.make_options(temp_path),
                s3_client_factory=lambda _remote: client,
            )

        self.assertEqual(result.files_found, 1)
        self.assertEqual(result.files_uploaded, 1)
        self.assertEqual(
            [item["Key"] for item in client.puts],
            ["tracks/Album/01.flac"],
        )

    def test_copy_to_remote_emits_progress_messages(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            album = temp_path / "Album"
            album.mkdir()
            (album / "01.flac").write_bytes(b"audio")
            client = FakeS3Client()
            messages: list[str] = []

            copy_to_remote(
                album,
                remote_name="archive",
                options=self.make_options(temp_path),
                s3_client_factory=lambda _remote: client,
                status=messages.append,
            )

        self.assertEqual(
            messages[0],
            "found 1 file(s) in 1 source item(s); uploading to archive",
        )
        self.assertRegex(messages[1], r"^remote workers: \d+ \(auto\)$")
        self.assertEqual(
            messages[2:],
            [
                "uploading 1/1: Album/01.flac",
                "uploaded 1/1: tracks/Album/01.flac",
            ],
        )

    def test_copy_to_remote_reports_configured_remote_workers(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            album = temp_path / "Album"
            album.mkdir()
            (album / "01.flac").write_bytes(b"audio")
            messages: list[str] = []

            copy_to_remote(
                album,
                remote_name="archive",
                options=self.make_options(temp_path, remote_workers=3),
                s3_client_factory=lambda _remote: FakeS3Client(),
                status=messages.append,
            )

        self.assertIn("remote workers: 3 (configured)", messages)

    def test_run_copy_to_remote_writes_progress_messages_to_stderr(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            source = temp_path / "Album"
            source.mkdir()
            options = self.make_options(temp_path)
            args = build_parser().parse_args(
                [
                    "tools",
                    "copy-to-remote",
                    "--remote",
                    "archive",
                    "--source",
                    str(source),
                    "--destination-prefix",
                    "Compilations",
                    "--remote-workers",
                    "4",
                ]
            )

            def fake_copy_to_remote(
                source: Path,
                *,
                remote_name: str,
                options: PlayerServerOptions,
                source_children: bool = False,
                delete_source: bool = False,
                destination_prefix: str = "",
                remote_workers: int | None = None,
                status: object = None,
            ) -> CopyToRemoteResult:
                self.assertEqual(remote_name, "archive")
                self.assertEqual(destination_prefix, "Compilations/")
                self.assertEqual(remote_workers, 4)
                if not callable(status):
                    raise AssertionError("status callback was not passed")
                status("uploading 1/1: Album/01.flac")
                return CopyToRemoteResult(
                    source=source.resolve(),
                    remote=options.remote_roots[0],
                    source_children=source_children,
                    delete_source=delete_source,
                    files_found=1,
                    files_uploaded=1,
                    upload_errors=(),
                    deleted_sources=(),
                    delete_errors=(),
                )

            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                patch("kukicha.commands.tools.load_player_options", return_value=options),
                patch(
                    "kukicha.commands.tools.copy_to_remote",
                    side_effect=fake_copy_to_remote,
                ),
                patch("sys.stdout", stdout),
                patch("sys.stderr", stderr),
            ):
                exit_code = args.func(args)

        self.assertEqual(exit_code, 0)
        self.assertIn("files uploaded: 1", stdout.getvalue())
        self.assertIn(
            "[copy-to-remote] uploading 1/1: Album/01.flac",
            stderr.getvalue(),
        )

    def test_run_copy_to_remote_loads_config_without_auth(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            source = temp_path / "Album"
            source.mkdir()
            config_path = temp_path / "kukicha.toml"
            config_path.write_text(
                "\n".join(
                    (
                        "[[remote_roots]]",
                        "name = 'archive'",
                        "endpoint_url = 'https://s3.example.test'",
                        "bucket = 'music-bucket'",
                        "prefix = 'tracks/'",
                    )
                ),
                encoding="utf-8",
            )
            args = build_parser().parse_args(
                [
                    "--config",
                    str(config_path),
                    "tools",
                    "copy-to-remote",
                    "--remote",
                    "archive",
                    "--source",
                    str(source),
                ]
            )

            def fake_copy_to_remote(
                source: Path,
                *,
                remote_name: str,
                options: PlayerServerOptions,
                source_children: bool = False,
                delete_source: bool = False,
                destination_prefix: str = "",
                remote_workers: int | None = None,
                status: object = None,
            ) -> CopyToRemoteResult:
                self.assertEqual(remote_name, "archive")
                self.assertEqual(destination_prefix, "")
                self.assertIsNone(options.auth)
                self.assertEqual(options.remote_roots[0].name, "archive")
                return CopyToRemoteResult(
                    source=source.resolve(),
                    remote=options.remote_roots[0],
                    source_children=source_children,
                    delete_source=delete_source,
                    files_found=0,
                    files_uploaded=0,
                    upload_errors=(),
                    deleted_sources=(),
                    delete_errors=(),
                )

            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                patch(
                    "kukicha.commands.tools.copy_to_remote",
                    side_effect=fake_copy_to_remote,
                ),
                patch("sys.stdout", stdout),
                patch("sys.stderr", stderr),
            ):
                exit_code = args.func(args)

        self.assertEqual(exit_code, 0)
        self.assertIn("files uploaded: 0", stdout.getvalue())
        self.assertNotIn("[auth] section is required", stderr.getvalue())

    def test_copy_to_remote_source_children_uploads_each_child_under_prefix(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            (temp_path / "Album1").mkdir()
            (temp_path / "Album1" / "01.flac").write_bytes(b"one")
            (temp_path / "Album2").mkdir()
            (temp_path / "Album2" / "02.flac").write_bytes(b"two")
            (temp_path / "loose.txt").write_text("loose", encoding="utf-8")
            client = FakeS3Client()

            result = copy_to_remote(
                temp_path,
                remote_name="archive",
                options=self.make_options(temp_path),
                source_children=True,
                s3_client_factory=lambda _remote: client,
            )

        self.assertEqual(result.files_found, 3)
        self.assertEqual(
            sorted(item["Key"] for item in client.puts),
            [
                "tracks/Album1/01.flac",
                "tracks/Album2/02.flac",
                "tracks/loose.txt",
            ],
        )

    def test_copy_to_remote_requires_directory_source(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            source = temp_path / "track.flac"
            source.write_bytes(b"audio")

            with self.assertRaisesRegex(NotADirectoryError, "source is not a folder"):
                copy_to_remote(
                    source,
                    remote_name="archive",
                    options=self.make_options(temp_path),
                    s3_client_factory=lambda _remote: FakeS3Client(),
                )

    def test_copy_to_remote_rejects_missing_and_duplicate_remote_names(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            source = temp_path / "Album"
            source.mkdir()

            with self.assertRaisesRegex(ValueError, "remote root not found"):
                copy_to_remote(
                    source,
                    remote_name="missing",
                    options=self.make_options(temp_path),
                    s3_client_factory=lambda _remote: FakeS3Client(),
                )

            options = self.make_options(
                temp_path,
                remote_roots=(
                    self.remote("archive", bucket="one", prefix="one/"),
                    self.remote("archive", bucket="two", prefix="two/"),
                ),
            )
            with self.assertRaisesRegex(ValueError, "remote root name is ambiguous"):
                copy_to_remote(
                    source,
                    remote_name="archive",
                    options=options,
                    s3_client_factory=lambda _remote: FakeS3Client(),
                )

    def test_copy_to_remote_uses_configured_profile_for_write_client(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            album = temp_path / "Album"
            album.mkdir()
            (album / "01.flac").write_bytes(b"audio")
            captured: dict[str, RemoteRootConfig] = {}

            def fake_factory(remote: RemoteRootConfig) -> FakeS3Client:
                captured["remote"] = remote
                return FakeS3Client()

            copy_to_remote(
                album,
                remote_name="archive",
                options=self.make_options(
                    temp_path,
                    remote_roots=(
                        self.remote("archive", profile="music-profile"),
                    ),
                ),
                s3_client_factory=fake_factory,
            )

        self.assertEqual(captured["remote"].profile, "music-profile")

    def test_copy_to_remote_delete_source_removes_only_successful_children(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            good = temp_path / "Good"
            bad = temp_path / "Bad"
            good.mkdir()
            bad.mkdir()
            (good / "01.flac").write_bytes(b"good")
            (bad / "02.flac").write_bytes(b"bad")
            client = FakeS3Client(fail_keys={"tracks/Bad/02.flac"})

            result = copy_to_remote(
                temp_path,
                remote_name="archive",
                options=self.make_options(temp_path),
                source_children=True,
                delete_source=True,
                s3_client_factory=lambda _remote: client,
            )

            self.assertTrue(temp_path.exists())
            self.assertFalse(good.exists())
            self.assertTrue(bad.exists())

        self.assertEqual(result.files_found, 2)
        self.assertEqual(result.files_uploaded, 1)
        self.assertEqual(result.files_failed, 1)
        self.assertEqual(result.deleted_sources, (good.resolve(),))

    def test_copy_to_remote_uploads_files_in_parallel_with_override(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            album = temp_path / "Album"
            album.mkdir()
            (album / "01.flac").write_bytes(b"one")
            (album / "02.flac").write_bytes(b"two")
            client = BlockingS3Client(expected_starts=2)

            result = copy_to_remote(
                album,
                remote_name="archive",
                options=self.make_options(temp_path, remote_workers=1),
                remote_workers=2,
                s3_client_factory=lambda _remote: client,
            )

        self.assertEqual(result.files_uploaded, 2)
        self.assertGreaterEqual(client.max_active, 2)
        self.assertEqual(
            sorted(item["Key"] for item in client.puts),
            ["tracks/Album/01.flac", "tracks/Album/02.flac"],
        )

    def test_remote_worker_source_labels_overrides_config_and_auto(self) -> None:
        self.assertEqual(remote_worker_source(4, 2), "override")
        self.assertEqual(remote_worker_source(None, 2), "configured")
        self.assertEqual(remote_worker_source(None, None), "auto")

    def test_format_copy_to_remote_summary_includes_delete_counts(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            album = temp_path / "Album"
            album.mkdir()
            (album / "01.flac").write_bytes(b"audio")
            result = copy_to_remote(
                album,
                remote_name="archive",
                options=self.make_options(temp_path),
                delete_source=True,
                s3_client_factory=lambda _remote: FakeS3Client(),
            )

        summary = format_copy_to_remote_summary(result)

        self.assertIn("files found: 1", summary)
        self.assertIn("files uploaded: 1", summary)
        self.assertIn("sources deleted: 1", summary)

    def make_options(
        self,
        temp_path: Path,
        *,
        remote_roots: tuple[RemoteRootConfig, ...] | None = None,
        remote_workers: int | None = None,
    ) -> PlayerServerOptions:
        return PlayerServerOptions(
            config_path=temp_path / "kukicha.toml",
            database=temp_path / "kukicha.sqlite",
            ffmpeg_path=None,
            remote_roots=remote_roots or (self.remote("archive"),),
            remote_workers=remote_workers,
        )

    def remote(
        self,
        name: str,
        *,
        bucket: str = "bucket",
        prefix: str = "tracks/",
        profile: str | None = None,
    ) -> RemoteRootConfig:
        return RemoteRootConfig(
            name=name,
            endpoint_url="https://s3.example.test",
            bucket=bucket,
            prefix=prefix,
            profile=profile,
        )


class FakeS3Client:
    def __init__(self, *, fail_keys: set[str] | None = None) -> None:
        self.fail_keys = fail_keys or set()
        self.puts: list[dict[str, object]] = []

    def put_object(self, **kwargs: object) -> dict[str, object]:
        key = str(kwargs["Key"])
        if key in self.fail_keys:
            raise OSError("upload failed")
        body = kwargs["Body"]
        if not hasattr(body, "read"):
            raise AssertionError("Body must be readable")
        self.puts.append(
            {
                "Bucket": kwargs["Bucket"],
                "Key": key,
                "Body": body.read(),
                "ContentType": kwargs["ContentType"],
                "Metadata": kwargs["Metadata"],
            }
        )
        return {}


class BlockingS3Client(FakeS3Client):
    def __init__(self, *, expected_starts: int) -> None:
        super().__init__()
        self.expected_starts = expected_starts
        self.started = 0
        self.active = 0
        self.max_active = 0
        self.lock = Lock()
        self.all_started = Event()

    def put_object(self, **kwargs: object) -> dict[str, object]:
        with self.lock:
            self.started += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            if self.started >= self.expected_starts:
                self.all_started.set()
        try:
            self.all_started.wait(timeout=2)
            return super().put_object(**kwargs)
        finally:
            with self.lock:
                self.active -= 1


class YoutubeAudioDownloadCommandTest(unittest.TestCase):
    def test_cli_accepts_youtube_audio_download_subcommand(self) -> None:
        parser = build_parser()

        args = parser.parse_args(
            [
                "tools",
                "yt-download-audio",
                "--split-into-chapters",
                "--chapters-file",
                "/tmp/chapters.txt",
                "https://www.youtube.com/watch?v=abc123",
                "--verbose",
            ]
        )

        self.assertEqual(args.url, "https://www.youtube.com/watch?v=abc123")
        self.assertEqual(args.chapters_file, Path("/tmp/chapters.txt"))
        self.assertTrue(args.split_into_chapters)
        self.assertTrue(args.verbose)
        self.assertIs(args.func, run_youtube_download_audio)

    def test_cli_rejects_youtube_audio_chapter_download_subcommand(self) -> None:
        parser = build_parser()
        stderr = io.StringIO()

        with (
            patch("sys.stderr", stderr),
            self.assertRaises(SystemExit) as raised,
        ):
            parser.parse_args(
                [
                    "tools",
                    "yt-download-audio-chapters",
                    "https://www.youtube.com/watch?v=abc123",
                ]
            )

        self.assertEqual(raised.exception.code, 2)

    def test_parse_chapters_file_accepts_timestamp_lines(self) -> None:
        with TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "chapters.txt"
            path.write_text(
                "\n".join(
                    [
                        "# downloaded album chapters",
                        "",
                        "0:00 Intro",
                        "03:12 - Track Two",
                        "1:02:03.5 Finale",
                    ]
                ),
                encoding="utf-8",
            )

            chapters = parse_chapters_file(path)

        self.assertEqual(
            chapters,
            [
                {"start_time": 0, "title": "Intro", "end_time": 192},
                {"start_time": 192, "title": "Track Two", "end_time": 3723.5},
                {"start_time": 3723.5, "title": "Finale"},
            ],
        )

    def test_parse_chapters_file_rejects_invalid_input(self) -> None:
        with TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "chapters.txt"
            cases = [
                ("", "did not contain any chapters"),
                ("00:00\n", "chapter line 1 is missing a title"),
                ("not a chapter\n", "chapter line 1 must use"),
                ("00:00 Intro\n00:00 Again\n", "chapter line 2 timestamp"),
                ("00:61 Bad\n", "chapter line 1: invalid timestamp seconds"),
            ]

            for text, message in cases:
                with self.subTest(text=text):
                    path.write_text(text, encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, message):
                        parse_chapters_file(path)

            missing_path = Path(tempdir) / "missing.txt"
            with self.assertRaisesRegex(FileNotFoundError, "chapters file not found"):
                parse_chapters_file(missing_path)

    def test_run_youtube_audio_download_requires_configured_root(self) -> None:
        with TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "kukicha.toml"
            config_path.write_text("log_level = 'INFO'\n", encoding="utf-8")
            args = build_parser().parse_args(
                [
                    "--config",
                    str(config_path),
                    "tools",
                    "yt-download-audio",
                    "https://www.youtube.com/watch?v=abc123",
                ]
            )

            stderr = io.StringIO()
            with patch("sys.stderr", stderr):
                exit_code = args.func(args)

        self.assertEqual(exit_code, 1)
        self.assertIn("youtube_download_root must be set", stderr.getvalue())

    def test_run_youtube_audio_download_loads_config_without_auth(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            config_path = temp_path / "kukicha.toml"
            config_path.write_text(
                "roots = ['music']\nyoutube_download_root = 'music'\n",
                encoding="utf-8",
            )
            args = build_parser().parse_args(
                [
                    "--config",
                    str(config_path),
                    "tools",
                    "yt-download-audio",
                    "--chapters-file",
                    str(temp_path / "chapters.txt"),
                    "https://www.youtube.com/watch?v=abc123",
                ]
            )

            def fake_download_youtube_audio(
                url: str,
                *,
                options: PlayerServerOptions,
                verbose: bool = False,
                chapters_file: Path | None = None,
                split_into_chapters: bool = False,
                status: object = None,
            ) -> YoutubeAudioDownloadResult:
                self.assertEqual(url, "https://www.youtube.com/watch?v=abc123")
                self.assertFalse(verbose)
                self.assertEqual(chapters_file, temp_path / "chapters.txt")
                self.assertTrue(split_into_chapters)
                self.assertIsNone(options.auth)
                self.assertEqual(options.youtube_download_root, "music")
                return YoutubeAudioDownloadResult(
                    output_dir=temp_path / "music" / ".kukicha" / "yt" / "Album [abc123]",
                    files_written=0,
                    media_id="abc123",
                    title="Album",
                    mode="video",
                    chapters_reported=0,
                )

            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                patch(
                    "kukicha.commands.youtube_audio.download_youtube_audio",
                    side_effect=fake_download_youtube_audio,
                ),
                patch("sys.stdout", stdout),
                patch("sys.stderr", stderr),
            ):
                exit_code = args.func(args)

        self.assertEqual(exit_code, 0)
        self.assertIn("Done. Final audio written to:", stdout.getvalue())
        self.assertEqual(stderr.getvalue(), "")

    def test_youtube_audio_destination_requires_configured_local_root(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            options = self.make_options(
                temp_path,
                roots=(temp_path / "music",),
                youtube_download_root=str(temp_path / "other"),
            )

            with self.assertRaisesRegex(PlayerConfigError, "must match"):
                require_youtube_download_destination(options)

    def test_youtube_audio_destination_rejects_ambiguous_local_and_remote_root(
        self,
    ) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            options = self.make_options(
                temp_path,
                roots=((temp_path / "music"),),
                remote_roots=(
                    RemoteRootConfig("music", "https://s3.example.test", "bucket"),
                ),
                youtube_download_root="music",
            )

            with self.assertRaisesRegex(PlayerConfigError, "ambiguous"):
                require_youtube_download_destination(options)

    def test_resolve_youtube_audio_tools_checks_required_programs(self) -> None:
        with TemporaryDirectory() as tempdir:
            options = self.make_options(Path(tempdir))

            def fake_which(program: str) -> str | None:
                return {
                    "ffmpeg": "/usr/local/bin/ffmpeg",
                    "ffprobe": "/usr/local/bin/ffprobe",
                    "deno": "/usr/local/bin/deno",
                }.get(program)

            with (
                patch("kukicha.commands.youtube_audio.shutil.which", side_effect=fake_which),
                patch("kukicha.commands.youtube_audio.check_deno_version") as check_deno,
            ):
                tools = resolve_youtube_audio_tools(options)

        self.assertEqual(
            tools,
            YoutubeAudioTools(
                ffmpeg="/usr/local/bin/ffmpeg",
                ffprobe="/usr/local/bin/ffprobe",
                deno="/usr/local/bin/deno",
            ),
        )
        check_deno.assert_called_once_with("/usr/local/bin/deno")

    def test_resolve_youtube_audio_tools_rejects_missing_deno(self) -> None:
        with TemporaryDirectory() as tempdir:
            options = self.make_options(Path(tempdir))

            def fake_which(program: str) -> str | None:
                return {
                    "ffmpeg": "/usr/local/bin/ffmpeg",
                    "ffprobe": "/usr/local/bin/ffprobe",
                }.get(program)

            with patch("kukicha.commands.youtube_audio.shutil.which", side_effect=fake_which):
                with self.assertRaisesRegex(RuntimeError, "deno"):
                    resolve_youtube_audio_tools(options)

    def test_download_youtube_audio_video_uses_config_path_and_cleans_temp(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            download_root = (temp_path / "music").resolve(strict=False)
            download_path = download_root / ".kukicha" / "yt"
            options = self.make_options(temp_path, roots=(download_root,))
            tools = YoutubeAudioTools(
                ffmpeg="/usr/local/bin/ffmpeg",
                ffprobe="/usr/local/bin/ffprobe",
                deno="/usr/local/bin/deno",
            )
            captured: dict[str, Path] = {}
            messages: list[str] = []

            def fake_download(
                _url: str,
                *,
                stage_root: Path,
                temp_dir: Path,
                tools: YoutubeAudioTools,
                verbose: bool,
            ) -> None:
                captured["stage_root"] = stage_root
                captured["temp_dir"] = temp_dir
                captured["source"] = stage_root / "source"
                captured["source"].mkdir(parents=True)

            def fake_find(stage_root: Path, *, tools: YoutubeAudioTools) -> list[Path]:
                return [
                    stage_root / "source" / "source.webm",
                ]

            def fake_copy(
                _input_path: Path,
                output_path: Path,
                *,
                tools: YoutubeAudioTools,
            ) -> None:
                output_path.write_bytes(b"audio")

            with (
                patch(
                    "kukicha.commands.youtube_audio.resolve_youtube_audio_tools",
                    return_value=tools,
                ),
                patch(
                    "kukicha.commands.youtube_audio.extract_youtube_info",
                    return_value={
                        "id": "abc123",
                        "title": "Album: Title",
                        "chapters": [{"title": "One"}, {"title": "Two"}],
                    },
                ),
                patch(
                    "kukicha.commands.youtube_audio.download_video_audio_file",
                    side_effect=fake_download,
                ),
                patch(
                    "kukicha.commands.youtube_audio.find_stage_video_files",
                    side_effect=fake_find,
                ),
                patch("kukicha.commands.youtube_audio.audio_codec", return_value="opus"),
                patch(
                    "kukicha.commands.youtube_audio.copy_audio_without_transcoding",
                    side_effect=fake_copy,
                ),
                patch("kukicha.commands.youtube_audio.assert_audio_only"),
            ):
                result = download_youtube_audio(
                    "https://www.youtube.com/watch?v=abc123",
                    options=options,
                    status=messages.append,
                )

            temp_root = captured["stage_root"].parent
            os_temp = Path(gettempdir()).resolve(strict=False)
            self.assertTrue(
                captured["stage_root"].resolve(strict=False).is_relative_to(os_temp)
            )
            self.assertEqual(captured["temp_dir"], temp_root / "yt-dlp")
            self.assertFalse(temp_root.exists())
            expected_output_dir = download_path / "Album_ Title [abc123]"
            expected_output = expected_output_dir / "Album_ Title [abc123].opus"
            self.assertEqual(result.output_dir, expected_output_dir)
            self.assertEqual(result.mode, "video")
            self.assertEqual(result.files_written, 1)
            self.assertTrue(expected_output.exists())
            self.assertFalse((download_path / "Album_ Title [abc123].opus").exists())
            self.assertIn(f"Final output directory: {expected_output_dir}", messages)

    def test_download_youtube_audio_video_split_into_chapters_uses_config_path(
        self,
    ) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            download_root = (temp_path / "music").resolve(strict=False)
            download_path = download_root / ".kukicha" / "yt"
            options = self.make_options(temp_path, roots=(download_root,))
            tools = YoutubeAudioTools(
                ffmpeg="/usr/local/bin/ffmpeg",
                ffprobe="/usr/local/bin/ffprobe",
                deno="/usr/local/bin/deno",
            )
            captured: dict[str, Path] = {}
            messages: list[str] = []

            def fake_download(
                _url: str,
                *,
                stage_root: Path,
                temp_dir: Path,
                tools: YoutubeAudioTools,
                verbose: bool,
                manual_chapters: object = None,
            ) -> None:
                captured["stage_root"] = stage_root
                captured["temp_dir"] = temp_dir
                captured["chapters"] = stage_root / "chapters"
                captured["chapters"].mkdir(parents=True)

            def fake_find(stage_root: Path, *, tools: YoutubeAudioTools) -> list[Path]:
                return [
                    stage_root / "chapters" / "001 - One.webm",
                    stage_root / "chapters" / "002 - Two.webm",
                ]

            def fake_copy(
                _input_path: Path,
                output_path: Path,
                *,
                tools: YoutubeAudioTools,
            ) -> None:
                output_path.write_bytes(b"audio")

            with (
                patch(
                    "kukicha.commands.youtube_audio.resolve_youtube_audio_tools",
                    return_value=tools,
                ),
                patch(
                    "kukicha.commands.youtube_audio.extract_youtube_info",
                    return_value={
                        "id": "abc123",
                        "title": "Album: Title",
                        "chapters": [{"title": "One"}, {"title": "Two"}],
                    },
                ),
                patch(
                    "kukicha.commands.youtube_audio.download_and_split_chapters",
                    side_effect=fake_download,
                ),
                patch(
                    "kukicha.commands.youtube_audio.find_stage_chapter_files",
                    side_effect=fake_find,
                ),
                patch("kukicha.commands.youtube_audio.audio_codec", return_value="opus"),
                patch(
                    "kukicha.commands.youtube_audio.copy_audio_without_transcoding",
                    side_effect=fake_copy,
                ),
                patch("kukicha.commands.youtube_audio.assert_audio_only"),
            ):
                result = download_youtube_audio(
                    "https://www.youtube.com/watch?v=abc123",
                    options=options,
                    split_into_chapters=True,
                    status=messages.append,
                )

            temp_root = captured["stage_root"].parent
            os_temp = Path(gettempdir()).resolve(strict=False)
            self.assertTrue(
                captured["stage_root"].resolve(strict=False).is_relative_to(os_temp)
            )
            self.assertEqual(captured["temp_dir"], temp_root / "yt-dlp")
            self.assertFalse(temp_root.exists())
            self.assertEqual(result.output_dir, download_path / "Album_ Title [abc123]")
            self.assertEqual(result.mode, "video")
            self.assertEqual(result.files_written, 2)
            self.assertTrue((result.output_dir / "001 - One.opus").exists())
            self.assertTrue((result.output_dir / "002 - Two.opus").exists())
            self.assertIn(f"Final output directory: {result.output_dir}", messages)

    def test_download_youtube_audio_chapters_file_implies_split_into_chapters(
        self,
    ) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            chapters_path = temp_path / "chapters.txt"
            chapters_path.write_text(
                "0:00 Manual One\n1:00 Manual Two\n",
                encoding="utf-8",
            )
            options = self.make_options(temp_path)
            tools = YoutubeAudioTools(
                ffmpeg="/usr/local/bin/ffmpeg",
                ffprobe="/usr/local/bin/ffprobe",
                deno="/usr/local/bin/deno",
            )
            expected_result = YoutubeAudioDownloadResult(
                output_dir=temp_path / "music" / ".kukicha" / "yt" / "Album [abc123]",
                files_written=2,
                media_id="abc123",
                title="Album",
                mode="video",
                chapters_reported=0,
            )

            with (
                patch(
                    "kukicha.commands.youtube_audio.resolve_youtube_audio_tools",
                    return_value=tools,
                ),
                patch(
                    "kukicha.commands.youtube_audio.extract_youtube_info",
                    return_value={
                        "id": "abc123",
                        "title": "Album",
                        "chapters": [],
                    },
                ),
                patch(
                    "kukicha.commands.youtube_audio.download_youtube_video_audio_chapters",
                    return_value=expected_result,
                ) as split_download,
                patch(
                    "kukicha.commands.youtube_audio.download_youtube_video_audio_file",
                ) as single_download,
            ):
                result = download_youtube_audio(
                    "https://www.youtube.com/watch?v=abc123",
                    options=options,
                    chapters_file=chapters_path,
                )

        self.assertEqual(result, expected_result)
        single_download.assert_not_called()
        split_download.assert_called_once()
        self.assertEqual(
            split_download.call_args.kwargs["manual_chapters"],
            [
                {"start_time": 0, "title": "Manual One", "end_time": 60},
                {"start_time": 60, "title": "Manual Two"},
            ],
        )

    def test_download_youtube_audio_video_uploads_to_remote_root(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            remote = RemoteRootConfig(
                name="archive",
                endpoint_url="https://s3.example.test",
                bucket="bucket",
                prefix="tracks/",
            )
            options = self.make_options(
                temp_path,
                remote_roots=(remote,),
                remote_workers=2,
                youtube_download_root="archive",
            )
            tools = YoutubeAudioTools(
                ffmpeg="/usr/local/bin/ffmpeg",
                ffprobe="/usr/local/bin/ffprobe",
                deno="/usr/local/bin/deno",
            )
            client = FakeS3Client()
            captured: dict[str, object] = {}
            messages: list[str] = []

            def fake_download(
                _url: str,
                *,
                stage_root: Path,
                temp_dir: Path,
                tools: YoutubeAudioTools,
                verbose: bool,
            ) -> None:
                captured["stage_root"] = stage_root
                captured["temp_dir"] = temp_dir
                (stage_root / "source").mkdir(parents=True)

            def fake_find(stage_root: Path, *, tools: YoutubeAudioTools) -> list[Path]:
                return [
                    stage_root / "source" / "source.webm",
                ]

            def fake_copy(
                _input_path: Path,
                output_path: Path,
                *,
                tools: YoutubeAudioTools,
            ) -> None:
                output_path.write_bytes(output_path.name.encode("utf-8"))

            def fake_client_factory(
                created_remote: RemoteRootConfig,
                *,
                remote_workers: int | None = None,
            ) -> FakeS3Client:
                captured["remote"] = created_remote
                captured["remote_workers"] = remote_workers
                return client

            with (
                patch(
                    "kukicha.commands.youtube_audio.resolve_youtube_audio_tools",
                    return_value=tools,
                ),
                patch(
                    "kukicha.commands.youtube_audio.extract_youtube_info",
                    return_value={
                        "id": "abc123",
                        "title": "Album: Title",
                        "chapters": [{"title": "One"}, {"title": "Two"}],
                    },
                ),
                patch(
                    "kukicha.commands.youtube_audio.download_video_audio_file",
                    side_effect=fake_download,
                ),
                patch(
                    "kukicha.commands.youtube_audio.find_stage_video_files",
                    side_effect=fake_find,
                ),
                patch("kukicha.commands.youtube_audio.audio_codec", return_value="opus"),
                patch(
                    "kukicha.commands.youtube_audio.copy_audio_without_transcoding",
                    side_effect=fake_copy,
                ),
                patch("kukicha.commands.youtube_audio.assert_audio_only"),
                patch(
                    "kukicha.commands.youtube_audio.create_s3_client_for_workers",
                    side_effect=fake_client_factory,
                ),
            ):
                result = download_youtube_audio(
                    "https://www.youtube.com/watch?v=abc123",
                    options=options,
                    status=messages.append,
                )

            temp_root = captured["stage_root"].parent
            self.assertFalse(temp_root.exists())
            self.assertEqual(captured["remote"], remote)
            self.assertEqual(captured["remote_workers"], 2)
            self.assertEqual(
                result.output_dir,
                canonical_s3_path(
                    remote,
                    "tracks/.kukicha/yt/Album_ Title [abc123]",
                ),
            )
            puts_by_key = {str(item["Key"]): item for item in client.puts}
            self.assertEqual(
                sorted(puts_by_key),
                [
                    (
                        "tracks/.kukicha/yt/Album_ Title [abc123]/"
                        "Album_ Title [abc123].opus"
                    ),
                ],
            )
            self.assertEqual(
                puts_by_key[
                    (
                        "tracks/.kukicha/yt/Album_ Title [abc123]/"
                        "Album_ Title [abc123].opus"
                    )
                ]["Body"],
                b"Album_ Title [abc123].opus",
            )
            self.assertTrue(
                any(
                    message.startswith("Uploading finalized audio to archive")
                    for message in messages
                )
            )

    def test_download_youtube_audio_video_uses_manual_chapters_without_yt_dlp_chapters(
        self,
    ) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            download_root = (temp_path / "music").resolve(strict=False)
            options = self.make_options(temp_path, roots=(download_root,))
            tools = YoutubeAudioTools(
                ffmpeg="/usr/local/bin/ffmpeg",
                ffprobe="/usr/local/bin/ffprobe",
                deno="/usr/local/bin/deno",
            )
            manual_chapters = [
                {"start_time": 0, "title": "Manual One", "end_time": 60},
                {"start_time": 60, "title": "Manual Two"},
            ]
            captured: dict[str, object] = {}
            messages: list[str] = []

            def fake_download(
                _url: str,
                *,
                stage_root: Path,
                temp_dir: Path,
                tools: YoutubeAudioTools,
                verbose: bool,
                manual_chapters: object = None,
            ) -> None:
                captured["manual_chapters"] = manual_chapters
                captured["stage_root"] = stage_root
                (stage_root / "chapters").mkdir(parents=True)

            def fake_find(stage_root: Path, *, tools: YoutubeAudioTools) -> list[Path]:
                return [
                    stage_root / "chapters" / "001 - Manual One.webm",
                    stage_root / "chapters" / "002 - Manual Two.webm",
                ]

            def fake_copy(
                _input_path: Path,
                output_path: Path,
                *,
                tools: YoutubeAudioTools,
            ) -> None:
                output_path.write_bytes(b"audio")

            with (
                patch(
                    "kukicha.commands.youtube_audio.resolve_youtube_audio_tools",
                    return_value=tools,
                ),
                patch(
                    "kukicha.commands.youtube_audio.extract_youtube_info",
                    return_value={
                        "id": "abc123",
                        "title": "Album",
                        "chapters": [],
                    },
                ),
                patch(
                    "kukicha.commands.youtube_audio.download_and_split_chapters",
                    side_effect=fake_download,
                ),
                patch(
                    "kukicha.commands.youtube_audio.find_stage_chapter_files",
                    side_effect=fake_find,
                ),
                patch("kukicha.commands.youtube_audio.audio_codec", return_value="opus"),
                patch(
                    "kukicha.commands.youtube_audio.copy_audio_without_transcoding",
                    side_effect=fake_copy,
                ),
                patch("kukicha.commands.youtube_audio.assert_audio_only"),
            ):
                result = download_youtube_audio(
                    "https://www.youtube.com/watch?v=abc123",
                    options=options,
                    manual_chapters=manual_chapters,
                    status=messages.append,
                )

        self.assertEqual(captured["manual_chapters"], manual_chapters)
        self.assertEqual(result.mode, "video")
        self.assertEqual(result.chapters_reported, 0)
        self.assertEqual(result.files_written, 2)
        self.assertIn("Chapters reported by yt-dlp: 0", messages)
        self.assertIn("Chapters supplied from file: 2", messages)

    def test_download_youtube_audio_video_manual_chapters_override_yt_dlp_chapters(
        self,
    ) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            download_root = (temp_path / "music").resolve(strict=False)
            options = self.make_options(temp_path, roots=(download_root,))
            tools = YoutubeAudioTools(
                ffmpeg="/usr/local/bin/ffmpeg",
                ffprobe="/usr/local/bin/ffprobe",
                deno="/usr/local/bin/deno",
            )
            manual_chapters = [
                {"start_time": 0, "title": "Manual One", "end_time": 30},
                {"start_time": 30, "title": "Manual Two"},
            ]
            captured: dict[str, object] = {}
            messages: list[str] = []

            def fake_download(
                _url: str,
                *,
                stage_root: Path,
                temp_dir: Path,
                tools: YoutubeAudioTools,
                verbose: bool,
                manual_chapters: object = None,
            ) -> None:
                captured["manual_chapters"] = manual_chapters
                captured["stage_root"] = stage_root
                (stage_root / "chapters").mkdir(parents=True)

            def fake_find(stage_root: Path, *, tools: YoutubeAudioTools) -> list[Path]:
                return [
                    stage_root / "chapters" / "001 - Manual One.webm",
                    stage_root / "chapters" / "002 - Manual Two.webm",
                ]

            def fake_copy(
                _input_path: Path,
                output_path: Path,
                *,
                tools: YoutubeAudioTools,
            ) -> None:
                output_path.write_bytes(b"audio")

            with (
                patch(
                    "kukicha.commands.youtube_audio.resolve_youtube_audio_tools",
                    return_value=tools,
                ),
                patch(
                    "kukicha.commands.youtube_audio.extract_youtube_info",
                    return_value={
                        "id": "abc123",
                        "title": "Album",
                        "chapters": [{"start_time": 0, "title": "Reported"}],
                    },
                ),
                patch(
                    "kukicha.commands.youtube_audio.download_and_split_chapters",
                    side_effect=fake_download,
                ),
                patch(
                    "kukicha.commands.youtube_audio.find_stage_chapter_files",
                    side_effect=fake_find,
                ),
                patch("kukicha.commands.youtube_audio.audio_codec", return_value="opus"),
                patch(
                    "kukicha.commands.youtube_audio.copy_audio_without_transcoding",
                    side_effect=fake_copy,
                ),
                patch("kukicha.commands.youtube_audio.assert_audio_only"),
            ):
                result = download_youtube_audio(
                    "https://www.youtube.com/watch?v=abc123",
                    options=options,
                    manual_chapters=manual_chapters,
                    status=messages.append,
                )

        self.assertEqual(captured["manual_chapters"], manual_chapters)
        self.assertEqual(result.mode, "video")
        self.assertEqual(result.chapters_reported, 1)
        self.assertEqual(result.files_written, 2)
        self.assertIn("Chapters reported by yt-dlp: 1", messages)
        self.assertIn("Chapters supplied from file: 2", messages)

    def test_download_youtube_audio_playlist_uses_config_path_and_ignores_item_chapters(
        self,
    ) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            download_root = (temp_path / "music").resolve(strict=False)
            download_path = download_root / ".kukicha" / "yt"
            options = self.make_options(temp_path, roots=(download_root,))
            tools = YoutubeAudioTools(
                ffmpeg="/usr/local/bin/ffmpeg",
                ffprobe="/usr/local/bin/ffprobe",
                deno="/usr/local/bin/deno",
            )
            captured: dict[str, Path] = {}
            messages: list[str] = []

            def fake_download(
                _url: str,
                *,
                stage_root: Path,
                temp_dir: Path,
                tools: YoutubeAudioTools,
                verbose: bool,
            ) -> None:
                captured["stage_root"] = stage_root
                captured["temp_dir"] = temp_dir
                captured["items"] = stage_root / "items"
                captured["items"].mkdir(parents=True)

            def fake_find(stage_root: Path, *, tools: YoutubeAudioTools) -> list[Path]:
                return [
                    stage_root / "items" / "001 - One.webm",
                    stage_root / "items" / "002 - Two.webm",
                ]

            def fake_copy(
                _input_path: Path,
                output_path: Path,
                *,
                tools: YoutubeAudioTools,
            ) -> None:
                output_path.write_bytes(b"audio")

            with (
                patch(
                    "kukicha.commands.youtube_audio.resolve_youtube_audio_tools",
                    return_value=tools,
                ),
                patch(
                    "kukicha.commands.youtube_audio.extract_youtube_info",
                    return_value={
                        "_type": "playlist",
                        "id": "pl123",
                        "title": "Playlist: Title",
                        "entries": [
                            {
                                "id": "one",
                                "title": "One",
                                "chapters": [{"start_time": 0, "title": "Intro"}],
                            },
                            {"id": "two", "title": "Two"},
                        ],
                    },
                ),
                patch(
                    "kukicha.commands.youtube_audio.download_playlist_audio_items",
                    side_effect=fake_download,
                ),
                patch(
                    "kukicha.commands.youtube_audio.find_stage_playlist_files",
                    side_effect=fake_find,
                ),
                patch("kukicha.commands.youtube_audio.audio_codec", return_value="opus"),
                patch(
                    "kukicha.commands.youtube_audio.copy_audio_without_transcoding",
                    side_effect=fake_copy,
                ),
                patch("kukicha.commands.youtube_audio.assert_audio_only"),
            ):
                result = download_youtube_audio(
                    "https://www.youtube.com/playlist?list=pl123",
                    options=options,
                    status=messages.append,
                )

            temp_root = captured["stage_root"].parent
            os_temp = Path(gettempdir()).resolve(strict=False)
            self.assertTrue(
                captured["stage_root"].resolve(strict=False).is_relative_to(os_temp)
            )
            self.assertEqual(captured["temp_dir"], temp_root / "yt-dlp")
            self.assertFalse(temp_root.exists())
            self.assertEqual(
                result.output_dir,
                download_path / "Playlist_ Title [pl123]",
            )
            self.assertEqual(result.mode, "playlist")
            self.assertEqual(result.items_reported, 2)
            self.assertEqual(result.chapters_reported, 0)
            self.assertTrue((result.output_dir / "001 - One.opus").exists())
            self.assertTrue((result.output_dir / "002 - Two.opus").exists())
            self.assertIn("Playlist items reported by yt-dlp: 2", messages)
            self.assertIn(f"Final output directory: {result.output_dir}", messages)

    def test_download_youtube_audio_playlist_rejects_chapters_file(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            options = self.make_options(temp_path)
            tools = YoutubeAudioTools(
                ffmpeg="/usr/local/bin/ffmpeg",
                ffprobe="/usr/local/bin/ffprobe",
                deno="/usr/local/bin/deno",
            )

            with (
                patch(
                    "kukicha.commands.youtube_audio.resolve_youtube_audio_tools",
                    return_value=tools,
                ),
                patch(
                    "kukicha.commands.youtube_audio.extract_youtube_info",
                    return_value={
                        "_type": "playlist",
                        "id": "pl123",
                        "title": "Playlist",
                        "entries": [{"id": "one", "title": "One"}],
                    },
                ),
                patch("kukicha.commands.youtube_audio.download_playlist_audio_items") as download,
            ):
                with self.assertRaisesRegex(RuntimeError, "--chapters-file"):
                    download_youtube_audio(
                        "https://www.youtube.com/playlist?list=pl123",
                        options=options,
                        chapters_file=temp_path / "missing-chapters.txt",
                    )

        download.assert_not_called()

    def test_download_youtube_audio_playlist_rejects_split_into_chapters(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            options = self.make_options(temp_path)
            tools = YoutubeAudioTools(
                ffmpeg="/usr/local/bin/ffmpeg",
                ffprobe="/usr/local/bin/ffprobe",
                deno="/usr/local/bin/deno",
            )

            with (
                patch(
                    "kukicha.commands.youtube_audio.resolve_youtube_audio_tools",
                    return_value=tools,
                ),
                patch(
                    "kukicha.commands.youtube_audio.extract_youtube_info",
                    return_value={
                        "_type": "playlist",
                        "id": "pl123",
                        "title": "Playlist",
                        "entries": [{"id": "one", "title": "One"}],
                    },
                ),
                patch("kukicha.commands.youtube_audio.download_playlist_audio_items") as download,
            ):
                with self.assertRaisesRegex(RuntimeError, "--split-into-chapters"):
                    download_youtube_audio(
                        "https://www.youtube.com/playlist?list=pl123",
                        options=options,
                        split_into_chapters=True,
                    )

        download.assert_not_called()

    def test_download_video_audio_file_sets_yt_dlp_paths_and_templates(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            stage_root = temp_path / "stage"
            temp_dir = temp_path / "yt-dlp"
            tools = YoutubeAudioTools(
                ffmpeg="/usr/local/bin/ffmpeg",
                ffprobe="/usr/local/bin/ffprobe",
                deno="/usr/local/bin/deno",
            )
            captured: dict[str, object] = {}

            class FakeYoutubeDL:
                def __init__(self, opts: dict[str, object]) -> None:
                    captured["opts"] = opts

                def __enter__(self) -> "FakeYoutubeDL":
                    return self

                def __exit__(self, *_args: object) -> None:
                    return None

                def download(self, urls: list[str]) -> int:
                    captured["urls"] = urls
                    return 0

            with patch("kukicha.commands.youtube_audio.yt_dlp.YoutubeDL", FakeYoutubeDL):
                download_video_audio_file(
                    "https://www.youtube.com/watch?v=abc123",
                    stage_root=stage_root,
                    temp_dir=temp_dir,
                    tools=tools,
                    verbose=True,
                )

        opts = captured["opts"]
        self.assertIsInstance(opts, dict)
        self.assertEqual(
            opts["paths"],
            {
                "home": str(stage_root),
                "temp": str(temp_dir),
            },
        )
        self.assertTrue(opts["noplaylist"])
        self.assertEqual(
            opts["outtmpl"],
            {
                "default": "source/source.%(ext)s",
            },
        )
        self.assertNotIn("postprocessors", opts)
        self.assertEqual(captured["urls"], ["https://www.youtube.com/watch?v=abc123"])

    def test_download_and_split_chapters_sets_yt_dlp_paths_and_templates(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            stage_root = temp_path / "stage"
            temp_dir = temp_path / "yt-dlp"
            tools = YoutubeAudioTools(
                ffmpeg="/usr/local/bin/ffmpeg",
                ffprobe="/usr/local/bin/ffprobe",
                deno="/usr/local/bin/deno",
            )
            captured: dict[str, object] = {}

            class FakeYoutubeDL:
                def __init__(self, opts: dict[str, object]) -> None:
                    captured["opts"] = opts

                def __enter__(self) -> "FakeYoutubeDL":
                    return self

                def __exit__(self, *_args: object) -> None:
                    return None

                def download(self, urls: list[str]) -> int:
                    captured["urls"] = urls
                    return 0

            with patch("kukicha.commands.youtube_audio.yt_dlp.YoutubeDL", FakeYoutubeDL):
                download_and_split_chapters(
                    "https://www.youtube.com/watch?v=abc123",
                    stage_root=stage_root,
                    temp_dir=temp_dir,
                    tools=tools,
                    verbose=True,
                )

        opts = captured["opts"]
        self.assertIsInstance(opts, dict)
        self.assertEqual(
            opts["paths"],
            {
                "home": str(stage_root),
                "temp": str(temp_dir),
            },
        )
        self.assertTrue(opts["noplaylist"])
        self.assertEqual(
            opts["outtmpl"],
            {
                "default": "source/source.%(ext)s",
                "chapter": "chapters/%(section_number)03d - %(section_title)s.%(ext)s",
            },
        )
        self.assertEqual(captured["urls"], ["https://www.youtube.com/watch?v=abc123"])

    def test_download_playlist_audio_items_sets_yt_dlp_paths_and_templates(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            stage_root = temp_path / "stage"
            temp_dir = temp_path / "yt-dlp"
            tools = YoutubeAudioTools(
                ffmpeg="/usr/local/bin/ffmpeg",
                ffprobe="/usr/local/bin/ffprobe",
                deno="/usr/local/bin/deno",
            )
            captured: dict[str, object] = {}

            class FakeYoutubeDL:
                def __init__(self, opts: dict[str, object]) -> None:
                    captured["opts"] = opts

                def __enter__(self) -> "FakeYoutubeDL":
                    return self

                def __exit__(self, *_args: object) -> None:
                    return None

                def download(self, urls: list[str]) -> int:
                    captured["urls"] = urls
                    return 0

            with patch("kukicha.commands.youtube_audio.yt_dlp.YoutubeDL", FakeYoutubeDL):
                download_playlist_audio_items(
                    "https://www.youtube.com/playlist?list=pl123",
                    stage_root=stage_root,
                    temp_dir=temp_dir,
                    tools=tools,
                    verbose=True,
                )

        opts = captured["opts"]
        self.assertIsInstance(opts, dict)
        self.assertEqual(
            opts["paths"],
            {
                "home": str(stage_root),
                "temp": str(temp_dir),
            },
        )
        self.assertFalse(opts["noplaylist"])
        self.assertEqual(
            opts["outtmpl"],
            {
                "default": "items/%(playlist_index)03d - %(title)s.%(ext)s",
            },
        )
        self.assertNotIn("postprocessors", opts)
        self.assertEqual(
            captured["urls"],
            ["https://www.youtube.com/playlist?list=pl123"],
        )

    def test_download_and_split_chapters_registers_manual_chapter_injector(
        self,
    ) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            stage_root = temp_path / "stage"
            temp_dir = temp_path / "yt-dlp"
            tools = YoutubeAudioTools(
                ffmpeg="/usr/local/bin/ffmpeg",
                ffprobe="/usr/local/bin/ffprobe",
                deno="/usr/local/bin/deno",
            )
            manual_chapters = [
                {"start_time": 0, "title": "Manual One", "end_time": 30},
                {"start_time": 30, "title": "Manual Two"},
            ]
            captured: dict[str, object] = {"postprocessors": []}

            class FakeYoutubeDL:
                def __init__(self, opts: dict[str, object]) -> None:
                    captured["opts"] = opts

                def __enter__(self) -> "FakeYoutubeDL":
                    return self

                def __exit__(self, *_args: object) -> None:
                    return None

                def add_post_processor(
                    self,
                    postprocessor: object,
                    when: str = "post_process",
                ) -> None:
                    captured["postprocessors"].append((when, postprocessor))

                def download(self, urls: list[str]) -> int:
                    captured["urls"] = urls
                    return 0

            with patch("kukicha.commands.youtube_audio.yt_dlp.YoutubeDL", FakeYoutubeDL):
                download_and_split_chapters(
                    "https://www.youtube.com/watch?v=abc123",
                    stage_root=stage_root,
                    temp_dir=temp_dir,
                    tools=tools,
                    verbose=False,
                    manual_chapters=manual_chapters,
                )

        opts = captured["opts"]
        self.assertIsInstance(opts, dict)
        self.assertEqual(opts["postprocessors"][0]["key"], "FFmpegSplitChapters")
        self.assertEqual(captured["urls"], ["https://www.youtube.com/watch?v=abc123"])
        self.assertEqual(len(captured["postprocessors"]), 1)

        when, postprocessor = captured["postprocessors"][0]
        self.assertEqual(when, "pre_process")
        _files_to_delete, info = postprocessor.run(
            {"chapters": [{"start_time": 0, "title": "Reported"}]}
        )
        self.assertEqual(info["chapters"], manual_chapters)

    def make_options(
        self,
        temp_path: Path,
        *,
        roots: tuple[Path, ...] | None = None,
        remote_roots: tuple[RemoteRootConfig, ...] = (),
        remote_workers: int | None = None,
        youtube_download_root: str | None = None,
    ) -> PlayerServerOptions:
        local_root = temp_path / "music"
        resolved_roots = tuple(
            root.expanduser().resolve(strict=False) for root in (roots or (local_root,))
        )
        return PlayerServerOptions(
            config_path=temp_path / "kukicha.toml",
            database=temp_path / "kukicha.sqlite",
            ffmpeg_path=None,
            roots=resolved_roots,
            remote_roots=remote_roots,
            remote_workers=remote_workers,
            youtube_download_root=youtube_download_root or str(local_root),
        )


if __name__ == "__main__":
    unittest.main()
