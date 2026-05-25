from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
import errno
import logging
from pathlib import Path
from time import perf_counter
import sqlite3

from ...library_sources import (
    SOURCE_KIND_LOCAL,
    SOURCE_KIND_S3,
    create_s3_client,
    is_remote_path,
    remote_root_from_source_json,
)
from ...player_common import placeholders_for
from ...player_runtime import PlayerJobCancelToken, PlayerJobResult, PlayerRuntime
from ..database import connect_database
from ..queries import AlbumNotFoundError
from .album_edits import album_artist_display_text, album_display_label, row_text

LOGGER = logging.getLogger("kukicha.player")


@dataclass(frozen=True, slots=True)
class AlbumDeleteSnapshot:
    track_id: int
    album_id: str
    path: str
    source_kind: str
    source_json: str
    root_path: str
    object_key: str | None
    sidecar_artwork_path: str | None
    sidecar_object_key: str | None


@dataclass(frozen=True, slots=True)
class RemoteSidecarRef:
    source_json: str
    object_key: str


@dataclass(frozen=True, slots=True)
class AlbumDeleteJob:
    album_id: str
    album_label: str
    album_name: str
    tracks: tuple[AlbumDeleteSnapshot, ...]
    local_sidecar_paths: tuple[str, ...]
    remote_sidecar_refs: tuple[RemoteSidecarRef, ...]


@dataclass(frozen=True, slots=True)
class AlbumDeleteResult:
    album_label: str
    album_name: str
    tracks_deleted: int
    local_files_deleted: int
    remote_objects_deleted: int
    local_folders_pruned: int
    remote_prefixes_pruned: int


def prepare_album_delete_job(database: Path, album_id: str) -> AlbumDeleteJob:
    cleaned_album_id = str(album_id or "").strip()
    if not cleaned_album_id:
        raise ValueError("album id is required")

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
                    tracks.album_id,
                    tracks.path,
                    tracks.sidecar_artwork_path,
                    COALESCE(sources.source_kind, roots.kind, 'local') AS source_kind,
                    COALESCE(roots.source_json, '{}') AS source_json,
                    COALESCE(roots.root_path, '') AS root_path,
                    sources.object_key,
                    sources.sidecar_object_key
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
        if not track_rows:
            raise ValueError("album has no tracks to delete")

        snapshots = tuple(album_delete_snapshot_from_row(row) for row in track_rows)
        local_sidecar_paths = deletable_local_sidecar_paths(
            connection,
            cleaned_album_id,
            snapshots,
        )
        remote_sidecar_refs = deletable_remote_sidecar_refs(
            connection,
            cleaned_album_id,
            snapshots,
        )
    finally:
        connection.close()

    album_name = str(album_row["album"]) if album_row["album"] else "<unknown album>"
    return AlbumDeleteJob(
        album_id=cleaned_album_id,
        album_label=album_display_label(artist_label, album_name),
        album_name=album_name,
        tracks=snapshots,
        local_sidecar_paths=local_sidecar_paths,
        remote_sidecar_refs=remote_sidecar_refs,
    )


def album_delete_snapshot_from_row(row: sqlite3.Row) -> AlbumDeleteSnapshot:
    return AlbumDeleteSnapshot(
        track_id=int(row["track_id"]),
        album_id=str(row["album_id"]) if row["album_id"] else "",
        path=str(row["path"]),
        source_kind=row_text(row, "source_kind", default=SOURCE_KIND_LOCAL),
        source_json=row_text(row, "source_json", default="{}"),
        root_path=row_text(row, "root_path"),
        object_key=optional_row_text(row, "object_key"),
        sidecar_artwork_path=optional_row_text(row, "sidecar_artwork_path"),
        sidecar_object_key=optional_row_text(row, "sidecar_object_key"),
    )


def optional_row_text(row: sqlite3.Row, name: str) -> str | None:
    value = row_text(row, name)
    return value if value else None


def snapshot_is_remote(snapshot: AlbumDeleteSnapshot) -> bool:
    return snapshot.source_kind == SOURCE_KIND_S3 or is_remote_path(snapshot.path)


def deletable_local_sidecar_paths(
    connection: sqlite3.Connection,
    album_id: str,
    snapshots: tuple[AlbumDeleteSnapshot, ...],
) -> tuple[str, ...]:
    candidates = tuple(
        dict.fromkeys(
            snapshot.sidecar_artwork_path
            for snapshot in snapshots
            if not snapshot_is_remote(snapshot)
            and snapshot.sidecar_artwork_path
            and snapshot.sidecar_artwork_path != snapshot.path
        )
    )
    if not candidates:
        return ()

    placeholders = placeholders_for(candidates)
    shared_paths = {
        str(row["sidecar_artwork_path"])
        for row in connection.execute(
            f"""
            SELECT DISTINCT sidecar_artwork_path
            FROM library_tracks
            WHERE sidecar_artwork_path IN ({placeholders})
                AND COALESCE(album_id, '') != ?
            """,
            (*candidates, album_id),
        )
        if row["sidecar_artwork_path"]
    }
    return tuple(path for path in candidates if path not in shared_paths)


def deletable_remote_sidecar_refs(
    connection: sqlite3.Connection,
    album_id: str,
    snapshots: tuple[AlbumDeleteSnapshot, ...],
) -> tuple[RemoteSidecarRef, ...]:
    refs = tuple(
        dict.fromkeys(
            RemoteSidecarRef(snapshot.source_json, snapshot.sidecar_object_key)
            for snapshot in snapshots
            if snapshot_is_remote(snapshot)
            and snapshot.sidecar_object_key
            and snapshot.sidecar_object_key != snapshot.object_key
        )
    )
    if not refs:
        return ()

    sidecar_keys = tuple(dict.fromkeys(ref.object_key for ref in refs))
    placeholders = placeholders_for(sidecar_keys)
    shared_refs = {
        RemoteSidecarRef(
            row_text(row, "source_json", default="{}"),
            str(row["sidecar_object_key"]),
        )
        for row in connection.execute(
            f"""
            SELECT
                COALESCE(roots.source_json, '{{}}') AS source_json,
                sources.sidecar_object_key
            FROM library_track_sources AS sources
            JOIN library_tracks AS tracks
                ON tracks.track_id = sources.track_id
            LEFT JOIN library_roots AS roots
                ON roots.position = sources.root_position
            WHERE sources.sidecar_object_key IN ({placeholders})
                AND COALESCE(tracks.album_id, '') != ?
            """,
            (*sidecar_keys, album_id),
        )
        if row["sidecar_object_key"]
    }
    return tuple(ref for ref in refs if ref not in shared_refs)


def delete_album_files(
    job: AlbumDeleteJob,
    *,
    cancel_check: Callable[[], None] | None = None,
) -> AlbumDeleteResult:
    local_paths: dict[str, AlbumDeleteSnapshot | None] = {}
    remote_objects: dict[RemoteSidecarRef, None] = {}

    for snapshot in job.tracks:
        if snapshot_is_remote(snapshot):
            object_key = snapshot.object_key.strip() if snapshot.object_key else ""
            if not object_key:
                raise ValueError("remote album delete requires S3 object key metadata")
            remote_objects[RemoteSidecarRef(snapshot.source_json, object_key)] = None
            continue
        local_paths.setdefault(snapshot.path, snapshot)

    for path in job.local_sidecar_paths:
        local_paths.setdefault(path, None)
    for ref in job.remote_sidecar_refs:
        remote_objects.setdefault(ref, None)

    local_deleted, touched_local_dirs = delete_local_paths(
        local_paths,
        job.tracks,
        cancel_check=cancel_check,
    )
    remote_deleted, touched_remote_prefixes = delete_remote_objects(
        tuple(remote_objects),
        cancel_check=cancel_check,
    )
    local_pruned = prune_touched_local_folders(
        touched_local_dirs,
        cancel_check=cancel_check,
    )
    remote_pruned = prune_touched_remote_prefixes(
        touched_remote_prefixes,
        cancel_check=cancel_check,
    )

    return AlbumDeleteResult(
        album_label=job.album_label,
        album_name=job.album_name,
        tracks_deleted=len(job.tracks),
        local_files_deleted=local_deleted,
        remote_objects_deleted=remote_deleted,
        local_folders_pruned=local_pruned,
        remote_prefixes_pruned=remote_pruned,
    )


def delete_local_paths(
    paths: dict[str, AlbumDeleteSnapshot | None],
    snapshots: tuple[AlbumDeleteSnapshot, ...],
    *,
    cancel_check: Callable[[], None] | None = None,
) -> tuple[int, dict[Path, Path]]:
    deleted = 0
    touched_dirs: dict[Path, Path] = {}
    snapshot_by_sidecar_path = {
        snapshot.sidecar_artwork_path: snapshot
        for snapshot in snapshots
        if snapshot.sidecar_artwork_path
    }
    for path_text, snapshot in paths.items():
        if cancel_check is not None:
            cancel_check()
        path = Path(path_text)
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        deleted += 1
        boundary_snapshot = snapshot or snapshot_by_sidecar_path.get(path_text)
        root = local_prune_root(boundary_snapshot, path)
        if root is not None:
            touched_dirs[path.parent] = root
    return deleted, touched_dirs


def local_prune_root(
    snapshot: AlbumDeleteSnapshot | None,
    path: Path,
) -> Path | None:
    if snapshot is None or not snapshot.root_path:
        return None
    root = Path(snapshot.root_path)
    try:
        if not path.parent.is_relative_to(root):
            return None
    except ValueError:
        return None
    return root


def prune_touched_local_folders(
    touched_dirs: dict[Path, Path],
    *,
    cancel_check: Callable[[], None] | None = None,
) -> int:
    pruned = 0
    for directory, root in sorted(
        touched_dirs.items(),
        key=lambda item: len(item[0].parts),
        reverse=True,
    ):
        current = directory
        while current != root:
            if cancel_check is not None:
                cancel_check()
            try:
                if not current.is_relative_to(root):
                    break
            except ValueError:
                break
            try:
                current.rmdir()
            except FileNotFoundError:
                current = current.parent
                continue
            except OSError as error:
                if error.errno in {errno.ENOTEMPTY, errno.EEXIST}:
                    break
                raise
            pruned += 1
            current = current.parent
    return pruned


def delete_remote_objects(
    refs: tuple[RemoteSidecarRef, ...],
    *,
    cancel_check: Callable[[], None] | None = None,
) -> tuple[int, dict[tuple[str, str], str]]:
    deleted = 0
    touched_prefixes: dict[tuple[str, str], str] = {}
    for ref in refs:
        if cancel_check is not None:
            cancel_check()
        remote = remote_root_from_source_json(ref.source_json)
        client = create_s3_client(remote)
        client.delete_object(Bucket=remote.bucket, Key=ref.object_key)
        deleted += 1
        prefix = remote_parent_prefix(ref.object_key)
        if remote_prefix_can_prune(prefix, remote.prefix):
            touched_prefixes[(ref.source_json, prefix)] = remote.prefix
    return deleted, touched_prefixes


def remote_parent_prefix(key: str) -> str:
    directory, separator, _name = key.rstrip("/").rpartition("/")
    return directory + "/" if separator else ""


def remote_parent_of_prefix(prefix: str) -> str:
    stripped = prefix.rstrip("/")
    directory, separator, _name = stripped.rpartition("/")
    return directory + "/" if separator else ""


def remote_prefix_can_prune(prefix: str, root_prefix: str) -> bool:
    if not prefix:
        return False
    if root_prefix and not prefix.startswith(root_prefix):
        return False
    return prefix != root_prefix


def prune_touched_remote_prefixes(
    touched_prefixes: dict[tuple[str, str], str],
    *,
    cancel_check: Callable[[], None] | None = None,
) -> int:
    pruned = 0
    pending = sorted(
        touched_prefixes,
        key=lambda item: item[1].count("/"),
        reverse=True,
    )
    seen: set[tuple[str, str]] = set()
    for source_json, prefix in pending:
        root_prefix = touched_prefixes[(source_json, prefix)]
        current = prefix
        while remote_prefix_can_prune(current, root_prefix):
            key = (source_json, current)
            if key in seen:
                break
            seen.add(key)
            if cancel_check is not None:
                cancel_check()
            remote = remote_root_from_source_json(source_json)
            client = create_s3_client(remote)
            if not remote_prefix_has_non_marker_objects(
                client,
                bucket=remote.bucket,
                prefix=current,
            ):
                if remote_prefix_marker_exists(
                    client,
                    bucket=remote.bucket,
                    prefix=current,
                ):
                    client.delete_object(Bucket=remote.bucket, Key=current)
                    pruned += 1
                current = remote_parent_of_prefix(current)
                continue
            break
    return pruned


def remote_prefix_has_non_marker_objects(
    client: object,
    *,
    bucket: str,
    prefix: str,
) -> bool:
    for item in iter_remote_prefix_items(client, bucket=bucket, prefix=prefix):
        key = item.get("Key")
        if isinstance(key, str) and key != prefix and not key.endswith("/"):
            return True
    return False


def remote_prefix_marker_exists(
    client: object,
    *,
    bucket: str,
    prefix: str,
) -> bool:
    return any(
        item.get("Key") == prefix
        for item in iter_remote_prefix_items(client, bucket=bucket, prefix=prefix)
    )


def iter_remote_prefix_items(
    client: object,
    *,
    bucket: str,
    prefix: str,
) -> Iterable[dict[str, object]]:
    continuation_token: str | None = None
    while True:
        kwargs: dict[str, object] = {"Bucket": bucket, "Prefix": prefix}
        if continuation_token:
            kwargs["ContinuationToken"] = continuation_token
        response = client.list_objects_v2(**kwargs)
        if not isinstance(response, dict):
            return
        for item in response.get("Contents", ()) or ():
            if isinstance(item, dict):
                yield item
        continuation_token = response.get("NextContinuationToken")
        if not response.get("IsTruncated") or not continuation_token:
            return


def run_delete_album_job(
    runtime: PlayerRuntime,
    job: AlbumDeleteJob,
    cancel_token: PlayerJobCancelToken,
) -> PlayerJobResult:
    started_at = perf_counter()
    result = delete_album_files(
        job,
        cancel_check=cancel_token.raise_if_canceled,
    )
    duration_seconds = perf_counter() - started_at
    LOGGER.info(
        "album delete completed for %s (tracks_deleted=%s, duration=%.2fs)",
        result.album_label,
        result.tracks_deleted,
        duration_seconds,
    )
    return PlayerJobResult(
        message=(
            f"Album files deleted for {job.album_label}. "
            "Rescan the library to remove it from Kukicha."
        ),
        context={
            "album": job.album_name,
            "tracks_deleted": result.tracks_deleted,
            "local_files_deleted": result.local_files_deleted,
            "remote_objects_deleted": result.remote_objects_deleted,
            "local_folders_pruned": result.local_folders_pruned,
            "remote_prefixes_pruned": result.remote_prefixes_pruned,
            "duration_seconds": duration_seconds,
            "rescan_recommended": True,
        },
    )
