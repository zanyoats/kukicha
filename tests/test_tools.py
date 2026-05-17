from __future__ import annotations

import io
from pathlib import Path
from tempfile import TemporaryDirectory, gettempdir
import unittest
from unittest.mock import call, patch

from kukicha.cli import build_parser
from kukicha.commands.tools import (
    bulk_tag_edit,
    format_bulk_tag_edit_summary,
    run_bulk_tag_edit,
)
from kukicha.commands.youtube_audio import (
    YoutubeAudioTools,
    download_and_split_chapters,
    download_playlist_audio_items,
    download_youtube_audio,
    parse_chapters_file,
    resolve_youtube_audio_tools,
    run_youtube_download_audio,
)
from kukicha.player_config import PlayerServerOptions


TEST_ARGON2ID_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$"
    "c29tZXNhbHR2YWx1ZQ$"
    "c29tZXBhc3N3b3JkaGFzaHZhbHVl"
)


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


class YoutubeAudioDownloadCommandTest(unittest.TestCase):
    def test_cli_accepts_youtube_audio_download_subcommand(self) -> None:
        parser = build_parser()

        args = parser.parse_args(
            [
                "tools",
                "yt-download-audio",
                "--chapters-file",
                "/tmp/chapters.txt",
                "https://www.youtube.com/watch?v=abc123",
                "--verbose",
            ]
        )

        self.assertEqual(args.url, "https://www.youtube.com/watch?v=abc123")
        self.assertEqual(args.chapters_file, Path("/tmp/chapters.txt"))
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

    def test_run_youtube_audio_download_requires_configured_path(self) -> None:
        with TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "kukicha.toml"
            password_hash_file = Path(tempdir) / "password.hash"
            password_hash_file.write_text(f"{TEST_ARGON2ID_HASH}\n", encoding="utf-8")
            password_hash_file.chmod(0o600)
            config_path.write_text(
                "\n".join(
                    (
                        "log_level = 'INFO'",
                        "[auth]",
                        "username = 'listener'",
                        "password_hash_file = 'password.hash'",
                    )
                ),
                encoding="utf-8",
            )
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
        self.assertIn("youtube_download_path must be set", stderr.getvalue())

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
            download_path = temp_path / "youtube"
            options = self.make_options(temp_path, youtube_download_path=download_path)
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
            self.assertTrue((result.output_dir / "001 - One.opus").exists())
            self.assertTrue((result.output_dir / "002 - Two.opus").exists())
            self.assertIn(f"Final output directory: {result.output_dir}", messages)

    def test_download_youtube_audio_video_uses_manual_chapters_without_yt_dlp_chapters(
        self,
    ) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            download_path = temp_path / "youtube"
            options = self.make_options(temp_path, youtube_download_path=download_path)
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
            download_path = temp_path / "youtube"
            options = self.make_options(temp_path, youtube_download_path=download_path)
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
            download_path = temp_path / "youtube"
            options = self.make_options(temp_path, youtube_download_path=download_path)
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
        youtube_download_path: Path | None = None,
    ) -> PlayerServerOptions:
        return PlayerServerOptions(
            config_path=temp_path / "kukicha.toml",
            database=temp_path / "kukicha.sqlite",
            ffmpeg_path=None,
            youtube_download_path=youtube_download_path or (temp_path / "youtube"),
        )


if __name__ == "__main__":
    unittest.main()
