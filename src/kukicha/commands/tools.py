from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from ..scanner import iter_music_files, write_album_audio_tags


@dataclass(frozen=True)
class BulkTagEditError:
    path: Path
    message: str


@dataclass(frozen=True)
class BulkTagEditResult:
    folder: Path
    files_found: int
    files_updated: int
    errors: tuple[BulkTagEditError, ...]

    @property
    def files_failed(self) -> int:
        return len(self.errors)


def run_bulk_tag_edit(args: argparse.Namespace) -> int:
    try:
        result = bulk_tag_edit(
            args.folder,
            album_artist=args.album_artist,
            album=args.album,
            genre=args.genre,
        )
    except (FileNotFoundError, NotADirectoryError) as error:
        raise SystemExit(str(error)) from error

    print(format_bulk_tag_edit_summary(result))
    for error in result.errors:
        print(f"[error] {error.path}: {error.message}", file=sys.stderr)
    return 1 if result.errors else 0


def bulk_tag_edit(
    folder: Path,
    *,
    album_artist: str,
    album: str,
    genre: str,
) -> BulkTagEditResult:
    resolved_folder = folder.expanduser().resolve()
    if not resolved_folder.exists():
        raise FileNotFoundError(f"folder does not exist: {folder}")
    if not resolved_folder.is_dir():
        raise NotADirectoryError(f"not a folder: {folder}")

    paths = sorted(iter_music_files([resolved_folder]), key=lambda path: str(path))
    errors: list[BulkTagEditError] = []
    files_updated = 0
    for path in paths:
        try:
            write_album_audio_tags(
                path,
                album_artist=album_artist,
                album=album,
                genre=genre,
            )
        except OSError as error:
            errors.append(BulkTagEditError(path=path, message=str(error)))
            continue
        files_updated += 1

    return BulkTagEditResult(
        folder=resolved_folder,
        files_found=len(paths),
        files_updated=files_updated,
        errors=tuple(errors),
    )


def format_bulk_tag_edit_summary(result: BulkTagEditResult) -> str:
    return "\n".join(
        [
            f"folder: {result.folder}",
            f"music files found: {result.files_found}",
            f"files updated: {result.files_updated}",
            f"files failed: {result.files_failed}",
        ]
    )


def non_empty_string(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise argparse.ArgumentTypeError("value must not be empty")
    return cleaned
