from __future__ import annotations

import argparse
import os
import shutil
import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .._compat import UTC
from ..audio_types import audio_mime_type_for_name
from ..file_metadata import file_created_at
from ..library_sources import (
    RemoteRootConfig,
    create_s3_client,
    create_s3_client_for_workers,
    remote_root_display_label,
    resolve_remote_worker_count,
)
from ..player_config import PlayerServerOptions, load_player_options
from ..player_errors import PlayerConfigError
from ..scanner import iter_music_files, write_album_audio_tags


REMOTE_UPLOAD_IGNORED_DIRECTORY_NAMES = frozenset(
    {
        ".AppleDouble",
        ".Spotlight-V100",
        ".TemporaryItems",
        ".Trash",
        ".Trashes",
        ".fseventsd",
        "__MACOSX",
        "lost+found",
    }
)
REMOTE_UPLOAD_IGNORED_DIRECTORY_PREFIXES = (".Trash-",)
REMOTE_UPLOAD_IGNORED_FILE_NAMES = frozenset(
    {
        ".DS_Store",
        ".directory",
        ".localized",
        "Icon\r",
    }
)
REMOTE_UPLOAD_IGNORED_FILE_PREFIXES = ("._", ".fuse_hidden", ".nfs")


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


@dataclass(frozen=True)
class CopyToRemoteUploadError:
    path: Path
    object_key: str
    message: str


@dataclass(frozen=True)
class CopyToRemoteDeleteError:
    path: Path
    message: str


@dataclass(frozen=True)
class CopyToRemoteUnit:
    path: Path
    files: tuple[Path, ...]


@dataclass(frozen=True)
class CopyToRemoteUploadTask:
    unit_index: int
    file_number: int
    path: Path
    object_key: str
    relative_path: str


@dataclass(frozen=True)
class CopyToRemoteResult:
    source: Path
    remote: RemoteRootConfig
    source_children: bool
    delete_source: bool
    files_found: int
    files_uploaded: int
    upload_errors: tuple[CopyToRemoteUploadError, ...]
    deleted_sources: tuple[Path, ...]
    delete_errors: tuple[CopyToRemoteDeleteError, ...]

    @property
    def files_failed(self) -> int:
        return len(self.upload_errors)

    @property
    def source_deletes_failed(self) -> int:
        return len(self.delete_errors)


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


def run_copy_to_remote(args: argparse.Namespace) -> int:
    def status(message: str) -> None:
        print(f"[copy-to-remote] {message}", file=sys.stderr, flush=True)

    try:
        options = load_player_options(args.config, require_auth=False)
        result = copy_to_remote(
            args.source,
            remote_name=args.remote,
            options=options,
            source_children=args.source_children,
            delete_source=args.delete_source,
            remote_workers=args.remote_workers,
            status=status,
        )
    except (PlayerConfigError, FileNotFoundError, NotADirectoryError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    print(format_copy_to_remote_summary(result))
    for error in result.upload_errors:
        print(
            f"[error] {error.path} -> {error.object_key}: {error.message}",
            file=sys.stderr,
        )
    for error in result.delete_errors:
        print(f"[delete-error] {error.path}: {error.message}", file=sys.stderr)
    return 1 if result.upload_errors or result.delete_errors else 0


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


def copy_to_remote(
    source: Path,
    *,
    remote_name: str,
    options: PlayerServerOptions,
    source_children: bool = False,
    delete_source: bool = False,
    remote_workers: int | None = None,
    s3_client_factory: Callable[..., object] = create_s3_client,
    status: Callable[[str], None] | None = None,
) -> CopyToRemoteResult:
    resolved_source = source.expanduser().resolve()
    if not resolved_source.exists():
        raise FileNotFoundError(f"source folder does not exist: {source}")
    if not resolved_source.is_dir():
        raise NotADirectoryError(f"source is not a folder: {source}")

    remote = select_remote_root(options.remote_roots, remote_name)
    worker_count = resolve_remote_worker_count(
        remote_workers if remote_workers is not None else options.remote_workers
    )
    worker_source = remote_worker_source(remote_workers, options.remote_workers)
    client = create_s3_client_for_workers(
        remote,
        s3_client_factory,
        remote_workers=worker_count,
    )
    units = copy_to_remote_units(resolved_source, source_children=source_children)
    base = resolved_source if source_children else resolved_source.parent
    files_found = sum(len(unit.files) for unit in units)

    emit_copy_to_remote_status(
        status,
        (
            f"found {files_found} file(s) in {len(units)} source item(s); "
            f"uploading to {remote_root_display_label(remote)}"
        ),
    )
    emit_copy_to_remote_status(
        status,
        f"remote workers: {worker_count} ({worker_source})",
    )

    upload_error_entries: list[tuple[int, CopyToRemoteUploadError]] = []
    files_uploaded = 0
    file_number = 0
    unit_failed = [False for _unit in units]
    upload_tasks: list[CopyToRemoteUploadTask] = []
    for unit_index, unit in enumerate(units):
        for path in unit.files:
            file_number += 1
            object_key = remote_object_key(remote, path.relative_to(base))
            relative_path = path.relative_to(base).as_posix()
            upload_tasks.append(
                CopyToRemoteUploadTask(
                    unit_index=unit_index,
                    file_number=file_number,
                    path=path,
                    object_key=object_key,
                    relative_path=relative_path,
                )
            )
            emit_copy_to_remote_status(
                status,
                f"uploading {file_number}/{files_found}: {relative_path}",
            )

    if upload_tasks:
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(
                    upload_file_to_remote,
                    client,
                    remote,
                    task.path,
                    task.object_key,
                ): task
                for task in upload_tasks
            }
            for future in as_completed(futures):
                task = futures[future]
                try:
                    future.result()
                except Exception as error:
                    unit_failed[task.unit_index] = True
                    emit_copy_to_remote_status(
                        status,
                        (
                            f"failed {task.file_number}/{files_found}: "
                            f"{task.relative_path}: {error}"
                        ),
                    )
                    upload_error_entries.append(
                        (
                            task.file_number,
                            CopyToRemoteUploadError(
                                path=task.path,
                                object_key=task.object_key,
                                message=str(error),
                            ),
                        )
                    )
                    continue
                files_uploaded += 1
                emit_copy_to_remote_status(
                    status,
                    f"uploaded {task.file_number}/{files_found}: {task.object_key}",
                )

    upload_errors = [
        error
        for _file_number, error in sorted(
            upload_error_entries,
            key=lambda entry: entry[0],
        )
    ]
    successful_units = [
        unit.path for unit_index, unit in enumerate(units) if not unit_failed[unit_index]
    ]

    deleted_sources: list[Path] = []
    delete_errors: list[CopyToRemoteDeleteError] = []
    if delete_source:
        emit_copy_to_remote_status(
            status,
            f"deleting {len(successful_units)} successful source item(s)",
        )
        for path in successful_units:
            emit_copy_to_remote_status(status, f"deleting source: {path}")
            try:
                delete_upload_unit(path)
            except OSError as error:
                emit_copy_to_remote_status(
                    status,
                    f"delete failed: {path}: {error}",
                )
                delete_errors.append(
                    CopyToRemoteDeleteError(path=path, message=str(error))
                )
                continue
            deleted_sources.append(path)
            emit_copy_to_remote_status(status, f"deleted source: {path}")

    return CopyToRemoteResult(
        source=resolved_source,
        remote=remote,
        source_children=source_children,
        delete_source=delete_source,
        files_found=files_found,
        files_uploaded=files_uploaded,
        upload_errors=tuple(upload_errors),
        deleted_sources=tuple(deleted_sources),
        delete_errors=tuple(delete_errors),
    )


def select_remote_root(
    remote_roots: tuple[RemoteRootConfig, ...],
    remote_name: str,
) -> RemoteRootConfig:
    matches = [remote for remote in remote_roots if remote.name == remote_name]
    if not matches:
        raise ValueError(f"remote root not found: {remote_name}")
    if len(matches) > 1:
        raise ValueError(f"remote root name is ambiguous: {remote_name}")
    return matches[0]


def copy_to_remote_units(
    source: Path,
    *,
    source_children: bool,
) -> tuple[CopyToRemoteUnit, ...]:
    if not source_children:
        return (CopyToRemoteUnit(path=source, files=regular_files_under(source)),)

    units: list[CopyToRemoteUnit] = []
    for child in sorted(source.iterdir(), key=lambda path: str(path)):
        if remote_upload_ignored_filesystem_path(child):
            continue
        if not child.is_dir() and not child.is_file():
            continue
        units.append(CopyToRemoteUnit(path=child, files=regular_files_under(child)))
    return tuple(units)


def regular_files_under(path: Path) -> tuple[Path, ...]:
    if path.is_file():
        return () if remote_upload_ignored_filesystem_path(path) else (path,)
    if remote_upload_ignored_filesystem_path(path):
        return ()
    files: list[Path] = []
    for directory, dirnames, filenames in os.walk(path):
        dirnames[:] = [
            dirname
            for dirname in dirnames
            if not remote_upload_ignored_filesystem_directory_name(dirname)
        ]
        directory_path = Path(directory)
        for filename in filenames:
            child = directory_path / filename
            if child.is_file() and not remote_upload_ignored_filesystem_path(child):
                files.append(child)
    return tuple(sorted(files, key=str))


def remote_upload_ignored_filesystem_path(path: Path) -> bool:
    if remote_upload_ignored_filesystem_file_name(path.name):
        return True

    directory_components = path.parts if path.is_dir() else path.parts[:-1]
    for component in directory_components:
        if remote_upload_ignored_filesystem_directory_name(component):
            return True
    return False


def remote_upload_ignored_filesystem_directory_name(name: str) -> bool:
    return name in REMOTE_UPLOAD_IGNORED_DIRECTORY_NAMES or name.startswith(
        REMOTE_UPLOAD_IGNORED_DIRECTORY_PREFIXES
    )


def remote_upload_ignored_filesystem_file_name(name: str) -> bool:
    return name in REMOTE_UPLOAD_IGNORED_FILE_NAMES or name.startswith(
        REMOTE_UPLOAD_IGNORED_FILE_PREFIXES
    )


def emit_copy_to_remote_status(
    status: Callable[[str], None] | None,
    message: str,
) -> None:
    if status is not None:
        status(message)


def remote_worker_source(
    override_workers: int | None,
    configured_workers: int | None,
) -> str:
    if override_workers is not None:
        return "override"
    if configured_workers is not None:
        return "configured"
    return "auto"


def remote_object_key(remote: RemoteRootConfig, relative_path: Path) -> str:
    return f"{remote.prefix}{relative_path.as_posix()}"


def upload_file_to_remote(
    client: object,
    remote: RemoteRootConfig,
    path: Path,
    object_key: str,
) -> None:
    with path.open("rb") as body:
        client.put_object(
            Bucket=remote.bucket,
            Key=object_key,
            Body=body,
            ContentType=content_type_for_path(path),
            Metadata=local_timestamp_metadata(path),
        )


def content_type_for_path(path: Path) -> str:
    return audio_mime_type_for_name(path.name)


def local_timestamp_metadata(path: Path) -> dict[str, str]:
    stat = path.stat()
    metadata = {
        "local-ctime": datetime.fromtimestamp(float(stat.st_ctime), UTC).isoformat(),
    }
    created_at = file_created_at(path)
    if created_at:
        metadata["local-created-at"] = created_at
    return metadata


def delete_upload_unit(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def format_bulk_tag_edit_summary(result: BulkTagEditResult) -> str:
    return "\n".join(
        [
            f"folder: {result.folder}",
            f"music files found: {result.files_found}",
            f"files updated: {result.files_updated}",
            f"files failed: {result.files_failed}",
        ]
    )


def format_copy_to_remote_summary(result: CopyToRemoteResult) -> str:
    lines = [
        f"source: {result.source}",
        f"remote: {remote_root_display_label(result.remote)}",
        f"bucket: {result.remote.bucket}",
        f"prefix: {result.remote.prefix}",
        f"source children: {'yes' if result.source_children else 'no'}",
        f"files found: {result.files_found}",
        f"files uploaded: {result.files_uploaded}",
        f"files failed: {result.files_failed}",
    ]
    if result.delete_source:
        lines.extend(
            [
                f"sources deleted: {len(result.deleted_sources)}",
                f"source deletes failed: {result.source_deletes_failed}",
            ]
        )
    return "\n".join(lines)


def non_empty_string(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise argparse.ArgumentTypeError("value must not be empty")
    return cleaned
