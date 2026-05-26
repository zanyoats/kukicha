from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import io
import logging
from pathlib import Path
import tempfile
from time import perf_counter
import sqlite3

from ...audio_types import KNOWN_IMAGE_MIME_TYPES, content_type_for_name
from ...library_sources import (
    SOURCE_KIND_LOCAL,
    SOURCE_KIND_S3,
    create_s3_client,
    is_remote_path,
    remote_root_from_source_json,
)
from ...player_runtime import PlayerJobCancelToken, PlayerJobResult, PlayerRuntime
from ..database import connect_database
from ..queries import AlbumNotFoundError
from .album_edits import album_artist_display_text, album_display_label, row_text

LOGGER = logging.getLogger("kukicha.player")


@dataclass(frozen=True, slots=True)
class AlbumCoverUploadTarget:
    source_kind: str
    source_json: str
    local_directory: str | None = None
    object_prefix: str | None = None


@dataclass(frozen=True, slots=True)
class AlbumCoverUploadJob:
    album_id: str
    album_label: str
    album_name: str
    cover_filename: str
    content_type: str
    data: bytes
    targets: tuple[AlbumCoverUploadTarget, ...]


@dataclass(frozen=True, slots=True)
class AlbumCoverUploadResult:
    album_label: str
    album_name: str
    cover_filename: str
    targets_updated: int
    local_files_updated: int
    remote_objects_updated: int


def prepare_album_cover_upload_job(
    database: Path,
    album_id: str,
    *,
    filename: str,
    data: bytes,
) -> AlbumCoverUploadJob:
    cleaned_album_id = str(album_id or "").strip()
    if not cleaned_album_id:
        raise ValueError("album id is required")

    cover_filename = cover_upload_filename(filename)
    if not data:
        raise ValueError("cover file is empty")

    connection = connect_database(database, create=False)
    try:
        album_row = connection.execute(
            """
            SELECT album
            FROM library_albums
            WHERE album_id = ?
            """,
            (cleaned_album_id,),
        ).fetchone()
        if album_row is None:
            raise AlbumNotFoundError(cleaned_album_id)
        artist_label = album_artist_display_text(connection, cleaned_album_id)

        track_rows = list(
            connection.execute(
                """
                SELECT
                    tracks.track_id,
                    tracks.path,
                    COALESCE(sources.source_kind, roots.kind, 'local') AS source_kind,
                    COALESCE(roots.source_json, '{}') AS source_json,
                    sources.object_key
                FROM library_tracks AS tracks
                LEFT JOIN library_track_sources AS sources
                    ON sources.track_id = tracks.track_id
                LEFT JOIN library_roots AS roots
                    ON roots.position = tracks.root_position
                WHERE tracks.album_id = ?
                ORDER BY tracks.track_id
                """,
                (cleaned_album_id,),
            )
        )
    finally:
        connection.close()

    if not track_rows:
        raise ValueError("album has no tracks for cover upload")

    targets = album_cover_upload_targets(track_rows)
    if not targets:
        raise ValueError("album has no track folders for cover upload")

    album_name = str(album_row["album"]) if album_row["album"] else "<unknown album>"
    return AlbumCoverUploadJob(
        album_id=cleaned_album_id,
        album_label=album_display_label(artist_label, album_name),
        album_name=album_name,
        cover_filename=cover_filename,
        content_type=content_type_for_name(cover_filename),
        data=bytes(data),
        targets=targets,
    )


def cover_upload_filename(filename: str) -> str:
    suffix = Path(str(filename or "")).suffix.casefold()
    if not suffix:
        raise ValueError("cover file must have an image extension")
    if suffix not in KNOWN_IMAGE_MIME_TYPES:
        allowed = ", ".join(sorted(KNOWN_IMAGE_MIME_TYPES))
        raise ValueError(f"cover file extension must be one of: {allowed}")
    return f"cover{suffix}"


def album_cover_upload_targets(
    rows: list[sqlite3.Row],
) -> tuple[AlbumCoverUploadTarget, ...]:
    targets: dict[AlbumCoverUploadTarget, None] = {}
    for row in rows:
        source_kind = row_text(row, "source_kind", default=SOURCE_KIND_LOCAL)
        path = row_text(row, "path")
        if source_kind == SOURCE_KIND_S3 or is_remote_path(path):
            object_key = row_text(row, "object_key")
            if not object_key:
                raise ValueError("remote cover upload requires S3 object key metadata")
            targets[
                AlbumCoverUploadTarget(
                    source_kind=SOURCE_KIND_S3,
                    source_json=row_text(row, "source_json", default="{}"),
                    object_prefix=remote_parent_prefix(object_key),
                )
            ] = None
            continue

        if not path:
            continue
        targets[
            AlbumCoverUploadTarget(
                source_kind=SOURCE_KIND_LOCAL,
                source_json="{}",
                local_directory=str(Path(path).parent),
            )
        ] = None
    return tuple(targets)


def remote_parent_prefix(key: str) -> str:
    directory, separator, _name = key.rstrip("/").rpartition("/")
    return directory + "/" if separator else ""


def upload_album_cover_files(
    job: AlbumCoverUploadJob,
    *,
    cancel_check: Callable[[], None] | None = None,
) -> AlbumCoverUploadResult:
    local_files_updated = 0
    remote_objects_updated = 0
    for target in job.targets:
        if cancel_check is not None:
            cancel_check()
        if target.source_kind == SOURCE_KIND_S3:
            upload_remote_album_cover(target, job)
            remote_objects_updated += 1
            continue
        upload_local_album_cover(target, job)
        local_files_updated += 1

    return AlbumCoverUploadResult(
        album_label=job.album_label,
        album_name=job.album_name,
        cover_filename=job.cover_filename,
        targets_updated=local_files_updated + remote_objects_updated,
        local_files_updated=local_files_updated,
        remote_objects_updated=remote_objects_updated,
    )


def upload_local_album_cover(
    target: AlbumCoverUploadTarget,
    job: AlbumCoverUploadJob,
) -> None:
    if not target.local_directory:
        raise ValueError("local cover upload requires a track folder")
    directory = Path(target.local_directory)
    cover_path = directory / job.cover_filename
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "wb",
            dir=directory,
            prefix=f".{job.cover_filename}.",
            delete=False,
        ) as handle:
            handle.write(job.data)
            temp_path = Path(handle.name)
        temp_path.replace(cover_path)
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass


def upload_remote_album_cover(
    target: AlbumCoverUploadTarget,
    job: AlbumCoverUploadJob,
) -> None:
    if target.object_prefix is None:
        raise ValueError("remote cover upload requires a track object prefix")
    remote = remote_root_from_source_json(target.source_json)
    object_key = f"{target.object_prefix}{job.cover_filename}"
    client = create_s3_client(remote)
    client.put_object(
        Bucket=remote.bucket,
        Key=object_key,
        Body=io.BytesIO(job.data),
        ContentType=job.content_type,
        Metadata={},
    )


def run_upload_album_cover_job(
    runtime: PlayerRuntime,
    job: AlbumCoverUploadJob,
    cancel_token: PlayerJobCancelToken,
) -> PlayerJobResult:
    started_at = perf_counter()
    result = upload_album_cover_files(
        job,
        cancel_check=cancel_token.raise_if_canceled,
    )
    duration_seconds = perf_counter() - started_at
    LOGGER.info(
        "album cover upload completed for %s (targets_updated=%s, duration=%.2fs)",
        result.album_label,
        result.targets_updated,
        duration_seconds,
    )
    return PlayerJobResult(
        message=(
            f"Cover uploaded for {job.album_label}. "
            "Rescan the library to reconcile the new cover art."
        ),
        context={
            "album": job.album_name,
            "cover_filename": job.cover_filename,
            "cover_targets": len(job.targets),
            "local_files_updated": result.local_files_updated,
            "remote_objects_updated": result.remote_objects_updated,
            "duration_seconds": duration_seconds,
            "rescan_recommended": True,
        },
    )
