from __future__ import annotations

import base64
import binascii
import configparser
import mimetypes
import re
import struct
import unicodedata
from collections.abc import Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable

from mutagen import File as MutagenFile

from ._compat import UTC
from .file_metadata import file_created_at
from .library_sources import (
    LibraryRootSource,
    RemoteRootConfig,
    SOURCE_KIND_LOCAL,
    SOURCE_KIND_S3,
    canonical_s3_path,
    create_s3_client,
    create_s3_client_for_workers,
    is_http_url_resource,
    is_remote_path,
    local_root_source,
    remote_root_display_label,
    remote_root_from_source_json,
    resolve_remote_worker_count,
)
from .models import (
    ALBUM_ARTWORK_HEIGHT,
    TRACK_ARTWORK_HEIGHT,
    MusicLibrary,
    PlaylistItemRecord,
    TrackArtwork,
    TrackRecord,
    TrackSourceRecord,
    UNKNOWN_METADATA_TAG,
    normalize_genre_values,
)

SUPPORTED_EXTENSIONS = {
    ".flac",
    ".m4a",
    ".m4b",
    ".m4p",
    ".m4r",
    ".mp3",
    ".oga",
    ".ogg",
    ".opus",
}
PLAYLIST_EXTENSIONS = {".m3u", ".m3u8", ".pls"}
ARTWORK_THUMBNAIL_HEIGHT = TRACK_ARTWORK_HEIGHT
ALBUM_ARTWORK_THUMBNAIL_HEIGHT = ALBUM_ARTWORK_HEIGHT
ARTWORK_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".gif")
ALBUM_ARTWORK_NAMES = ("cover", "folder", "front", "album", "artwork", "albumart")
RAW_KEY_ALIASES = {
    "tpe1": "artist",
    "tpe2": "albumartist",
    "tcom": "composer",
    "talb": "album",
    "tit1": "grouping",
    "tit2": "title",
    "trck": "tracknumber",
    "tpos": "discnumber",
    "tdor": "originaldate",
    "tdrc": "date",
    "tory": "originalyear",
    "tyer": "date",
    "tcon": "genre",
    "\xa9art": "artist",
    "aart": "albumartist",
    "\xa9wrt": "composer",
    "\xa9alb": "album",
    "\xa9grp": "grouping",
    "\xa9mvn": "movementname",
    "\xa9nam": "title",
    "\xa9wrk": "work",
    "\xa9day": "date",
    "\xa9gen": "genre",
    "----:com.apple.itunes:work": "work",
    "----:com.apple.itunes:movementname": "movementname",
    "----:com.apple.itunes:originaldate": "originaldate",
    "----:com.apple.itunes:originalyear": "originalyear",
    "----:com.apple.itunes:originalreleasedate": "originaldate",
    "----:com.apple.itunes:originalreleaseyear": "originalyear",
    "trkn": "tracknumber",
    "disk": "discnumber",
    "tcmp": "compilation",
    "cpil": "compilation",
    "----:com.apple.itunes:compilation": "compilation",
    "originaldate": "originaldate",
    "originalyear": "originalyear",
    "originalreleasedate": "originaldate",
    "originalreleaseyear": "originalyear",
}
PRIMARY_TAG_FIELDS: dict[str, tuple[str, ...]] = {
    "artist": ("artist", "albumartist", "album artist", "composer"),
    "album_artist": ("albumartist", "album artist", "artist"),
    "composer": ("composer",),
    "album": ("album",),
    "title": ("title",),
    "work": ("work",),
    "grouping": ("grouping", "contentgroup", "content group"),
    "movement_name": ("movementname", "movement name", "movement_name"),
    "is_compilation": ("compilation",),
    "track_number": ("tracknumber", "track"),
    "disc_number": ("discnumber", "disc"),
    "date": ("originaldate", "originalyear", "date", "year"),
}
GENRE_TAG_NAMES = {
    "genre",
    "genres",
    "style",
    "styles",
}
ARTWORK_TAG_NAMES = {
    "covr",
    "coverart",
    "metadata_block_picture",
    "metadata-block-picture",
    "metadatablockpicture",
    "picture",
}
ARTWORK_TAG_COMPACTS = {
    value.replace("_", "").replace("-", "") for value in ARTWORK_TAG_NAMES
}
DEFAULT_SCAN_PROGRESS_EVERY = 500
IGNORED_RAW_TAG_PREFIXES = {
    "apic",
    "coverart",
    "covr",
    "geob",
    "mcdifact",
    "metadatablockpicture",
    "picture",
    "priv",
    "ufid",
}
ITUNES_STORE_FILE_TYPES = {"m4a", "m4b", "m4p", "m4r"}
PLAYLIST_NAME_PREFIX = "#PLAYLIST:"
EXTINF_PREFIX = "#EXTINF:"
EXTGENRE_PREFIX = "#EXTGENRE:"
EXTALBUMARTURL_PREFIX = "#EXTALBUMARTURL:"
DOWNLOAD_CHUNK_SIZE = 1024 * 512


@dataclass(frozen=True, slots=True)
class IncrementalLibraryBuild:
    library: MusicLibrary
    scanned_paths: frozenset[str]
    reused_paths: frozenset[str]


@dataclass(frozen=True, slots=True)
class UploadedPlaylistParseResult:
    name: str
    items: tuple[PlaylistItemRecord, ...]
    skipped_relative_paths: tuple[str, ...]


@dataclass(slots=True)
class IncrementalScanAccumulator:
    tracks: list[TrackRecord]
    scanned_paths: set[str]
    reused_paths: set[str]
    music_count: int = 0


@dataclass(frozen=True, slots=True)
class FileSnapshot:
    modified_at_ns: int
    size_bytes: int


@dataclass(frozen=True, slots=True)
class SidecarArtworkSnapshot:
    path: str
    modified_at_ns: int
    size_bytes: int


@dataclass(frozen=True, slots=True)
class S3ObjectSnapshot:
    key: str
    size_bytes: int | None = None
    last_modified: str | None = None
    last_modified_ns: int | None = None
    etag: str | None = None
    version_id: str | None = None
    content_type: str | None = None


def build_library(
    roots: Iterable[Path],
    *,
    progress: Callable[[str], None] | None = None,
    progress_every: int = DEFAULT_SCAN_PROGRESS_EVERY,
    report_new_paths: bool = False,
    on_missing_required_tags: Callable[[TrackRecord, list[str]], None] | None = None,
) -> MusicLibrary:
    return build_incremental_library(
        roots,
        existing_tracks_by_path={},
        progress=progress,
        progress_every=progress_every,
        report_new_paths=report_new_paths,
        on_missing_required_tags=on_missing_required_tags,
    ).library


def build_incremental_library(
    roots: Iterable[Path | LibraryRootSource],
    *,
    existing_tracks_by_path: dict[str, TrackRecord],
    progress: Callable[[str], None] | None = None,
    progress_every: int = DEFAULT_SCAN_PROGRESS_EVERY,
    report_new_paths: bool = False,
    on_missing_required_tags: Callable[[TrackRecord, list[str]], None] | None = None,
    s3_client_factory: Callable[..., object] = create_s3_client,
    remote_workers: int | None = None,
) -> IncrementalLibraryBuild:
    clear_external_artwork_caches()
    source_list = scan_sources_for_roots(roots)
    scan = IncrementalScanAccumulator(
        tracks=[],
        scanned_paths=set(),
        reused_paths=set(),
    )
    progress_step = max(1, int(progress_every))

    for root_position, source in enumerate(source_list):
        if source.kind == SOURCE_KIND_S3:
            scan_s3_root(
                scan,
                source,
                root_position=root_position,
                root_total=len(source_list),
                existing_tracks_by_path=existing_tracks_by_path,
                progress=progress,
                progress_step=progress_step,
                report_new_paths=report_new_paths,
                on_missing_required_tags=on_missing_required_tags,
                s3_client_factory=s3_client_factory,
                remote_workers=remote_workers,
            )
            continue
        scan_local_root(
            scan,
            source,
            root_position=root_position,
            root_total=len(source_list),
            existing_tracks_by_path=existing_tracks_by_path,
            progress=progress,
            progress_step=progress_step,
            report_new_paths=report_new_paths,
            on_missing_required_tags=on_missing_required_tags,
        )

    if progress and scan.music_count % progress_step:
        progress(f"scanned {scan.music_count} music files")
    return IncrementalLibraryBuild(
        library=MusicLibrary(
            roots=[root_path_for_source(source) for source in source_list],
            tracks=scan.tracks,
            supported_extensions=sorted(SUPPORTED_EXTENSIONS),
            generated_at=datetime.now(UTC).isoformat(),
            playlists=[],
        ),
        scanned_paths=frozenset(scan.scanned_paths),
        reused_paths=frozenset(scan.reused_paths),
    )


def build_library_from_sources(
    sources: Iterable[LibraryRootSource],
    *,
    progress: Callable[[str], None] | None = None,
    progress_every: int = DEFAULT_SCAN_PROGRESS_EVERY,
    report_new_paths: bool = False,
    on_missing_required_tags: Callable[[TrackRecord, list[str]], None] | None = None,
    remote_workers: int | None = None,
) -> MusicLibrary:
    return build_incremental_library(
        sources,
        existing_tracks_by_path={},
        progress=progress,
        progress_every=progress_every,
        report_new_paths=report_new_paths,
        on_missing_required_tags=on_missing_required_tags,
        remote_workers=remote_workers,
    ).library


def build_incremental_library_from_sources(
    sources: Iterable[LibraryRootSource],
    *,
    existing_tracks_by_path: dict[str, TrackRecord],
    progress: Callable[[str], None] | None = None,
    progress_every: int = DEFAULT_SCAN_PROGRESS_EVERY,
    report_new_paths: bool = False,
    on_missing_required_tags: Callable[[TrackRecord, list[str]], None] | None = None,
    s3_client_factory: Callable[..., object] = create_s3_client,
    remote_workers: int | None = None,
) -> IncrementalLibraryBuild:
    return build_incremental_library(
        sources,
        existing_tracks_by_path=existing_tracks_by_path,
        progress=progress,
        progress_every=progress_every,
        report_new_paths=report_new_paths,
        on_missing_required_tags=on_missing_required_tags,
        s3_client_factory=s3_client_factory,
        remote_workers=remote_workers,
    )


def scan_sources_for_roots(
    roots: Iterable[Path | LibraryRootSource],
) -> tuple[LibraryRootSource, ...]:
    sources: list[LibraryRootSource] = []
    for position, root in enumerate(roots):
        if isinstance(root, LibraryRootSource):
            sources.append(root)
            continue
        sources.append(local_root_source(position, Path(root).expanduser().resolve()))
    return tuple(sources)


def root_path_for_source(source: LibraryRootSource) -> str:
    if source.kind == SOURCE_KIND_LOCAL:
        return str(Path(source.path).expanduser().resolve())
    return source.path


def scan_local_root(
    scan: IncrementalScanAccumulator,
    source: LibraryRootSource,
    *,
    root_position: int,
    root_total: int,
    existing_tracks_by_path: dict[str, TrackRecord],
    progress: Callable[[str], None] | None,
    progress_step: int,
    report_new_paths: bool,
    on_missing_required_tags: Callable[[TrackRecord, list[str]], None] | None,
) -> None:
    root = Path(source.path).expanduser().resolve()
    if progress:
        progress(f"scanning root {root_position + 1}/{root_total}: {root}")
    root_count = 0
    root_scanned = 0
    root_reused = 0
    for path in iter_music_files([root]):
        scan.music_count += 1
        root_count += 1
        path_text = str(path)
        existing_track = existing_tracks_by_path.get(path_text)
        if existing_track is not None and track_file_snapshot_matches(existing_track, path):
            track = reused_track_record(existing_track, root_position=root_position)
            was_scanned = False
            root_reused += 1
        else:
            if progress and report_new_paths and existing_track is None:
                progress(
                    f"root {root_position + 1}/{root_total} "
                    f"reading new file: {path_text}"
                )
            track = scan_track(path)
            track.root_position = root_position
            was_scanned = True
            root_scanned += 1
        record_incremental_track(
            scan,
            track,
            was_scanned=was_scanned,
            on_missing_required_tags=on_missing_required_tags,
        )
        emit_incremental_scan_progress(
            scan,
            root_position=root_position,
            root_total=root_total,
            root_count=root_count,
            root_scanned=root_scanned,
            root_reused=root_reused,
            progress=progress,
            progress_step=progress_step,
        )
    if progress:
        progress(
            root_scan_complete_message(
                root_position,
                root_total,
                root,
                root_count,
                read_count=root_scanned,
                reused_count=root_reused,
            )
        )


def scan_s3_root(
    scan: IncrementalScanAccumulator,
    source: LibraryRootSource,
    *,
    root_position: int,
    root_total: int,
    existing_tracks_by_path: dict[str, TrackRecord],
    progress: Callable[[str], None] | None,
    progress_step: int,
    report_new_paths: bool,
    on_missing_required_tags: Callable[[TrackRecord, list[str]], None] | None,
    s3_client_factory: Callable[..., object],
    remote_workers: int | None,
) -> None:
    root_label = source_progress_label(source)
    if progress:
        progress(f"scanning root {root_position + 1}/{root_total}: {root_label}")
    root_count = 0
    root_scanned = 0
    root_reused = 0
    remote = remote_root_from_source_json(source.source_json)
    worker_count = resolve_remote_worker_count(remote_workers)
    client = create_s3_client_for_workers(
        remote,
        s3_client_factory,
        remote_workers=worker_count,
    )
    root_progress = root_progress_callback(
        progress,
        root_position=root_position,
        root_total=root_total,
    )
    for track, was_scanned in iter_s3_tracks(
        remote,
        client,
        root_position=root_position,
        existing_tracks_by_path=existing_tracks_by_path,
        progress=root_progress,
        progress_every=progress_step,
        report_new_paths=report_new_paths,
        remote_workers=worker_count,
    ):
        scan.music_count += 1
        root_count += 1
        if was_scanned:
            root_scanned += 1
        else:
            root_reused += 1
        record_incremental_track(
            scan,
            track,
            was_scanned=was_scanned,
            on_missing_required_tags=on_missing_required_tags,
        )
        emit_incremental_scan_progress(
            scan,
            root_position=root_position,
            root_total=root_total,
            root_count=root_count,
            root_scanned=root_scanned,
            root_reused=root_reused,
            progress=progress,
            progress_step=progress_step,
        )
    if progress:
        progress(
            root_scan_complete_message(
                root_position,
                root_total,
                root_label,
                root_count,
                read_count=root_scanned,
                reused_count=root_reused,
            )
        )


def record_incremental_track(
    scan: IncrementalScanAccumulator,
    track: TrackRecord,
    *,
    was_scanned: bool,
    on_missing_required_tags: Callable[[TrackRecord, list[str]], None] | None,
) -> None:
    if was_scanned:
        scan.scanned_paths.add(track.path)
    else:
        scan.reused_paths.add(track.path)
    missing_fields = missing_required_tags(track)
    if missing_fields:
        if on_missing_required_tags is not None:
            on_missing_required_tags(track, missing_fields)
        return
    scan.tracks.append(track)


def emit_incremental_scan_progress(
    scan: IncrementalScanAccumulator,
    *,
    root_position: int,
    root_total: int,
    root_count: int,
    root_scanned: int,
    root_reused: int,
    progress: Callable[[str], None] | None,
    progress_step: int,
) -> None:
    if progress is None:
        return
    if scan.music_count % progress_step == 0:
        progress(f"scanned {scan.music_count} music files")
    if root_count % progress_step == 0:
        progress(
            root_scan_progress_message(
                root_position,
                root_total,
                root_count,
                read_count=root_scanned,
                reused_count=root_reused,
            )
        )


def reused_track_record(track: TrackRecord, *, root_position: int) -> TrackRecord:
    return replace(
        track,
        root_position=root_position,
        genres=list(track.genres),
        styles=list(track.styles),
    )


def source_progress_label(source: LibraryRootSource) -> str:
    if source.kind == SOURCE_KIND_S3:
        try:
            return remote_root_display_label(remote_root_from_source_json(source.source_json))
        except Exception:
            return source.path
    return source.path


def root_progress_callback(
    progress: Callable[[str], None] | None,
    *,
    root_position: int,
    root_total: int,
) -> Callable[[str], None] | None:
    if progress is None:
        return None

    def emit(message: str) -> None:
        progress(f"root {root_position + 1}/{root_total} {message}")

    return emit


def root_scan_progress_message(
    root_position: int,
    root_total: int,
    music_count: int,
    *,
    read_count: int,
    reused_count: int,
) -> str:
    return (
        f"root {root_position + 1}/{root_total} progress: "
        f"{music_count} music file(s) checked "
        f"({read_count} read, {reused_count} reused)"
    )


def root_scan_complete_message(
    root_position: int,
    root_total: int,
    label: object,
    music_count: int,
    *,
    read_count: int,
    reused_count: int,
) -> str:
    return (
        f"finished root {root_position + 1}/{root_total}: {label} "
        f"({music_count} music file(s), {read_count} read, "
        f"{reused_count} reused)"
    )


def iter_s3_tracks(
    remote: RemoteRootConfig,
    client: object,
    *,
    root_position: int,
    existing_tracks_by_path: dict[str, TrackRecord],
    progress: Callable[[str], None] | None = None,
    progress_every: int = DEFAULT_SCAN_PROGRESS_EVERY,
    report_new_paths: bool = False,
    remote_workers: int | None = None,
) -> Iterable[tuple[TrackRecord, bool]]:
    objects = list(
        iter_s3_objects(
            client,
            bucket=remote.bucket,
            prefix=remote.prefix,
            progress=(
                (lambda message: progress(f"listing remote objects: {message}"))
                if progress
                else None
            ),
        )
    )
    sidecars_by_directory = s3_sidecars_by_directory(objects)
    audio_objects = tuple(
        snapshot
        for snapshot in objects
        if Path(snapshot.key).suffix.casefold() in SUPPORTED_EXTENSIONS
    )
    sidecar_count = sum(
        1
        for snapshot in objects
        if Path(snapshot.key).suffix.casefold() in ARTWORK_IMAGE_EXTENSIONS
    )
    if progress:
        progress(
            f"found {len(audio_objects)} remote music file(s) "
            f"and {sidecar_count} sidecar artwork file(s)"
        )
    worker_count = resolve_remote_worker_count(remote_workers)
    if worker_count == 1:
        for index, snapshot in enumerate(audio_objects, start=1):
            canonical_path = canonical_s3_path(remote, snapshot.key)
            sidecar = selected_s3_sidecar(snapshot.key, sidecars_by_directory)
            existing_track = existing_tracks_by_path.get(canonical_path)
            if existing_track is not None and s3_track_snapshot_matches(
                existing_track,
                snapshot,
                sidecar,
            ):
                yield reused_track_record(existing_track, root_position=root_position), False
                continue
            if progress and report_new_paths and existing_track is None:
                progress(
                    f"reading new remote file {index}/{len(audio_objects)}: "
                    f"{snapshot.key}"
                )
            yield scan_s3_track(
                remote,
                client,
                snapshot,
                sidecar=sidecar,
                root_position=root_position,
            ), True
        return

    executor: ThreadPoolExecutor | None = None
    scan_entries: list[tuple[TrackRecord | Future[TrackRecord], bool]] = []
    try:
        for index, snapshot in enumerate(audio_objects, start=1):
            canonical_path = canonical_s3_path(remote, snapshot.key)
            sidecar = selected_s3_sidecar(snapshot.key, sidecars_by_directory)
            existing_track = existing_tracks_by_path.get(canonical_path)
            if existing_track is not None and s3_track_snapshot_matches(
                existing_track,
                snapshot,
                sidecar,
            ):
                scan_entries.append(
                    (
                        reused_track_record(
                            existing_track,
                            root_position=root_position,
                        ),
                        False,
                    )
                )
                continue
            if progress and report_new_paths and existing_track is None:
                progress(
                    f"reading new remote file {index}/{len(audio_objects)}: "
                    f"{snapshot.key}"
                )
            if executor is None:
                executor = ThreadPoolExecutor(max_workers=worker_count)
            scan_entries.append(
                (
                    executor.submit(
                        scan_s3_track,
                        remote,
                        client,
                        snapshot,
                        sidecar=sidecar,
                        root_position=root_position,
                    ),
                    True,
                )
            )
        for result, was_scanned in scan_entries:
            if isinstance(result, Future):
                try:
                    yield result.result(), True
                except Exception:
                    cancel_pending_s3_track_scans(scan_entries)
                    raise
                continue
            yield result, was_scanned
    finally:
        if executor is not None:
            executor.shutdown(cancel_futures=True)


def cancel_pending_s3_track_scans(
    scan_entries: Iterable[tuple[TrackRecord | Future[TrackRecord], bool]],
) -> None:
    for result, _was_scanned in scan_entries:
        if isinstance(result, Future):
            result.cancel()


def iter_s3_objects(
    client: object,
    *,
    bucket: str,
    prefix: str,
    progress: Callable[[str], None] | None = None,
) -> Iterable[S3ObjectSnapshot]:
    continuation_token: str | None = None
    page_count = 0
    object_count = 0
    while True:
        kwargs: dict[str, object] = {"Bucket": bucket, "Prefix": prefix}
        if continuation_token:
            kwargs["ContinuationToken"] = continuation_token
        if progress and page_count == 0:
            progress(f"starting list for s3://{bucket}/{prefix}")
        response = client.list_objects_v2(**kwargs)
        page_count += 1
        if not isinstance(response, dict):
            return
        for item in response.get("Contents", ()) or ():
            if not isinstance(item, dict):
                continue
            key = item.get("Key")
            if not isinstance(key, str) or not key or key.endswith("/"):
                continue
            object_count += 1
            yield s3_object_snapshot_from_item(item)
        if progress:
            progress(
                f"listed {object_count} object(s) across {page_count} page(s)"
            )
        continuation_token = response.get("NextContinuationToken")
        if not response.get("IsTruncated") or not continuation_token:
            break


def s3_object_snapshot_from_item(item: dict[str, object]) -> S3ObjectSnapshot:
    last_modified_value = item.get("LastModified")
    return S3ObjectSnapshot(
        key=str(item["Key"]),
        size_bytes=optional_int_value(item.get("Size")),
        last_modified=normalized_datetime_text(last_modified_value),
        last_modified_ns=datetime_value_ns(last_modified_value),
        etag=optional_text_value(item.get("ETag")),
        version_id=optional_text_value(item.get("VersionId")),
        content_type=optional_text_value(item.get("ContentType")),
    )


def s3_sidecars_by_directory(
    objects: Iterable[S3ObjectSnapshot],
) -> dict[str, dict[str, S3ObjectSnapshot]]:
    sidecars: dict[str, dict[str, S3ObjectSnapshot]] = {}
    for snapshot in objects:
        suffix = Path(snapshot.key).suffix.casefold()
        if suffix not in ARTWORK_IMAGE_EXTENSIONS:
            continue
        directory, _separator, name = snapshot.key.rpartition("/")
        directory_key = directory + "/" if directory else ""
        sidecars.setdefault(directory_key, {})[name] = snapshot
    return sidecars


def selected_s3_sidecar(
    audio_key: str,
    sidecars_by_directory: dict[str, dict[str, S3ObjectSnapshot]],
) -> S3ObjectSnapshot | None:
    directory, _separator, _name = audio_key.rpartition("/")
    directory_key = directory + "/" if directory else ""
    candidates = sidecars_by_directory.get(directory_key, {})
    if not candidates:
        return None
    for artwork_name in ALBUM_ARTWORK_NAMES:
        for extension in ARTWORK_IMAGE_EXTENSIONS:
            snapshot = candidates.get(f"{artwork_name}{extension}")
            if snapshot is not None:
                return snapshot

    normalized_names = {normalize_cache_component(name) for name in ALBUM_ARTWORK_NAMES}
    for name in sorted(candidates, key=str.casefold):
        path = Path(name)
        if path.suffix.casefold() not in ARTWORK_IMAGE_EXTENSIONS:
            continue
        if normalize_cache_component(path.stem) in normalized_names:
            return candidates[name]
    return None


def s3_track_snapshot_matches(
    track: TrackRecord,
    snapshot: S3ObjectSnapshot,
    sidecar: S3ObjectSnapshot | None,
) -> bool:
    source = track.source
    if source is None or source.source_kind != SOURCE_KIND_S3:
        return False
    return (
        source.object_key == snapshot.key
        and source.etag == snapshot.etag
        and source.version_id == snapshot.version_id
        and source.last_modified == snapshot.last_modified
        and source.content_type == snapshot.content_type
        and source.size_bytes == snapshot.size_bytes
        and source.sidecar_object_key == (sidecar.key if sidecar else None)
        and source.sidecar_etag == (sidecar.etag if sidecar else None)
        and source.sidecar_version_id == (sidecar.version_id if sidecar else None)
        and source.sidecar_last_modified == (sidecar.last_modified if sidecar else None)
        and source.sidecar_content_type == (sidecar.content_type if sidecar else None)
        and source.sidecar_size_bytes == (sidecar.size_bytes if sidecar else None)
    )


def scan_s3_track(
    remote: RemoteRootConfig,
    client: object,
    snapshot: S3ObjectSnapshot,
    *,
    sidecar: S3ObjectSnapshot | None,
    root_position: int,
) -> TrackRecord:
    canonical_path = canonical_s3_path(remote, snapshot.key)
    with TemporaryDirectory(prefix="kukicha-s3-scan-") as tempdir:
        temp_path = Path(tempdir)
        audio_path = temp_path / temp_download_name(snapshot.key, fallback="audio")
        try:
            object_metadata = download_s3_object(
                client,
                remote.bucket,
                snapshot.key,
                audio_path,
            )
            if sidecar is not None:
                download_s3_object(
                    client,
                    remote.bucket,
                    sidecar.key,
                    temp_path / temp_download_name(sidecar.key, fallback="cover"),
                )
            track = scan_track(audio_path)
        finally:
            pass
    apply_s3_track_snapshot(
        track,
        remote,
        snapshot,
        sidecar=sidecar,
        root_position=root_position,
        canonical_path=canonical_path,
        object_metadata=object_metadata,
    )
    return track


def temp_download_name(key: str, *, fallback: str) -> str:
    name = Path(key).name
    return name or fallback


def download_s3_object(
    client: object,
    bucket: str,
    key: str,
    destination: Path,
) -> dict[str, str]:
    response = client.get_object(Bucket=bucket, Key=key)
    body = response.get("Body") if isinstance(response, dict) else None
    if body is None or not hasattr(body, "read"):
        raise OSError(f"failed to download S3 object: {key}")
    metadata = s3_user_metadata(response)
    try:
        with destination.open("wb") as handle:
            while True:
                chunk = body.read(DOWNLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                handle.write(chunk)
    finally:
        close = getattr(body, "close", None)
        if callable(close):
            close()
    return metadata


def apply_s3_track_snapshot(
    track: TrackRecord,
    remote: RemoteRootConfig,
    snapshot: S3ObjectSnapshot,
    *,
    sidecar: S3ObjectSnapshot | None,
    root_position: int,
    canonical_path: str,
    object_metadata: dict[str, str] | None = None,
) -> None:
    track.path = canonical_path
    track.root_position = root_position
    track.file_created_at = s3_file_created_at(
        object_metadata or {},
        snapshot.last_modified,
    )
    track.file_modified_at_ns = snapshot.last_modified_ns
    track.file_size_bytes = snapshot.size_bytes
    if sidecar is None:
        track.sidecar_artwork_path = None
        track.sidecar_artwork_modified_at_ns = None
        track.sidecar_artwork_size_bytes = None
    else:
        track.sidecar_artwork_path = canonical_s3_path(remote, sidecar.key)
        track.sidecar_artwork_modified_at_ns = sidecar.last_modified_ns
        track.sidecar_artwork_size_bytes = sidecar.size_bytes
    track.source = TrackSourceRecord(
        source_kind=SOURCE_KIND_S3,
        root_position=root_position,
        canonical_path=canonical_path,
        object_key=snapshot.key,
        etag=snapshot.etag,
        version_id=snapshot.version_id,
        last_modified=snapshot.last_modified,
        content_type=snapshot.content_type,
        size_bytes=snapshot.size_bytes,
        sidecar_object_key=sidecar.key if sidecar else None,
        sidecar_etag=sidecar.etag if sidecar else None,
        sidecar_version_id=sidecar.version_id if sidecar else None,
        sidecar_last_modified=sidecar.last_modified if sidecar else None,
        sidecar_content_type=sidecar.content_type if sidecar else None,
        sidecar_size_bytes=sidecar.size_bytes if sidecar else None,
    )


def s3_user_metadata(response: dict[str, object]) -> dict[str, str]:
    metadata = response.get("Metadata")
    if not isinstance(metadata, dict):
        return {}
    normalized: dict[str, str] = {}
    for key, value in metadata.items():
        key_text = str(key).strip().casefold()
        value_text = optional_text_value(value)
        if key_text and value_text:
            normalized[key_text] = value_text
    return normalized


def s3_file_created_at(metadata: dict[str, str], fallback: str | None) -> str | None:
    for key in ("local-created-at", "local-ctime"):
        candidate = normalized_metadata_datetime_text(metadata.get(key))
        if candidate is not None:
            return candidate
    return fallback


def normalized_metadata_datetime_text(value: object) -> str | None:
    text = optional_text_value(value)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


def normalized_datetime_text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).replace(microsecond=0).isoformat()
    text = str(value).strip()
    return text or None


def datetime_value_ns(value: object) -> int | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return int(value.timestamp() * 1_000_000_000)
    return None


def optional_text_value(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def optional_int_value(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def iter_music_files(roots: Iterable[Path]) -> Iterable[Path]:
    for root in roots:
        if root.is_file() and root.suffix.casefold() in SUPPORTED_EXTENSIONS:
            yield root
            continue
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.casefold() in SUPPORTED_EXTENSIONS:
                yield path


def parse_uploaded_playlist_file(
    filename: str,
    data: bytes,
    tracks: Iterable[TrackRecord],
) -> UploadedPlaylistParseResult:
    upload_name = Path(str(filename or "playlist")).name
    suffix = Path(upload_name).suffix.casefold()
    if suffix not in PLAYLIST_EXTENSIONS:
        allowed = ", ".join(sorted(PLAYLIST_EXTENSIONS))
        raise ValueError(f"playlist file extension must be one of: {allowed}")
    text = read_uploaded_playlist_text(upload_name, data)
    tracks_by_path = {normalize_playlist_track_path(track.path): track for track in tracks}
    if suffix == ".pls":
        return parse_uploaded_pls_playlist(upload_name, text, tracks_by_path)
    return parse_uploaded_m3u_playlist(upload_name, text, tracks_by_path)


def parse_uploaded_m3u_playlist(
    filename: str,
    text: str,
    tracks_by_path: dict[str, TrackRecord],
) -> UploadedPlaylistParseResult:
    name = Path(filename).stem or "Playlist"
    items: list[PlaylistItemRecord] = []
    skipped: list[str] = []
    pending_title: str | None = None
    pending_duration: float | None = None
    pending_duration_is_indeterminate = False
    pending_genre: str | None = None
    pending_cover_url: str | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line_key = line.casefold()
        if line.startswith("#"):
            if line_key.startswith(PLAYLIST_NAME_PREFIX.casefold()):
                candidate = line[len(PLAYLIST_NAME_PREFIX) :].strip()
                if candidate:
                    name = candidate
                continue
            if line_key.startswith(EXTINF_PREFIX.casefold()):
                (
                    pending_duration,
                    pending_title,
                    pending_duration_is_indeterminate,
                ) = parse_extinf(line[len(EXTINF_PREFIX) :])
                continue
            if line_key.startswith(EXTGENRE_PREFIX.casefold()):
                pending_genre = line[len(EXTGENRE_PREFIX) :].strip() or None
                continue
            if line_key.startswith(EXTALBUMARTURL_PREFIX.casefold()):
                pending_cover_url = line[len(EXTALBUMARTURL_PREFIX) :].strip() or None
                continue
            continue

        item_path = normalize_uploaded_playlist_resource(line)
        if item_path is None:
            skipped.append(line)
        else:
            items.append(
                playlist_item_record(
                    item_path,
                    tracks_by_path,
                    title=pending_title,
                    duration_seconds=pending_duration,
                    duration_is_indeterminate=pending_duration_is_indeterminate,
                    genre=pending_genre,
                    cover_url=pending_cover_url,
                )
            )
        pending_title = None
        pending_duration = None
        pending_duration_is_indeterminate = False
        pending_genre = None
        pending_cover_url = None

    return UploadedPlaylistParseResult(
        name=name,
        items=tuple(items),
        skipped_relative_paths=tuple(skipped),
    )


def parse_uploaded_pls_playlist(
    filename: str,
    text: str,
    tracks_by_path: dict[str, TrackRecord],
) -> UploadedPlaylistParseResult:
    name = Path(filename).stem or "Playlist"
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str.casefold
    try:
        parser.read_string(text)
    except configparser.Error as error:
        raise ValueError("playlist file is not valid PLS") from error
    section_name = pls_playlist_section_name(parser)
    if section_name is None:
        raise ValueError("playlist file is missing [playlist] section")
    section = parser[section_name]
    version = section.get("version", fallback=None)
    if version is not None and version.strip() and version.strip() != "2":
        raise ValueError("playlist file has unsupported PLS version")

    items: list[PlaylistItemRecord] = []
    skipped: list[str] = []
    for index in pls_playlist_indexes(section):
        resource = section.get(f"file{index}", fallback="").strip()
        if not resource:
            continue
        item_path = normalize_uploaded_playlist_resource(resource)
        if item_path is None:
            skipped.append(resource)
            continue
        title = section.get(f"title{index}", fallback="").strip() or None
        duration, duration_is_indeterminate = parse_pls_length(
            section.get(f"length{index}", fallback=None)
        )
        items.append(
            playlist_item_record(
                item_path,
                tracks_by_path,
                title=title,
                duration_seconds=duration,
                duration_is_indeterminate=duration_is_indeterminate,
            )
        )

    return UploadedPlaylistParseResult(
        name=name,
        items=tuple(items),
        skipped_relative_paths=tuple(skipped),
    )


def playlist_item_record(
    item_path: str,
    tracks_by_path: dict[str, TrackRecord],
    *,
    title: str | None = None,
    duration_seconds: float | None = None,
    duration_is_indeterminate: bool = False,
    genre: str | None = None,
    cover_url: str | None = None,
) -> PlaylistItemRecord:
    track = tracks_by_path.get(item_path) if not is_url_resource(item_path) else None
    if track is not None:
        return PlaylistItemRecord(
            path=track.path,
            track_id=track.track_id,
        )
    return PlaylistItemRecord(
        path=item_path,
        title=title or item_path,
        duration_seconds=None if duration_is_indeterminate else duration_seconds,
        duration_is_indeterminate=duration_is_indeterminate,
        genre=genre,
        cover_url=cover_url,
    )


def pls_playlist_section_name(parser: configparser.ConfigParser) -> str | None:
    for section_name in parser.sections():
        if section_name.strip().casefold() == "playlist":
            return section_name
    return None


def pls_playlist_indexes(section: configparser.SectionProxy) -> list[int]:
    count = parse_pls_entry_count(section.get("numberofentries", fallback=None))
    if count is not None:
        return list(range(1, count + 1))
    indexes: set[int] = set()
    for key in section:
        match = re.fullmatch(r"file(\d+)", key)
        if match is None:
            continue
        index = int(match.group(1))
        if index > 0:
            indexes.add(index)
    return sorted(indexes)


def parse_pls_entry_count(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        count = int(value.strip())
    except ValueError:
        return None
    return count if count >= 0 else None


def parse_pls_length(value: str | None) -> tuple[float | None, bool]:
    if value is None:
        return None, False
    duration = parse_playlist_duration(value)
    if duration is None:
        return None, False
    if duration < 0:
        return None, True
    return duration, False


def read_uploaded_playlist_text(filename: str, data: bytes) -> str:
    if not data:
        raise ValueError("playlist file is empty")
    if Path(filename).suffix.casefold() == ".m3u":
        try:
            return data.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise ValueError("playlist file is not valid UTF-8") from error
    return data.decode("utf-8-sig", errors="replace")


def parse_extinf(value: str) -> tuple[float | None, str | None, bool]:
    duration_text, separator, title = value.partition(",")
    duration = parse_playlist_duration(duration_text)
    duration_is_indeterminate = duration is not None and duration <= 0
    resolved_title = title.strip() if separator and title.strip() else None
    return (
        None if duration_is_indeterminate else duration,
        resolved_title,
        duration_is_indeterminate,
    )


def parse_playlist_duration(value: str) -> float | None:
    match = re.match(r"\s*(-?\d+(?:\.\d+)?)", value)
    if match is None:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def normalize_uploaded_playlist_resource(value: str) -> str | None:
    text = value.strip()
    if is_url_resource(text) or is_remote_path(text):
        return text
    path = Path(text).expanduser()
    if not path.is_absolute():
        return None
    return normalize_local_playlist_path(str(path))


def normalize_playlist_track_path(value: str) -> str:
    text = str(value).strip()
    if is_remote_path(text):
        return text
    return normalize_local_playlist_path(text)


def normalize_local_playlist_path(value: str) -> str:
    return str(Path(value).expanduser().resolve(strict=False))


def is_url_resource(value: str) -> bool:
    return is_http_url_resource(value)


def scan_track(path: Path) -> TrackRecord:
    record = TrackRecord(
        path=str(path),
        file_created_at=file_created_at(path),
        file_type=path.suffix.lower().lstrip("."),
    )
    apply_track_file_snapshot(record, path)

    try:
        audio = MutagenFile(path, easy=False)
    except Exception as exc:
        record.scan_error = str(exc)
        return record

    try:
        easy_audio = MutagenFile(path, easy=True)
    except Exception:
        easy_audio = None

    if audio is None:
        return record

    tags = normalize_tags(getattr(audio, "tags", None), getattr(easy_audio, "tags", None))
    genres = collect_genres(tags)
    info = getattr(audio, "info", None)

    record.artist = first_value(tags, PRIMARY_TAG_FIELDS["artist"])
    record.album_artist = first_value(tags, PRIMARY_TAG_FIELDS["album_artist"])
    record.composer = first_value(tags, PRIMARY_TAG_FIELDS["composer"])
    if not (record.album_artist or record.artist):
        record.album_artist = UNKNOWN_METADATA_TAG
    record.album = first_value(tags, PRIMARY_TAG_FIELDS["album"]) or UNKNOWN_METADATA_TAG
    record.title = first_value(tags, PRIMARY_TAG_FIELDS["title"]) or fallback_track_title(path)
    record.work = first_value(tags, PRIMARY_TAG_FIELDS["work"])
    record.grouping = first_value(tags, PRIMARY_TAG_FIELDS["grouping"])
    record.movement_name = first_value(tags, PRIMARY_TAG_FIELDS["movement_name"])
    record.is_compilation = first_bool(tags, PRIMARY_TAG_FIELDS["is_compilation"])
    record.track_number = first_value(tags, PRIMARY_TAG_FIELDS["track_number"])
    record.disc_number = first_value(tags, PRIMARY_TAG_FIELDS["disc_number"])
    record.date = first_value(tags, PRIMARY_TAG_FIELDS["date"])
    if record.file_type in ITUNES_STORE_FILE_TYPES:
        record.itunes_store_track_id = first_numeric_value(tags, ("cnid",))
        record.itunes_store_album_id = first_numeric_value(tags, ("plid",))
    record.genres = genres or [UNKNOWN_METADATA_TAG]
    artwork_by_height = extract_preferred_artworks(
        audio,
        path,
        heights=(
            ARTWORK_THUMBNAIL_HEIGHT,
            ALBUM_ARTWORK_THUMBNAIL_HEIGHT,
        ),
    )
    record.artwork = artwork_by_height.get(ARTWORK_THUMBNAIL_HEIGHT)
    record.album_artwork = artwork_by_height.get(ALBUM_ARTWORK_THUMBNAIL_HEIGHT)
    record.duration_seconds = round(float(getattr(info, "length", 0.0)), 3) or None
    bitrate = getattr(info, "bitrate", None)
    record.bitrate = int(bitrate) if bitrate else None
    return record


def apply_track_file_snapshot(record: TrackRecord, path: Path) -> None:
    file_snapshot = track_file_snapshot(path)
    if file_snapshot is not None:
        record.file_modified_at_ns = file_snapshot.modified_at_ns
        record.file_size_bytes = file_snapshot.size_bytes

    sidecar_snapshot = selected_sidecar_artwork_snapshot(path)
    if sidecar_snapshot is None:
        record.sidecar_artwork_path = None
        record.sidecar_artwork_modified_at_ns = None
        record.sidecar_artwork_size_bytes = None
        return

    record.sidecar_artwork_path = sidecar_snapshot.path
    record.sidecar_artwork_modified_at_ns = sidecar_snapshot.modified_at_ns
    record.sidecar_artwork_size_bytes = sidecar_snapshot.size_bytes


def track_file_snapshot_matches(track: TrackRecord, path: Path) -> bool:
    file_snapshot = track_file_snapshot(path)
    if file_snapshot is None:
        return False
    if track.file_modified_at_ns != file_snapshot.modified_at_ns:
        return False
    if track.file_size_bytes != file_snapshot.size_bytes:
        return False

    sidecar_snapshot = selected_sidecar_artwork_snapshot(path)
    if sidecar_snapshot is None:
        return (
            track.sidecar_artwork_path is None
            and track.sidecar_artwork_modified_at_ns is None
            and track.sidecar_artwork_size_bytes is None
        )
    return (
        track.sidecar_artwork_path == sidecar_snapshot.path
        and track.sidecar_artwork_modified_at_ns == sidecar_snapshot.modified_at_ns
        and track.sidecar_artwork_size_bytes == sidecar_snapshot.size_bytes
    )


def track_file_snapshot(path: Path) -> FileSnapshot | None:
    try:
        stat_result = path.stat()
    except OSError:
        return None
    return FileSnapshot(
        modified_at_ns=int(getattr(stat_result, "st_mtime_ns", int(stat_result.st_mtime * 1_000_000_000))),
        size_bytes=int(stat_result.st_size),
    )


def selected_sidecar_artwork_snapshot(path: Path) -> SidecarArtworkSnapshot | None:
    return cached_sidecar_artwork_snapshot(str(path.parent))


@lru_cache(maxsize=4096)
def cached_sidecar_artwork_snapshot(directory: str) -> SidecarArtworkSnapshot | None:
    for artwork_path in iter_external_artwork_paths(Path(directory)):
        snapshot = track_file_snapshot(artwork_path)
        if snapshot is None:
            continue
        return SidecarArtworkSnapshot(
            path=str(artwork_path),
            modified_at_ns=snapshot.modified_at_ns,
            size_bytes=snapshot.size_bytes,
        )
    return None


def fallback_track_title(path: Path) -> str:
    return path.stem.strip() or UNKNOWN_METADATA_TAG


def write_track_audio_tags(
    path: Path,
    *,
    artist: str | None,
    album_artist: str | None,
    album: str | None,
    track_number: str | None,
    title: str | None,
    genre: str | None,
) -> None:
    resolved_artist = artist.strip() if artist else ""
    resolved_album_artist = album_artist.strip() if album_artist else ""
    resolved_album = album.strip() if album else ""
    resolved_track_number = track_number.strip() if track_number else ""
    resolved_title = title.strip() if title else ""
    genre_values = audio_genre_tag_values(genre)

    try:
        audio = MutagenFile(path, easy=True)
    except Exception as error:
        raise OSError(f"failed to open tags for {path}: {error}") from error

    if audio is None:
        raise OSError(f"unsupported or unreadable audio file: {path}")

    if getattr(audio, "tags", None) is None and hasattr(audio, "add_tags"):
        try:
            audio.add_tags()
        except Exception:
            pass

    try:
        if resolved_artist:
            audio["artist"] = [resolved_artist]
        else:
            delete_easy_tag(audio, "artist")

        if resolved_album_artist:
            audio["albumartist"] = [resolved_album_artist]
        else:
            delete_easy_tag(audio, "albumartist")

        if resolved_album:
            audio["album"] = [resolved_album]
        else:
            delete_easy_tag(audio, "album")

        if resolved_track_number:
            audio["tracknumber"] = [resolved_track_number]
        else:
            delete_easy_tag(audio, "tracknumber")

        if resolved_title:
            audio["title"] = [resolved_title]
        else:
            delete_easy_tag(audio, "title")

        if genre_values:
            audio["genre"] = genre_values
        else:
            delete_easy_tag(audio, "genre")

        audio.save()
    except Exception as error:
        raise OSError(f"failed to update tags for {path}: {error}") from error


def write_album_audio_tags(
    path: Path,
    *,
    album_artist: str,
    album: str,
    genre: str,
) -> None:
    resolved_album_artist = album_artist.strip()
    resolved_album = album.strip()
    genre_values = audio_genre_tag_values(genre)

    try:
        audio = MutagenFile(path, easy=True)
    except Exception as error:
        raise OSError(f"failed to open tags for {path}: {error}") from error

    if audio is None:
        raise OSError(f"unsupported or unreadable audio file: {path}")

    if getattr(audio, "tags", None) is None and hasattr(audio, "add_tags"):
        try:
            audio.add_tags()
        except Exception:
            pass

    try:
        audio["albumartist"] = [resolved_album_artist]
        audio["album"] = [resolved_album]
        if genre_values:
            audio["genre"] = genre_values
        else:
            delete_easy_tag(audio, "genre")
        audio.save()
    except Exception as error:
        raise OSError(f"failed to update tags for {path}: {error}") from error


def audio_genre_tag_values(genre: str | None) -> list[str]:
    return normalize_genre_values((genre,))


def missing_required_tags(track: TrackRecord) -> list[str]:
    missing: list[str] = []
    if not (track.album_artist or track.artist):
        missing.append("artist")
    if not track.album:
        missing.append("album")
    if not track.title:
        missing.append("title")
    return missing


def normalize_tags(*tag_sets: object) -> dict[str, list[str]]:
    normalized: dict[str, list[str]] = {}
    for tags in tag_sets:
        if not tags:
            continue
        for key in tags.keys():
            normalized_key = canonicalize_key(str(key))
            if is_ignored_raw_tag_key(normalized_key):
                continue
            values = tags.get(key, [])
            flattened = normalize_values(values)
            if not flattened:
                continue
            bucket = normalized.setdefault(normalized_key, [])
            for value in flattened:
                if value not in bucket:
                    bucket.append(value)
    return normalized


def normalize_values(values: object) -> list[str]:
    if values is None:
        return []
    if hasattr(values, "text"):
        return normalize_values(values.text)
    if hasattr(values, "value"):
        return normalize_values(values.value)
    if isinstance(values, (str, bytes)):
        return [stringify_value(values)]
    if isinstance(values, tuple):
        return [stringify_value(values)]
    if isinstance(values, list | set):
        flattened: list[str] = []
        for value in values:
            flattened.extend(normalize_values(value))
        return [value for value in flattened if value]
    return [stringify_value(values)]


def stringify_value(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").strip()
    if isinstance(value, tuple) and len(value) == 2 and all(isinstance(item, int) for item in value):
        current, total = value
        return f"{current}/{total}" if total else str(current)
    return str(value).strip()


def canonicalize_key(key: str) -> str:
    cleaned = key.strip().casefold()
    compact = cleaned.replace(" ", "")
    return RAW_KEY_ALIASES.get(compact, cleaned)


def delete_easy_tag(audio: object, key: str) -> None:
    try:
        del audio[key]
    except KeyError:
        return


def first_value(tags: dict[str, list[str]], keys: Iterable[str]) -> str | None:
    for key in keys:
        values = tags.get(key.casefold())
        if values:
            return values[0]
    return None


def first_bool(tags: dict[str, list[str]], keys: Iterable[str]) -> bool:
    for key in keys:
        values = tags.get(key.casefold())
        if values:
            return any(is_truthy_tag_value(value) for value in values)
    return False


def first_numeric_value(tags: dict[str, list[str]], keys: Iterable[str]) -> str | None:
    value = first_value(tags, keys)
    if not value:
        return None
    digits = "".join(character for character in value if character.isdigit())
    return digits or None


def is_truthy_tag_value(value: str) -> bool:
    folded = value.strip().casefold()
    return folded in {"1", "true", "t", "yes", "y", "on"}


def collect_genres(tags: dict[str, list[str]]) -> list[str]:
    collected: dict[str, str] = {}
    for key, values in tags.items():
        if is_genre_key(key):
            for genre in normalize_genre_values(values):
                collected.setdefault(genre.casefold(), genre)
    return sorted(collected.values(), key=str.casefold)


def is_genre_key(key: str) -> bool:
    compact = key.replace(" ", "")
    return compact in GENRE_TAG_NAMES or "genre" in compact or "style" in compact


def extract_preferred_artworks(
    audio: object,
    path: Path,
    *,
    heights: Iterable[int],
) -> dict[int, TrackArtwork]:
    height_values = tuple(sorted({height for height in heights}))
    if not height_values:
        return {}

    artwork_by_height = extract_external_artworks(path, heights=height_values)
    if artwork_by_height:
        return artwork_by_height
    artwork = extract_artwork_source(audio)
    if artwork is not None:
        artwork_by_height = thumbnail_artworks(artwork, heights=height_values)
        if artwork_by_height:
            return artwork_by_height
    return {}


def extract_artwork_source(audio: object) -> TrackArtwork | None:
    for picture in getattr(audio, "pictures", []) or []:
        artwork = artwork_from_picture_object(picture)
        if artwork:
            return artwork

    tags = getattr(audio, "tags", None)
    if not tags:
        return None

    coverart_mime_type = tag_first_value(tags, "coverartmime")
    for key in tags.keys():
        normalized_key = canonicalize_key(str(key))
        if not is_artwork_key(normalized_key):
            continue
        values = tags.get(key, [])
        for value in iter_artwork_values(values):
            artwork = artwork_from_tag_value(
                value,
                key=normalized_key,
                mime_type_hint=coverart_mime_type,
            )
            if artwork:
                return artwork
    return None


def extract_external_artworks(
    path: Path,
    *,
    heights: Iterable[int],
) -> dict[int, TrackArtwork]:
    height_values = tuple(sorted({height for height in heights}))
    if not height_values:
        return {}
    return cached_external_artworks(str(path.parent), height_values)


@lru_cache(maxsize=4096)
def cached_external_artworks(
    directory: str,
    heights: tuple[int, ...],
) -> dict[int, TrackArtwork]:
    artwork = cached_external_artwork(directory)
    if artwork is None:
        return {}
    return thumbnail_artworks(artwork, heights=heights)


@lru_cache(maxsize=4096)
def cached_external_artwork(directory: str) -> TrackArtwork | None:
    for artwork_path in iter_external_artwork_paths(Path(directory)):
        artwork = artwork_from_image_path(artwork_path)
        if artwork:
            return artwork
    return None


def clear_external_artwork_caches() -> None:
    cached_external_artwork.cache_clear()
    cached_external_artworks.cache_clear()
    cached_sidecar_artwork_snapshot.cache_clear()


def iter_external_artwork_paths(directory: Path) -> Iterable[Path]:
    yield from iter_named_artwork_files(directory, ALBUM_ARTWORK_NAMES)


def iter_named_artwork_files(directory: Path, names: Iterable[str]) -> Iterable[Path]:
    if not directory.is_dir():
        return

    seen: set[Path] = set()
    for name in names:
        for extension in ARTWORK_IMAGE_EXTENSIONS:
            candidate = directory / f"{name}{extension}"
            if candidate not in seen and candidate.is_file():
                seen.add(candidate)
                yield candidate

    normalized_names = {normalize_cache_component(name) for name in names}
    try:
        children = sorted(directory.iterdir(), key=lambda child: child.name.casefold())
    except OSError:
        return
    for child in children:
        if child in seen or not child.is_file() or child.suffix.casefold() not in ARTWORK_IMAGE_EXTENSIONS:
            continue
        if normalize_cache_component(child.stem) in normalized_names:
            seen.add(child)
            yield child


def artwork_from_image_path(path: Path) -> TrackArtwork | None:
    if path.suffix.casefold() not in ARTWORK_IMAGE_EXTENSIONS:
        return None
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if not data:
        return None
    return TrackArtwork(
        mime_type=sniff_image_mime_type(data, mimetypes.guess_type(path.name)[0]),
        data=data,
    )


def normalize_cache_component(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold().strip()


def tag_first_value(tags: object, key: str) -> str | None:
    if not hasattr(tags, "keys") or not hasattr(tags, "get"):
        return None
    values: object = []
    for tag_key in tags.keys():
        if canonicalize_key(str(tag_key)) == key:
            values = tags.get(tag_key, [])
            break
    normalized = normalize_values(values)
    return normalized[0] if normalized else None


def iter_artwork_values(values: object) -> Iterable[object]:
    if values is None:
        return []
    if isinstance(values, list | set | tuple):
        return values
    return [values]


def artwork_from_tag_value(
    value: object,
    *,
    key: str,
    mime_type_hint: str | None = None,
) -> TrackArtwork | None:
    artwork = artwork_from_picture_object(value)
    if artwork:
        return artwork

    if isinstance(value, bytes | bytearray):
        data = bytes(value)
        return TrackArtwork(mime_type=artwork_mime_type(value, data, mime_type_hint), data=data)

    if isinstance(value, str):
        data = decode_base64_data(value)
        if not data:
            return None
        if is_flac_picture_key(key):
            return parse_flac_picture_block(data)
        return TrackArtwork(mime_type=sniff_image_mime_type(data, mime_type_hint), data=data)

    data = getattr(value, "data", None)
    if isinstance(data, bytes | bytearray):
        raw_data = bytes(data)
        return TrackArtwork(
            mime_type=artwork_mime_type(value, raw_data, mime_type_hint),
            data=raw_data,
        )
    return None


def artwork_from_picture_object(value: object) -> TrackArtwork | None:
    data = getattr(value, "data", None)
    if not isinstance(data, bytes | bytearray):
        return None
    raw_data = bytes(data)
    mime_type = getattr(value, "mime", None)
    return TrackArtwork(mime_type=sniff_image_mime_type(raw_data, mime_type), data=raw_data)


def thumbnail_artwork(
    artwork: TrackArtwork,
    *,
    height: int = ARTWORK_THUMBNAIL_HEIGHT,
) -> TrackArtwork | None:
    return thumbnail_artworks(artwork, heights=(height,)).get(height)


def thumbnail_artworks(
    artwork: TrackArtwork,
    *,
    heights: Iterable[int],
) -> dict[int, TrackArtwork]:
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise RuntimeError("Pillow is required to extract cover art thumbnails.") from exc

    height_values = sorted({height for height in heights})
    if not height_values:
        return {}

    try:
        with Image.open(BytesIO(artwork.data)) as image:
            transposed = ImageOps.exif_transpose(image)
            if not transposed.width or not transposed.height:
                return {}
            has_alpha = image_has_alpha(transposed)
            converted = transposed.convert("RGBA" if has_alpha else "RGB")
            thumbnails: dict[int, TrackArtwork] = {}
            for height in height_values:
                target_height = max(1, height)
                width = max(1, round(transposed.width * target_height / transposed.height))
                resized = converted.resize(
                    (width, target_height),
                    Image.Resampling.LANCZOS,
                )
                output = BytesIO()
                if has_alpha:
                    resized.save(output, format="PNG", optimize=True)
                    thumbnails[height] = TrackArtwork(
                        mime_type="image/png",
                        data=output.getvalue(),
                    )
                    continue
                resized.save(output, format="JPEG", quality=85, optimize=True)
                thumbnails[height] = TrackArtwork(
                    mime_type="image/jpeg",
                    data=output.getvalue(),
                )
            return thumbnails
    except Exception:
        return {}


def image_has_alpha(image: object) -> bool:
    getbands = getattr(image, "getbands", None)
    if callable(getbands) and "A" in getbands():
        return True
    return getattr(image, "mode", "") == "P" and "transparency" in getattr(image, "info", {})


def artwork_mime_type(value: object, data: bytes, mime_type_hint: str | None = None) -> str:
    image_format = getattr(value, "imageformat", None)
    if image_format == 13:
        return "image/jpeg"
    if image_format == 14:
        return "image/png"
    return sniff_image_mime_type(data, mime_type_hint)


def sniff_image_mime_type(data: bytes, mime_type_hint: str | None = None) -> str:
    if mime_type_hint and mime_type_hint.startswith("image/"):
        return mime_type_hint
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def decode_base64_data(value: str) -> bytes | None:
    compact = "".join(value.split())
    if not compact:
        return None
    try:
        return base64.b64decode(compact, validate=True)
    except (binascii.Error, ValueError):
        return None


def parse_flac_picture_block(data: bytes) -> TrackArtwork | None:
    offset = 4
    mime_length, offset = read_flac_picture_uint(data, offset)
    if mime_length is None:
        return None
    mime_end = offset + mime_length
    if mime_end > len(data):
        return None
    mime_type = data[offset:mime_end].decode("utf-8", errors="replace")
    offset = mime_end

    description_length, offset = read_flac_picture_uint(data, offset)
    if description_length is None:
        return None
    offset += description_length
    if offset + 20 > len(data):
        return None
    offset += 16

    image_length, offset = read_flac_picture_uint(data, offset)
    if image_length is None:
        return None
    image_end = offset + image_length
    if image_end > len(data):
        return None
    image_data = data[offset:image_end]
    if not image_data:
        return None
    return TrackArtwork(
        mime_type=sniff_image_mime_type(image_data, mime_type),
        data=image_data,
    )


def read_flac_picture_uint(data: bytes, offset: int) -> tuple[int | None, int]:
    if offset + 4 > len(data):
        return None, offset
    return struct.unpack_from(">I", data, offset)[0], offset + 4


def is_artwork_key(key: str) -> bool:
    compact = compact_tag_key(key)
    return compact.startswith("apic") or compact in ARTWORK_TAG_COMPACTS


def is_ignored_raw_tag_key(key: str) -> bool:
    compact = compact_tag_key(key)
    return any(compact.startswith(prefix) for prefix in IGNORED_RAW_TAG_PREFIXES)


def is_flac_picture_key(key: str) -> bool:
    return compact_tag_key(key) == "metadatablockpicture"


def compact_tag_key(key: str) -> str:
    return key.replace(" ", "").replace("_", "").replace("-", "").casefold()
