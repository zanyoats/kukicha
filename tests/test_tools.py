from __future__ import annotations

import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import call, patch

from kukicha.cli import build_parser
from kukicha.commands.tools import (
    bulk_tag_edit,
    format_bulk_tag_edit_summary,
    run_bulk_tag_edit,
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


if __name__ == "__main__":
    unittest.main()
