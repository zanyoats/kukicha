from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, fields
import logging
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Any
import urllib.parse

from ...audio_types import content_type_for_name
from ..queries import AlbumNotFoundError, TrackNotFoundError
from ..database import connect_existing_database
from ...album_artists import (
    DEFAULT_ALBUM_ARTIST_SPLIT_PATTERNS,
    album_artist_has_mapping_pattern,
    album_artist_id_text,
    default_album_artist_mapping,
    display_album_artists,
    mapped_album_artists_from_text,
)
from ...display import display_album_title
from ...discogs import file_album_id_from_album_id, local_album_id
from ...library_sources import (
    SOURCE_KIND_LOCAL,
    SOURCE_KIND_S3,
    create_s3_client,
    is_remote_path,
    remote_root_from_source_json,
)
from ..library import (
    CoverArtResolutionStats,
    GenreResolutionStats,
    load_taxonomy_genre_matcher_from_connection,
    update_genre_resolution_stats,
)
from ..discogs import (
    DISCOGS_ENTITY_MASTER,
    DISCOGS_ENTITY_RELEASE,
    DiscogsClient,
    DiscogsLookupStats,
    discogs_artist_tag_value,
    discogs_genre_style_values,
    discogs_master_id,
    discogs_title,
    get_discogs_entity,
    parse_discogs_album_url,
)
from ..metadata import (
    METADATA_ENTITY_MASTER,
    METADATA_ENTITY_RELEASE,
    METADATA_ENTITY_RELEASE_GROUP,
    METADATA_PROVIDER_DISCOGS,
    METADATA_PROVIDER_MUSICBRAINZ,
    album_metadata_link_for_album_id,
    delete_album_metadata_track_links,
    load_album_metadata_track_links,
    store_album_metadata_link,
    store_album_metadata_track_link,
)
from ...models import (
    ALBUM_ARTWORK_HEIGHT,
    TRACK_ARTWORK_HEIGHT,
    TrackArtwork,
    normalize_genre_values,
)
from ..musicbrainz import (
    MusicBrainzClient,
    MusicBrainzLookupStats,
    get_musicbrainz_entity,
    musicbrainz_genres,
    musicbrainz_release_group_mbid,
    normalize_musicbrainz_mbid,
)
from ...player_common import optional_int, placeholders_for
from ...player_runtime import (
    PlayerJobCanceled,
    PlayerJobCancelToken,
    PlayerJobResult,
    PlayerRuntime,
)
from ...scanner import (
    DOWNLOAD_CHUNK_SIZE,
    s3_user_metadata,
    write_album_audio_tags,
    write_track_audio_tags,
)
from ...text import normalize_slug_text

LOGGER = logging.getLogger("kukicha.player")

@dataclass(frozen=True, slots=True)
class AlbumTrackTagEdit:
    track_id: int
    artist: str
    track_number: str
    title: str


@dataclass(frozen=True, slots=True)
class AlbumMusicBrainzEditGroupRequest:
    provider: str
    entity_type: str
    entity_id: str
    related_entity_type: str | None = None
    related_entity_id: str | None = None
    track_ids: tuple[int, ...] = ()

    @property
    def musicbrainz_release_mbid(self) -> str | None:
        if self.provider == METADATA_PROVIDER_MUSICBRAINZ and self.entity_type == METADATA_ENTITY_RELEASE:
            return self.entity_id
        return None

    @property
    def musicbrainz_release_group_mbid(self) -> str | None:
        if self.provider != METADATA_PROVIDER_MUSICBRAINZ:
            return None
        if self.entity_type == METADATA_ENTITY_RELEASE_GROUP:
            return self.entity_id
        if self.related_entity_type == METADATA_ENTITY_RELEASE_GROUP:
            return self.related_entity_id
        return None


@dataclass(frozen=True, slots=True)
class AlbumMusicBrainzEditRequest:
    album_id: str
    album_label: str
    album_name: str
    groups: tuple[AlbumMusicBrainzEditGroupRequest, ...]

    @property
    def track_ids(self) -> tuple[int, ...]:
        return tuple(track_id for group in self.groups for track_id in group.track_ids)


@dataclass(frozen=True, slots=True)
class AlbumMusicBrainzEditGroupJob:
    request: AlbumMusicBrainzEditGroupRequest
    tracks: tuple[AlbumEditSnapshot, ...]


@dataclass(frozen=True, slots=True)
class AlbumMusicBrainzEditJob:
    request: AlbumMusicBrainzEditRequest
    groups: tuple[AlbumMusicBrainzEditGroupJob, ...]

    @property
    def tracks(self) -> tuple[AlbumEditSnapshot, ...]:
        return tuple(track for group in self.groups for track in group.tracks)


@dataclass(frozen=True, slots=True)
class AlbumMusicBrainzEditResult:
    album_label: str
    album: str
    album_artist: str
    genre: str
    tracks_updated: int
    ids_cleared: bool
    genre_resolution: GenreResolutionStats


@dataclass(frozen=True, slots=True, init=False)
class MetadataPayload:
    provider: str
    entity_type: str
    entity_id: str
    payload: dict[str, object]

    def __init__(
        self,
        *,
        entity_type: str,
        payload: dict[str, object],
        provider: str = METADATA_PROVIDER_MUSICBRAINZ,
        entity_id: str | None = None,
        mbid: str | None = None,
    ) -> None:
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "entity_type", entity_type)
        object.__setattr__(self, "entity_id", entity_id or mbid or "")
        object.__setattr__(self, "payload", payload)

    @property
    def mbid(self) -> str:
        return self.entity_id


MusicBrainzPayload = MetadataPayload


@dataclass(frozen=True, slots=True)
class AlbumMusicBrainzAudioTags:
    album: str
    album_artist: str
    genres: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AlbumTagEditRequest:
    album_id: str
    album: str
    album_artist: str
    genre: str
    tracks: tuple[AlbumTrackTagEdit, ...]


@dataclass(frozen=True, slots=True)
class AlbumEditSnapshot:
    track_id: int
    album_id: str
    root_position: int | None
    path: str
    source_kind: str
    source_json: str
    object_key: str | None
    content_type: str | None
    album: str
    title: str
    genres: tuple[str, ...]
    styles: tuple[str, ...]
    track_artwork: TrackArtwork | None
    album_artwork: TrackArtwork | None


@dataclass(frozen=True, slots=True)
class AlbumTagEditJob:
    request: AlbumTagEditRequest
    album_label: str
    album_name: str
    tracks: tuple[AlbumEditSnapshot, ...]


@dataclass(frozen=True, slots=True)
class AlbumTagEditResult:
    album_label: str
    tracks_updated: int
    albums_scanned: int
    affected_album_ids: tuple[str, ...]
    genre_resolution: GenreResolutionStats
    cover_art_resolution: CoverArtResolutionStats


@dataclass(frozen=True, slots=True)
class AlbumEditJob:
    tag_job: AlbumTagEditJob
    musicbrainz_job: AlbumMusicBrainzEditJob | None = None

    @property
    def album_label(self) -> str:
        return self.tag_job.album_label

    @property
    def album_name(self) -> str:
        return self.tag_job.album_name


@dataclass(frozen=True, slots=True)
class AlbumEditResult:
    album_label: str
    album: str
    album_artist: str
    tracks_updated: int
    musicbrainz_ids_cleared: bool
    genre_resolution: GenreResolutionStats
    cover_art_resolution: CoverArtResolutionStats


@dataclass(frozen=True, slots=True)
class BulkAlbumMetadataEditRowRequest:
    album_id: str
    track_ids: tuple[int, ...]
    metadata_url: str
    loaded_metadata_url: str = ""
    loaded_metadata_mixed: bool = False
    album_label: str = ""
    group_label: str = ""


@dataclass(frozen=True, slots=True)
class BulkAlbumMetadataEditJob:
    rows: tuple[BulkAlbumMetadataEditRowRequest, ...]


@dataclass(frozen=True, slots=True)
class BulkAlbumMetadataEditFailure:
    album_label: str
    group_label: str
    metadata_url: str
    reason: str


@dataclass(frozen=True, slots=True)
class BulkAlbumMetadataEditCurrentChange:
    album_label: str
    group_label: str
    loaded_metadata_url: str
    loaded_metadata_mixed: bool
    current_metadata_url: str
    current_metadata_mixed: bool


@dataclass(frozen=True, slots=True)
class BulkAlbumMetadataEditResult:
    rows_updated: int
    rows_cleared: int
    rows_skipped: int
    rows_failed: int
    tracks_updated: int
    failures: tuple[BulkAlbumMetadataEditFailure, ...] = ()
    changed_since_loaded: tuple[BulkAlbumMetadataEditCurrentChange, ...] = ()


def prepare_album_edit_job(
    database: Path,
    album_id: str,
    payload: dict[str, Any],
) -> AlbumEditJob:
    tag_payload = payload.get("tags")
    if not isinstance(tag_payload, dict):
        raise ValueError("tag edit payload is required")

    tag_job = prepare_album_tag_edit_job(database, album_id, tag_payload)
    musicbrainz_payload = combined_album_musicbrainz_payload(payload)
    musicbrainz_job = (
        prepare_album_musicbrainz_edit_job(database, album_id, musicbrainz_payload)
        if musicbrainz_payload is not None
        else None
    )
    return AlbumEditJob(
        tag_job=tag_job,
        musicbrainz_job=musicbrainz_job,
    )


def combined_album_musicbrainz_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    raw_musicbrainz = payload.get("metadata", payload.get("musicbrainz"))
    if raw_musicbrainz is None:
        return None
    if not isinstance(raw_musicbrainz, dict):
        raise ValueError("Metadata edit payload must be an object")

    raw_groups = raw_musicbrainz.get("groups")
    if raw_groups is not None:
        if not isinstance(raw_groups, list):
            raise ValueError("Metadata groups must be a list")
        if not raw_groups:
            return None
        return raw_musicbrainz

    return raw_musicbrainz if raw_musicbrainz else None


def prepare_bulk_album_metadata_edit_job(
    payload: dict[str, Any],
) -> BulkAlbumMetadataEditJob:
    raw_rows = payload.get("rows")
    if not isinstance(raw_rows, list) or not raw_rows:
        raise ValueError("at least one metadata URL change is required")

    rows: list[BulkAlbumMetadataEditRowRequest] = []
    seen_track_ids: set[int] = set()
    for raw_row in raw_rows:
        if not isinstance(raw_row, dict):
            raise ValueError("invalid metadata URL row")
        raw_album_id = raw_row.get("album_id")
        if not isinstance(raw_album_id, str) or not raw_album_id.strip():
            raise ValueError("album id is required")
        raw_metadata_url = raw_row.get("metadata_url", "")
        if not isinstance(raw_metadata_url, str):
            raise ValueError("Metadata URL must be a string")
        raw_loaded_metadata_url = raw_row.get("loaded_metadata_url", "")
        if not isinstance(raw_loaded_metadata_url, str):
            raise ValueError("Loaded metadata URL must be a string")
        track_ids = parse_album_musicbrainz_track_ids(raw_row.get("track_ids"))
        for track_id in track_ids:
            if track_id in seen_track_ids:
                raise ValueError(f"duplicate track id: {track_id}")
            seen_track_ids.add(track_id)
        rows.append(
            BulkAlbumMetadataEditRowRequest(
                album_id=raw_album_id.strip(),
                track_ids=track_ids,
                metadata_url=raw_metadata_url.strip(),
                loaded_metadata_url=raw_loaded_metadata_url.strip(),
                loaded_metadata_mixed=bool(raw_row.get("loaded_metadata_mixed")),
                album_label=optional_string(raw_row.get("album_label")),
                group_label=optional_string(raw_row.get("group_label")),
            )
        )

    return BulkAlbumMetadataEditJob(rows=tuple(rows))


def optional_string(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def prepare_album_musicbrainz_edit_request(
    database: Path,
    album_id: str,
    payload: dict[str, Any],
) -> AlbumMusicBrainzEditRequest:
    groups = parse_album_musicbrainz_group_requests(payload)

    connection = connect_existing_database(database)
    try:
        album_row = connection.execute(
            """
            SELECT album
            FROM library_albums
            WHERE album_id = ?
            """,
            (album_id,),
        ).fetchone()
        artist_label = album_artist_display_text(connection, album_id)
    finally:
        connection.close()

    if album_row is None:
        raise AlbumNotFoundError(album_id)

    album_name = str(album_row["album"]) if album_row["album"] else "<unknown album>"
    return AlbumMusicBrainzEditRequest(
        album_id=album_id,
        album_label=album_display_label(
            artist_label,
            album_name,
        ),
        album_name=album_name,
        groups=groups,
    )


def parse_album_musicbrainz_group_requests(
    payload: dict[str, Any],
) -> tuple[AlbumMusicBrainzEditGroupRequest, ...]:
    raw_groups = payload.get("groups")
    if raw_groups is None:
        return (parse_album_musicbrainz_group_request(payload, require_tracks=False),)

    if not isinstance(raw_groups, list) or not raw_groups:
        raise ValueError("at least one metadata group is required")

    groups: list[AlbumMusicBrainzEditGroupRequest] = []
    seen_track_ids: set[int] = set()
    for item in raw_groups:
        if not isinstance(item, dict):
            raise ValueError("invalid metadata group payload")
        group = parse_album_musicbrainz_group_request(item, require_tracks=True)
        for track_id in group.track_ids:
            if track_id in seen_track_ids:
                raise ValueError(f"duplicate track id: {track_id}")
            seen_track_ids.add(track_id)
        groups.append(group)
    return tuple(groups)


def parse_album_musicbrainz_group_request(
    payload: dict[str, Any],
    *,
    require_tracks: bool,
) -> AlbumMusicBrainzEditGroupRequest:
    raw_metadata_url = payload.get("metadata_url", payload.get("musicbrainz_url"))
    if raw_metadata_url is not None and not isinstance(raw_metadata_url, str):
        raise ValueError("Metadata URL must be a string")
    if payload.get("musicbrainz_release_mbid") or payload.get("musicbrainz_release_group_mbid"):
        raise ValueError("Use a MusicBrainz or Discogs URL instead of separate IDs")

    provider = ""
    entity_type = ""
    entity_id = ""
    if raw_metadata_url and raw_metadata_url.strip():
        provider, entity_type, entity_id = parse_album_metadata_url(raw_metadata_url)
    track_ids = parse_album_musicbrainz_track_ids(payload.get("track_ids"))
    if require_tracks and not track_ids:
        raise ValueError("at least one track is required")

    return AlbumMusicBrainzEditGroupRequest(
        provider=provider,
        entity_type=entity_type,
        entity_id=entity_id,
        track_ids=track_ids,
    )


def parse_album_metadata_url(value: str) -> tuple[str, str, str]:
    text = value.strip()
    if not text:
        return "", "", ""

    parts = urllib.parse.urlsplit(text)
    if not parts.scheme or not parts.netloc:
        raise ValueError("Expected a MusicBrainz or Discogs URL.")

    host = parts.netloc.casefold()
    if host in {"musicbrainz.org", "www.musicbrainz.org"}:
        release_mbid, release_group_mbid = parse_musicbrainz_album_url(text)
        if release_mbid:
            return METADATA_PROVIDER_MUSICBRAINZ, METADATA_ENTITY_RELEASE, release_mbid
        if release_group_mbid:
            return (
                METADATA_PROVIDER_MUSICBRAINZ,
                METADATA_ENTITY_RELEASE_GROUP,
                release_group_mbid,
            )
    if host in {"discogs.com", "www.discogs.com"}:
        reference = parse_discogs_album_url(text)
        entity_type = (
            METADATA_ENTITY_RELEASE
            if reference.entity_type == DISCOGS_ENTITY_RELEASE
            else METADATA_ENTITY_MASTER
        )
        return METADATA_PROVIDER_DISCOGS, entity_type, reference.entity_id

    raise ValueError("Expected a MusicBrainz or Discogs URL.")


def parse_musicbrainz_album_url(value: str) -> tuple[str | None, str | None]:
    text = value.strip()
    if not text:
        return None, None

    parts = urllib.parse.urlsplit(text)
    if not parts.scheme or not parts.netloc:
        raise ValueError("Expected a MusicBrainz release or release group URL.")
    if parts.netloc.casefold() not in {"musicbrainz.org", "www.musicbrainz.org"}:
        raise ValueError("Expected a MusicBrainz release or release group URL.")
    path_parts = [part for part in parts.path.split("/") if part]
    if len(path_parts) < 2:
        raise ValueError("Expected a MusicBrainz release or release group URL.")

    entity_type = path_parts[0]
    if entity_type == "release":
        return normalize_musicbrainz_mbid(text, entity_type="release"), None
    if entity_type == "release-group":
        return None, normalize_musicbrainz_mbid(text, entity_type="release-group")
    raise ValueError("Expected a MusicBrainz release or release group URL.")


def parse_album_musicbrainz_track_ids(value: object) -> tuple[int, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not value:
        raise ValueError("at least one track is required")

    seen_track_ids: set[int] = set()
    track_ids: list[int] = []
    for item in value:
        track_id = optional_int(item)
        if track_id is None or track_id < 1:
            raise ValueError("invalid track id")
        if track_id in seen_track_ids:
            raise ValueError(f"duplicate track id: {track_id}")
        seen_track_ids.add(track_id)
        track_ids.append(track_id)
    return tuple(track_ids)


def prepare_album_musicbrainz_edit_job(
    database: Path,
    album_id: str,
    payload: dict[str, Any],
) -> AlbumMusicBrainzEditJob:
    request = prepare_album_musicbrainz_edit_request(database, album_id, payload)
    connection = connect_existing_database(database)
    try:
        requested_track_ids = list(request.track_ids)
        if not requested_track_ids and len(request.groups) == 1:
            ordered_track_rows = list(
                connection.execute(
                    """
                    SELECT
                        tracks.track_id,
                        tracks.album_id,
                        tracks.root_position,
                        tracks.path,
                        tracks.album,
                        tracks.title,
                        COALESCE(sources.source_kind, roots.kind, 'local') AS source_kind,
                        COALESCE(roots.source_json, '{}') AS source_json,
                        sources.object_key,
                        sources.content_type
                    FROM library_tracks AS tracks
                    LEFT JOIN library_track_sources AS sources
                        ON sources.track_id = tracks.track_id
                    LEFT JOIN library_roots AS roots
                        ON roots.position = tracks.root_position
                    WHERE tracks.album_id = ?
                    ORDER BY tracks.track_id
                    """,
                    (album_id,),
                )
            )
            if not ordered_track_rows:
                raise AlbumNotFoundError(album_id)
            rows_by_id = {
                int(row["track_id"]): row
                for row in ordered_track_rows
            }
            request_groups = (
                AlbumMusicBrainzEditGroupRequest(
                    provider=request.groups[0].provider,
                    entity_type=request.groups[0].entity_type,
                    entity_id=request.groups[0].entity_id,
                    related_entity_type=request.groups[0].related_entity_type,
                    related_entity_id=request.groups[0].related_entity_id,
                    track_ids=tuple(rows_by_id),
                ),
            )
        elif requested_track_ids:
            placeholders = placeholders_for(requested_track_ids)
            track_rows = list(
                connection.execute(
                    f"""
                    SELECT
                        tracks.track_id,
                        tracks.album_id,
                        tracks.root_position,
                        tracks.path,
                        tracks.album,
                        tracks.title,
                        COALESCE(sources.source_kind, roots.kind, 'local') AS source_kind,
                        COALESCE(roots.source_json, '{{}}') AS source_json,
                        sources.object_key,
                        sources.content_type
                    FROM library_tracks AS tracks
                    LEFT JOIN library_track_sources AS sources
                        ON sources.track_id = tracks.track_id
                    LEFT JOIN library_roots AS roots
                        ON roots.position = tracks.root_position
                    WHERE tracks.track_id IN ({placeholders})
                    ORDER BY tracks.track_id
                    """,
                    requested_track_ids,
                )
            )
            rows_by_id = {
                int(row["track_id"]): row
                for row in track_rows
            }
            missing_track_ids = [
                track_id
                for track_id in requested_track_ids
                if track_id not in rows_by_id
            ]
            if missing_track_ids:
                raise TrackNotFoundError(missing_track_ids[0])
            request_groups = request.groups
        else:
            raise ValueError("at least one track is required")

        groups: list[AlbumMusicBrainzEditGroupJob] = []
        for group_request in request_groups:
            snapshots: list[AlbumEditSnapshot] = []
            for track_id in group_request.track_ids:
                row = rows_by_id[track_id]
                row_album_id = str(row["album_id"]) if row["album_id"] else ""
                if row_album_id != album_id:
                    raise ValueError(f"track does not belong to album: {track_id}")
                snapshots.append(album_edit_snapshot_from_row(row, row_album_id=row_album_id))
            groups.append(
                AlbumMusicBrainzEditGroupJob(
                    request=group_request,
                    tracks=tuple(snapshots),
                )
            )
    finally:
        connection.close()

    return AlbumMusicBrainzEditJob(
        request=request,
        groups=tuple(groups),
    )


def album_edit_snapshot_from_row(
    row: sqlite3.Row,
    *,
    row_album_id: str,
) -> AlbumEditSnapshot:
    return AlbumEditSnapshot(
        track_id=int(row["track_id"]),
        album_id=row_album_id,
        root_position=int(row["root_position"]) if row["root_position"] is not None else None,
        path=str(row["path"]),
        source_kind=row_text(row, "source_kind", default=SOURCE_KIND_LOCAL),
        source_json=row_text(row, "source_json", default="{}"),
        object_key=row_optional_text(row, "object_key"),
        content_type=row_optional_text(row, "content_type"),
        album=str(row["album"]) if row["album"] else "",
        title=str(row["title"]) if row["title"] else "",
        genres=(),
        styles=(),
        track_artwork=None,
        album_artwork=None,
    )


def row_text(row: sqlite3.Row, name: str, *, default: str = "") -> str:
    try:
        value = row[name]
    except (IndexError, KeyError):
        return default
    return str(value) if value is not None else default


def row_optional_text(row: sqlite3.Row, name: str) -> str | None:
    value = row_text(row, name)
    return value if value else None


def parse_album_tag_edit_request(
    album_id: str,
    payload: dict[str, Any],
) -> AlbumTagEditRequest:
    album = str(payload.get("album") or "").strip()
    album_artist = str(payload.get("album_artist") or "").strip()
    genre = str(payload.get("genre") or "").strip()
    raw_tracks = payload.get("tracks")
    if not isinstance(raw_tracks, list) or not raw_tracks:
        raise ValueError("at least one track is required")

    seen_track_ids: set[int] = set()
    tracks: list[AlbumTrackTagEdit] = []
    for item in raw_tracks:
        if not isinstance(item, dict):
            raise ValueError("invalid track edit payload")
        track_id = optional_int(item.get("track_id"))
        if track_id is None or track_id < 1:
            raise ValueError("invalid track id")
        if track_id in seen_track_ids:
            raise ValueError(f"duplicate track id: {track_id}")
        seen_track_ids.add(track_id)
        tracks.append(
            AlbumTrackTagEdit(
                track_id=track_id,
                artist=str(item.get("artist") or "").strip(),
                track_number=str(item.get("track_number") or "").strip(),
                title=str(item.get("title") or "").strip(),
            )
        )
    return AlbumTagEditRequest(
        album_id=album_id,
        album=album,
        album_artist=album_artist,
        genre=genre,
        tracks=tuple(tracks),
    )


def album_display_label(artist: str | None, album: str | None) -> str:
    artist_text = artist.strip() if artist else ""
    album_text = album.strip() if album else ""
    resolved_artist = artist_text or "<unknown artist>"
    resolved_album = display_album_title(album_text or "<unknown album>")
    return f"{resolved_artist} - {resolved_album}"


def prepare_album_tag_edit_job(
    database: Path,
    album_id: str,
    payload: dict[str, Any],
) -> AlbumTagEditJob:
    request = parse_album_tag_edit_request(album_id, payload)
    requested_track_ids = [item.track_id for item in request.tracks]
    connection = connect_existing_database(database)
    try:
        album_row = connection.execute(
            """
            SELECT album
            FROM library_albums
            WHERE album_id = ?
            """,
            (album_id,),
        ).fetchone()
        if album_row is None:
            raise AlbumNotFoundError(album_id)
        artist_label = album_artist_display_text(connection, album_id)

        placeholders = placeholders_for(requested_track_ids)
        track_rows = list(
            connection.execute(
                f"""
                SELECT
                    tracks.track_id,
                    tracks.album_id,
                    tracks.root_position,
                    tracks.path,
                    tracks.album,
                    tracks.title,
                    COALESCE(sources.source_kind, roots.kind, 'local') AS source_kind,
                    COALESCE(roots.source_json, '{{}}') AS source_json,
                    sources.object_key,
                    sources.content_type
                FROM library_tracks AS tracks
                LEFT JOIN library_track_sources AS sources
                    ON sources.track_id = tracks.track_id
                LEFT JOIN library_roots AS roots
                    ON roots.position = tracks.root_position
                WHERE tracks.track_id IN ({placeholders})
                ORDER BY tracks.track_id
                """,
                requested_track_ids,
            )
        )
        rows_by_id = {
            int(row["track_id"]): row
            for row in track_rows
        }
        missing_track_ids = [
            track_id
            for track_id in requested_track_ids
            if track_id not in rows_by_id
        ]
        if missing_track_ids:
            raise TrackNotFoundError(missing_track_ids[0])

        genre_rows: dict[int, list[str]] = {}
        for row in connection.execute(
            f"""
            SELECT track_id, genre
            FROM library_track_genres
            WHERE track_id IN ({placeholders})
            ORDER BY track_id, position
            """,
            requested_track_ids,
        ):
            genre_rows.setdefault(int(row["track_id"]), []).append(str(row["genre"]))

        style_rows: dict[int, list[str]] = {}
        for row in connection.execute(
            f"""
            SELECT track_id, style
            FROM library_track_styles
            WHERE track_id IN ({placeholders})
            ORDER BY track_id, position
            """,
            requested_track_ids,
        ):
            style_rows.setdefault(int(row["track_id"]), []).append(str(row["style"]))

        artwork_rows: dict[int, dict[int, TrackArtwork]] = {}
        for row in connection.execute(
            f"""
            SELECT track_id, height_px, mime_type, data
            FROM library_track_artwork
            WHERE track_id IN ({placeholders})
            """,
            requested_track_ids,
        ):
            artwork_rows.setdefault(int(row["track_id"]), {})[int(row["height_px"])] = TrackArtwork(
                mime_type=str(row["mime_type"]),
                data=bytes(row["data"]),
            )

        snapshots: list[AlbumEditSnapshot] = []
        for track_edit in request.tracks:
            row = rows_by_id[track_edit.track_id]
            row_album_id = str(row["album_id"]) if row["album_id"] else ""
            if row_album_id != album_id:
                raise ValueError(f"track does not belong to album: {track_edit.track_id}")
            track_artworks = artwork_rows.get(track_edit.track_id, {})
            snapshots.append(
                AlbumEditSnapshot(
                    track_id=track_edit.track_id,
                    album_id=row_album_id,
                    root_position=(
                        int(row["root_position"])
                        if row["root_position"] is not None
                        else None
                    ),
                    path=str(row["path"]),
                    source_kind=row_text(row, "source_kind", default=SOURCE_KIND_LOCAL),
                    source_json=row_text(row, "source_json", default="{}"),
                    object_key=row_optional_text(row, "object_key"),
                    content_type=row_optional_text(row, "content_type"),
                    album=str(row["album"]) if row["album"] else "",
                    title=str(row["title"]) if row["title"] else "",
                    genres=tuple(genre_rows.get(track_edit.track_id, ())),
                    styles=tuple(style_rows.get(track_edit.track_id, ())),
                    track_artwork=track_artworks.get(TRACK_ARTWORK_HEIGHT),
                    album_artwork=track_artworks.get(ALBUM_ARTWORK_HEIGHT),
                )
            )
    finally:
        connection.close()

    if not request.album:
        raise ValueError("album title is required")

    for track_edit, snapshot in zip(request.tracks, snapshots, strict=True):
        title = snapshot.title or Path(snapshot.path).name
        if not track_edit.title:
            raise ValueError(f"track requires title: {title}")
        if track_edit.artist or request.album_artist:
            continue
        raise ValueError(f"track requires artist or album artist: {title}")

    album_name = str(album_row["album"]) if album_row and album_row["album"] else "<unknown album>"
    return AlbumTagEditJob(
        request=request,
        album_label=album_display_label(
            artist_label,
            album_name,
        ),
        album_name=album_name,
        tracks=tuple(snapshots),
    )


def album_artist_display_text(
    connection: sqlite3.Connection,
    album_id: str,
) -> str | None:
    artists = [
        str(row["artist"])
        for row in connection.execute(
            """
            SELECT artist
            FROM library_album_artists
            WHERE album_id = ?
            ORDER BY position
            """,
            (album_id,),
        )
        if row["artist"]
    ]
    return display_album_artists(artists) or None


def snapshot_is_remote_track(snapshot: AlbumEditSnapshot) -> bool:
    return snapshot.source_kind == SOURCE_KIND_S3 or is_remote_path(snapshot.path)


def write_album_edit_track_tags(
    snapshot: AlbumEditSnapshot,
    *,
    artist: str | None,
    album_artist: str | None,
    album: str | None,
    track_number: str | None,
    title: str | None,
    genre: str | None,
) -> None:
    def write(path: Path) -> None:
        write_track_audio_tags(
            path,
            artist=artist,
            album_artist=album_artist,
            album=album,
            track_number=track_number,
            title=title,
            genre=genre,
        )

    write_album_edit_audio(snapshot, write)


def write_album_edit_album_tags(
    snapshot: AlbumEditSnapshot,
    *,
    album_artist: str,
    album: str,
    genre: str,
) -> None:
    def write(path: Path) -> None:
        write_album_audio_tags(
            path,
            album_artist=album_artist,
            album=album,
            genre=genre,
        )

    write_album_edit_audio(snapshot, write)


def write_album_edit_audio(
    snapshot: AlbumEditSnapshot,
    write: Callable[[Path], None],
) -> None:
    if not snapshot_is_remote_track(snapshot):
        write(Path(snapshot.path))
        return

    remote, object_key = remote_album_edit_target(snapshot)
    client = create_s3_client(remote)
    with TemporaryDirectory(prefix="kukicha-remote-tag-edit-") as tempdir:
        temp_path = Path(tempdir) / remote_album_edit_temp_name(snapshot, object_key)
        response = client.get_object(Bucket=remote.bucket, Key=object_key)
        if not isinstance(response, dict):
            raise OSError(f"failed to download S3 object: {object_key}")
        metadata = s3_user_metadata(response)
        content_type = remote_album_edit_content_type(snapshot, object_key)
        body = response.get("Body")
        if body is None or not hasattr(body, "read"):
            raise OSError(f"failed to download S3 object: {object_key}")
        try:
            with temp_path.open("wb") as handle:
                while True:
                    chunk = body.read(DOWNLOAD_CHUNK_SIZE)
                    if not chunk:
                        break
                    handle.write(chunk)
        finally:
            close = getattr(body, "close", None)
            if callable(close):
                close()

        write(temp_path)
        with temp_path.open("rb") as body_handle:
            client.put_object(
                Bucket=remote.bucket,
                Key=object_key,
                Body=body_handle,
                ContentType=content_type,
                Metadata=metadata,
            )


def remote_album_edit_target(snapshot: AlbumEditSnapshot) -> tuple[Any, str]:
    object_key = snapshot.object_key.strip() if snapshot.object_key else ""
    if not object_key:
        raise ValueError("remote audio edit requires S3 object key metadata")
    try:
        remote = remote_root_from_source_json(snapshot.source_json)
    except Exception as error:
        raise ValueError("remote audio edit requires valid S3 root metadata") from error
    return remote, object_key


def remote_album_edit_temp_name(
    snapshot: AlbumEditSnapshot,
    object_key: str,
) -> str:
    name = Path(object_key).name or Path(snapshot.path).name
    return name or "audio"


def remote_album_edit_content_type(
    snapshot: AlbumEditSnapshot,
    object_key: str,
) -> str:
    name = Path(object_key).name or Path(snapshot.path).name
    content_type = content_type_for_name(name)
    if content_type != "application/octet-stream":
        return content_type
    return snapshot.content_type or content_type


def edit_library_album_musicbrainz(
    database: Path,
    job: AlbumMusicBrainzEditJob,
    *,
    album_artist_split_patterns: Iterable[str | None] = DEFAULT_ALBUM_ARTIST_SPLIT_PATTERNS,
    prefer_musicbrainz_english_aliases: bool = True,
    cancel_check: Callable[[], None] | None = None,
) -> AlbumMusicBrainzEditResult:
    connection = connect_existing_database(database)
    try:
        connection.execute("SAVEPOINT edit_album_musicbrainz")
        try:
            if cancel_check is not None:
                cancel_check()

            delete_album_metadata_links_for_job(connection, job)
            delete_album_metadata_track_links(
                connection,
                (snapshot.path for snapshot in job.tracks),
            )

            pending_groups = [
                group
                for group in job.groups
                if group.request.provider and group.request.entity_id
            ]
            if not pending_groups:
                connection.execute("RELEASE SAVEPOINT edit_album_musicbrainz")
                connection.commit()
                return AlbumMusicBrainzEditResult(
                    album_label=job.request.album_label,
                    album="",
                    album_artist="",
                    genre="",
                    tracks_updated=0,
                    ids_cleared=True,
                    genre_resolution=GenreResolutionStats(),
                )

            total_tracks_updated = 0
            combined_genre_resolution = GenreResolutionStats()
            first_tag_values: AlbumMusicBrainzAudioTags | None = None
            first_genre_text = ""
            for group in pending_groups:
                if cancel_check is not None:
                    cancel_check()

                lookup_stats = (
                    DiscogsLookupStats()
                    if group.request.provider == METADATA_PROVIDER_DISCOGS
                    else MusicBrainzLookupStats()
                )
                payloads, related_entity_type, related_entity_id = load_album_metadata_edit_payloads(
                    connection,
                    group.request,
                    lookup_stats,
                )
                tag_values, genre_resolution = album_musicbrainz_audio_tags(
                    connection,
                    payloads,
                    prefer_musicbrainz_english_aliases=prefer_musicbrainz_english_aliases,
                )
                genre_resolution.musicbrainz_api_calls = lookup_stats.api_calls
                genre_resolution.musicbrainz_cached_calls = lookup_stats.cached_calls
                genre_resolution.musicbrainz_rate_limit_retries = lookup_stats.rate_limit_retries
                genre_resolution.musicbrainz_fetch_failures = lookup_stats.fetch_failures
                add_genre_resolution_stats(combined_genre_resolution, genre_resolution)

                genre_text = "; ".join(tag_values.genres)
                if first_tag_values is None:
                    first_tag_values = tag_values
                    first_genre_text = genre_text
                for snapshot in group.tracks:
                    if cancel_check is not None:
                        cancel_check()
                    write_album_edit_album_tags(
                        snapshot,
                        album_artist=tag_values.album_artist,
                        album=tag_values.album,
                        genre=genre_text,
                    )
                    total_tracks_updated += 1

                target_file_album_id = musicbrainz_audio_tag_file_album_id(
                    connection,
                    tag_values,
                    split_patterns=album_artist_split_patterns,
                )
                store_album_metadata_link(
                    connection,
                    target_file_album_id,
                    provider=group.request.provider,
                    entity_type=group.request.entity_type,
                    entity_id=group.request.entity_id,
                    related_entity_type=related_entity_type,
                    related_entity_id=related_entity_id,
                )
                for snapshot in group.tracks:
                    store_album_metadata_track_link(
                        connection,
                        snapshot.path,
                        target_file_album_id,
                        provider=group.request.provider,
                        entity_type=group.request.entity_type,
                        entity_id=group.request.entity_id,
                        related_entity_type=related_entity_type,
                        related_entity_id=related_entity_id,
                    )
            if cancel_check is not None:
                cancel_check()
            connection.execute("RELEASE SAVEPOINT edit_album_musicbrainz")
            connection.commit()
        except Exception:
            connection.execute("ROLLBACK TO SAVEPOINT edit_album_musicbrainz")
            connection.execute("RELEASE SAVEPOINT edit_album_musicbrainz")
            connection.rollback()
            raise
    finally:
        connection.close()

    return AlbumMusicBrainzEditResult(
        album_label=job.request.album_label,
        album=first_tag_values.album if first_tag_values is not None else job.request.album_name,
        album_artist=first_tag_values.album_artist if first_tag_values is not None else "",
        genre=first_genre_text,
        tracks_updated=total_tracks_updated,
        ids_cleared=False,
        genre_resolution=combined_genre_resolution,
    )


def delete_album_metadata_links_for_job(
    connection: sqlite3.Connection,
    job: AlbumMusicBrainzEditJob,
) -> None:
    request_file_album_id = file_album_id_from_album_id(job.request.album_id)
    existing_track_links = load_album_metadata_track_links(
        connection,
        (snapshot.path for snapshot in job.tracks),
    )

    link_keys = {
        (
            link.file_album_id,
            link.provider,
            link.entity_type,
            link.entity_id,
            link.related_entity_type or "",
            link.related_entity_id or "",
        )
        for link in existing_track_links.values()
        if link.file_album_id and link.provider and link.entity_id
    }
    if not link_keys:
        current_link = album_metadata_link_for_album_id(
            connection,
            job.request.album_id,
        )
        if current_link is not None:
            link_keys.add(
                (
                    current_link.file_album_id,
                    current_link.provider,
                    current_link.entity_type,
                    current_link.entity_id,
                    current_link.related_entity_type or "",
                    current_link.related_entity_id or "",
                )
            )

    if request_file_album_id == job.request.album_id and not link_keys:
        connection.execute(
            "DELETE FROM album_metadata_links WHERE file_album_id = ?",
            (request_file_album_id,),
        )
        return

    if request_file_album_id != job.request.album_id:
        connection.execute(
            "DELETE FROM album_metadata_links WHERE file_album_id = ?",
            (job.request.album_id,),
        )

    for file_album_id, provider, entity_type, entity_id, related_entity_type, related_entity_id in link_keys:
        connection.execute(
            """
            DELETE FROM album_metadata_links
            WHERE file_album_id = ?
                AND provider = ?
                AND entity_type = ?
                AND entity_id = ?
                AND COALESCE(related_entity_type, '') = ?
                AND COALESCE(related_entity_id, '') = ?
            """,
            (
                file_album_id,
                provider,
                entity_type,
                entity_id,
                related_entity_type,
                related_entity_id,
            ),
        )


def load_album_metadata_edit_payloads(
    connection: sqlite3.Connection,
    request: AlbumMusicBrainzEditGroupRequest,
    stats: MusicBrainzLookupStats | DiscogsLookupStats,
) -> tuple[tuple[MusicBrainzPayload, ...], str | None, str | None]:
    if request.provider == METADATA_PROVIDER_DISCOGS:
        return load_album_discogs_edit_payloads(connection, request, stats)
    return load_album_musicbrainz_edit_payloads(connection, request, stats)


def load_album_musicbrainz_edit_payloads(
    connection: sqlite3.Connection,
    request: AlbumMusicBrainzEditGroupRequest,
    stats: MusicBrainzLookupStats | DiscogsLookupStats,
) -> tuple[tuple[MusicBrainzPayload, ...], str | None, str | None]:
    client = MusicBrainzClient(stats=stats)
    payloads: list[MusicBrainzPayload] = []
    release_group_mbid = request.musicbrainz_release_group_mbid

    if request.musicbrainz_release_mbid:
        release_payload = get_musicbrainz_entity(
            connection,
            client,
            entity_type="release",
            mbid=request.musicbrainz_release_mbid,
        )
        if release_payload is not None:
            payloads.append(
                MusicBrainzPayload(
                    provider=METADATA_PROVIDER_MUSICBRAINZ,
                    entity_type="release",
                    entity_id=request.musicbrainz_release_mbid,
                    payload=release_payload,
                )
            )
            if release_group_mbid is None:
                release_group_mbid = musicbrainz_release_group_mbid(release_payload)

    if release_group_mbid:
        release_group_payload = get_musicbrainz_entity(
            connection,
            client,
            entity_type="release-group",
            mbid=release_group_mbid,
        )
        if release_group_payload is not None:
            payloads.append(
                MusicBrainzPayload(
                    provider=METADATA_PROVIDER_MUSICBRAINZ,
                    entity_type="release-group",
                    entity_id=release_group_mbid,
                    payload=release_group_payload,
                )
            )

    if not payloads:
        raise ValueError("No MusicBrainz data available for the saved URL.")

    related_entity_type = (
        METADATA_ENTITY_RELEASE_GROUP
        if request.entity_type == METADATA_ENTITY_RELEASE and release_group_mbid
        else None
    )
    related_entity_id = release_group_mbid if related_entity_type else None
    return tuple(payloads), related_entity_type, related_entity_id


def load_album_discogs_edit_payloads(
    connection: sqlite3.Connection,
    request: AlbumMusicBrainzEditGroupRequest,
    stats: MusicBrainzLookupStats | DiscogsLookupStats,
) -> tuple[tuple[MusicBrainzPayload, ...], str | None, str | None]:
    client = DiscogsClient(stats=stats)
    payloads: list[MusicBrainzPayload] = []
    master_id = request.related_entity_id if request.related_entity_type == METADATA_ENTITY_MASTER else None

    if request.entity_type == METADATA_ENTITY_RELEASE:
        release_payload = get_discogs_entity(
            connection,
            client,
            entity_type=DISCOGS_ENTITY_RELEASE,
            entity_id=request.entity_id,
        )
        if release_payload is not None:
            payloads.append(
                MusicBrainzPayload(
                    provider=METADATA_PROVIDER_DISCOGS,
                    entity_type=METADATA_ENTITY_RELEASE,
                    entity_id=request.entity_id,
                    payload=release_payload,
                )
            )
            if master_id is None:
                master_id = discogs_master_id(release_payload)

    if request.entity_type == METADATA_ENTITY_MASTER:
        master_id = request.entity_id

    if master_id:
        master_payload = get_discogs_entity(
            connection,
            client,
            entity_type=DISCOGS_ENTITY_MASTER,
            entity_id=master_id,
        )
        if master_payload is not None:
            payloads.append(
                MusicBrainzPayload(
                    provider=METADATA_PROVIDER_DISCOGS,
                    entity_type=METADATA_ENTITY_MASTER,
                    entity_id=master_id,
                    payload=master_payload,
                )
            )

    if not payloads:
        raise ValueError("No Discogs data available for the saved URL.")

    related_entity_type = (
        METADATA_ENTITY_MASTER
        if request.entity_type == METADATA_ENTITY_RELEASE and master_id
        else None
    )
    related_entity_id = master_id if related_entity_type else None
    return tuple(payloads), related_entity_type, related_entity_id


def add_genre_resolution_stats(
    target: GenreResolutionStats,
    source: GenreResolutionStats,
) -> None:
    for field in fields(GenreResolutionStats):
        setattr(
            target,
            field.name,
            int(getattr(target, field.name)) + int(getattr(source, field.name)),
        )


def album_musicbrainz_audio_tags(
    connection: sqlite3.Connection,
    payloads: tuple[MusicBrainzPayload, ...],
    *,
    prefer_musicbrainz_english_aliases: bool = True,
) -> tuple[AlbumMusicBrainzAudioTags, GenreResolutionStats]:
    tag_payload = preferred_metadata_tag_payload(payloads)
    genres, stats = metadata_audio_genre_values(connection, payloads)
    return (
        AlbumMusicBrainzAudioTags(
            album=metadata_album_tag_title(tag_payload),
            album_artist=metadata_album_artist_tag_value(
                tag_payload,
                prefer_english_aliases=prefer_musicbrainz_english_aliases,
            ),
            genres=genres,
        ),
        stats,
    )


def preferred_metadata_tag_payload(
    payloads: tuple[MusicBrainzPayload, ...],
) -> MusicBrainzPayload:
    for payload in payloads:
        if payload.entity_type == "release":
            return payload
    return payloads[0]


def metadata_album_tag_title(payload: MusicBrainzPayload) -> str:
    if payload.provider == METADATA_PROVIDER_DISCOGS:
        return discogs_title(payload.payload, entity_type=payload.entity_type)
    return musicbrainz_album_tag_title(payload)


def musicbrainz_album_tag_title(payload: MusicBrainzPayload) -> str:
    title = payload.payload.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError(f"MusicBrainz {payload.entity_type} payload is missing a title.")
    return title.strip()


def metadata_album_artist_tag_value(
    payload: MusicBrainzPayload,
    *,
    prefer_english_aliases: bool = True,
) -> str:
    if payload.provider == METADATA_PROVIDER_DISCOGS:
        return discogs_artist_tag_value(payload.payload, entity_type=payload.entity_type)
    return musicbrainz_album_artist_tag_value(
        payload,
        prefer_english_aliases=prefer_english_aliases,
    )


def musicbrainz_album_artist_tag_value(
    payload: MusicBrainzPayload,
    *,
    prefer_english_aliases: bool = True,
) -> str:
    artist_credit = payload.payload.get("artist-credit")
    if not isinstance(artist_credit, list):
        raise ValueError(
            f"MusicBrainz {payload.entity_type} payload is missing artist credit."
        )

    parts: list[str] = []
    for item in artist_credit:
        if not isinstance(item, dict):
            continue
        name = musicbrainz_artist_credit_name(
            item,
            prefer_english_aliases=prefer_english_aliases,
        )
        if not name:
            continue
        joinphrase = item.get("joinphrase")
        parts.append(name + (joinphrase if isinstance(joinphrase, str) else ""))

    artist = "".join(parts).strip()
    if not artist:
        raise ValueError(
            f"MusicBrainz {payload.entity_type} payload is missing artist credit."
        )
    return artist


def musicbrainz_artist_credit_name(
    item: dict[object, object],
    *,
    prefer_english_aliases: bool,
) -> str:
    if prefer_english_aliases:
        alias = first_musicbrainz_artist_alias(item, locale="en")
        if alias:
            return alias

    name = item.get("name")
    return name.strip() if isinstance(name, str) else ""


def first_musicbrainz_artist_alias(
    item: dict[object, object],
    *,
    locale: str,
) -> str:
    artist = item.get("artist")
    if not isinstance(artist, dict):
        return ""
    aliases = artist.get("aliases")
    if not isinstance(aliases, list):
        return ""

    requested_locale = locale.casefold()
    for alias in aliases:
        if not isinstance(alias, dict):
            continue
        alias_locale = alias.get("locale")
        if not isinstance(alias_locale, str):
            continue
        if alias_locale.casefold() != requested_locale:
            continue
        name = alias.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return ""


def metadata_audio_genre_values(
    connection: sqlite3.Connection,
    payloads: tuple[MusicBrainzPayload, ...],
) -> tuple[tuple[str, ...], GenreResolutionStats]:
    matcher = load_taxonomy_genre_matcher_from_connection(connection)
    if not matcher.candidates:
        raise ValueError("No taxonomy genres are available for metadata genre matching.")

    source_genres: dict[str, tuple[str, str, str]] = {}
    for payload in payloads:
        for genre in metadata_source_genres(payload):
            source_genres.setdefault(
                genre.casefold(),
                (genre, payload.entity_type, payload.entity_id),
            )

    if not source_genres:
        raise ValueError("Metadata provider returned no genres for the saved URL.")

    stats = GenreResolutionStats(musicbrainz_album_overrides=1)
    resolved_genres: list[str] = []
    resolved_styles: list[str] = []
    for genre, _entity_type, _mbid in source_genres.values():
        match = matcher.resolve(genre)
        update_genre_resolution_stats(stats, match.resolution)
        if match.resolution == "unmatched":
            stats.musicbrainz_unmatched_genres += 1
            continue
        resolved_genres.extend(match.genres)
        resolved_styles.extend(match.styles)

    genre_values = tuple(normalize_genre_values((*resolved_genres, *resolved_styles)))
    if not genre_values:
        raise ValueError("No metadata genres matched the taxonomy for the saved URL.")
    return genre_values, stats


def metadata_source_genres(payload: MusicBrainzPayload) -> tuple[str, ...]:
    if payload.provider == METADATA_PROVIDER_DISCOGS:
        return discogs_genre_style_values(payload.payload)
    return tuple(musicbrainz_genres(payload.payload))


def musicbrainz_audio_tag_file_album_id(
    connection: sqlite3.Connection,
    tag_values: AlbumMusicBrainzAudioTags,
    *,
    split_patterns: Iterable[str | None],
) -> str:
    album_artist = tag_values.album_artist.strip()
    album = tag_values.album.strip()
    if not album_artist or not album:
        raise ValueError("MusicBrainz album and album artist are required.")

    artists = musicbrainz_audio_tag_album_artists(
        connection,
        album_artist,
        split_patterns=split_patterns,
    )
    artist_id = album_artist_id_text(artists) or album_artist
    artist_slug = normalize_slug_text(artist_id)
    album_slug = normalize_slug_text(album)
    if not artist_slug or not album_slug:
        raise ValueError("MusicBrainz album and album artist are required.")
    return local_album_id(artist_id, album)


def musicbrainz_audio_tag_album_artists(
    connection: sqlite3.Connection,
    album_artist: str,
    *,
    split_patterns: Iterable[str | None],
) -> tuple[str, ...]:
    row = connection.execute(
        """
        SELECT mapped_artists
        FROM album_artist_split_mappings
        WHERE album_artist = ?
        """,
        (album_artist,),
    ).fetchone()
    if row is not None:
        return mapped_album_artists_from_text(row["mapped_artists"]) or (album_artist,)
    if album_artist_has_mapping_pattern(album_artist, split_patterns):
        return default_album_artist_mapping(album_artist) or (album_artist,)
    return (album_artist,)


def edit_library_album_tags(
    database: Path,
    job: AlbumTagEditJob,
    *,
    album_artist_split_patterns: Iterable[str | None] = DEFAULT_ALBUM_ARTIST_SPLIT_PATTERNS,
    cancel_check: Callable[[], None] | None = None,
) -> AlbumTagEditResult:
    del database, album_artist_split_patterns
    snapshots_by_track_id = {
        snapshot.track_id: snapshot
        for snapshot in job.tracks
    }

    for track_edit in job.request.tracks:
        if cancel_check is not None:
            cancel_check()
        snapshot = snapshots_by_track_id[track_edit.track_id]
        write_album_edit_track_tags(
            snapshot,
            artist=track_edit.artist,
            album_artist=job.request.album_artist,
            album=job.request.album,
            track_number=track_edit.track_number,
            title=track_edit.title,
            genre=job.request.genre,
        )

    return AlbumTagEditResult(
        album_label=job.album_label,
        tracks_updated=len(job.request.tracks),
        albums_scanned=0,
        affected_album_ids=(),
        genre_resolution=GenreResolutionStats(),
        cover_art_resolution=CoverArtResolutionStats(),
    )


def edit_library_album_edit(
    database: Path,
    job: AlbumEditJob,
    *,
    album_artist_split_patterns: Iterable[str | None] = DEFAULT_ALBUM_ARTIST_SPLIT_PATTERNS,
    prefer_musicbrainz_english_aliases: bool = True,
    cancel_check: Callable[[], None] | None = None,
) -> AlbumEditResult:
    tag_result = edit_library_album_tags(
        database,
        job.tag_job,
        album_artist_split_patterns=album_artist_split_patterns,
        cancel_check=cancel_check,
    )
    musicbrainz_result: AlbumMusicBrainzEditResult | None = None
    if job.musicbrainz_job is not None:
        if cancel_check is not None:
            cancel_check()
        musicbrainz_result = edit_library_album_musicbrainz(
            database,
            job.musicbrainz_job,
            album_artist_split_patterns=album_artist_split_patterns,
            prefer_musicbrainz_english_aliases=prefer_musicbrainz_english_aliases,
            cancel_check=cancel_check,
        )

    musicbrainz_wrote_tags = (
        musicbrainz_result is not None
        and not musicbrainz_result.ids_cleared
    )
    return AlbumEditResult(
        album_label=job.album_label,
        album=(
            musicbrainz_result.album
            if musicbrainz_wrote_tags and musicbrainz_result is not None
            else job.tag_job.request.album
        ),
        album_artist=(
            musicbrainz_result.album_artist
            if musicbrainz_wrote_tags and musicbrainz_result is not None
            else job.tag_job.request.album_artist
        ),
        tracks_updated=max(
            tag_result.tracks_updated,
            musicbrainz_result.tracks_updated if musicbrainz_result is not None else 0,
        ),
        musicbrainz_ids_cleared=(
            musicbrainz_result.ids_cleared if musicbrainz_result is not None else False
        ),
        genre_resolution=(
            musicbrainz_result.genre_resolution
            if musicbrainz_result is not None
            else tag_result.genre_resolution
        ),
        cover_art_resolution=tag_result.cover_art_resolution,
    )


def edit_library_bulk_album_metadata_urls(
    database: Path,
    job: BulkAlbumMetadataEditJob,
    *,
    album_artist_split_patterns: Iterable[str | None] = DEFAULT_ALBUM_ARTIST_SPLIT_PATTERNS,
    prefer_musicbrainz_english_aliases: bool = True,
    cancel_check: Callable[[], None] | None = None,
) -> BulkAlbumMetadataEditResult:
    rows_updated = 0
    rows_cleared = 0
    rows_skipped = 0
    rows_failed = 0
    tracks_updated = 0
    failures: list[BulkAlbumMetadataEditFailure] = []
    changed_since_loaded: list[BulkAlbumMetadataEditCurrentChange] = []

    for row in job.rows:
        try:
            if cancel_check is not None:
                cancel_check()
            current_url, current_mixed = current_metadata_url_for_bulk_row(
                database,
                row.album_id,
                row.track_ids,
            )
            if (
                row.loaded_metadata_mixed
                or current_mixed
                or current_url != row.loaded_metadata_url
            ):
                changed_since_loaded.append(
                    BulkAlbumMetadataEditCurrentChange(
                        album_label=bulk_row_album_label(database, row),
                        group_label=row.group_label,
                        loaded_metadata_url=row.loaded_metadata_url,
                        loaded_metadata_mixed=row.loaded_metadata_mixed,
                        current_metadata_url=current_url,
                        current_metadata_mixed=current_mixed,
                    )
                )
            if not current_mixed and current_url == row.metadata_url:
                rows_skipped += 1
                continue

            row_job = prepare_album_musicbrainz_edit_job(
                database,
                row.album_id,
                {
                    "metadata_url": row.metadata_url,
                    "track_ids": list(row.track_ids),
                },
            )
            row_result = edit_library_album_musicbrainz(
                database,
                row_job,
                album_artist_split_patterns=album_artist_split_patterns,
                prefer_musicbrainz_english_aliases=prefer_musicbrainz_english_aliases,
                cancel_check=cancel_check,
            )
            if row_result.ids_cleared:
                rows_cleared += 1
            else:
                rows_updated += 1
                tracks_updated += row_result.tracks_updated
        except PlayerJobCanceled:
            raise
        except Exception as error:
            rows_failed += 1
            failures.append(
                BulkAlbumMetadataEditFailure(
                    album_label=bulk_row_album_label(database, row),
                    group_label=row.group_label,
                    metadata_url=row.metadata_url,
                    reason=brief_album_metadata_row_error(error),
                )
            )

    return BulkAlbumMetadataEditResult(
        rows_updated=rows_updated,
        rows_cleared=rows_cleared,
        rows_skipped=rows_skipped,
        rows_failed=rows_failed,
        tracks_updated=tracks_updated,
        failures=tuple(failures),
        changed_since_loaded=tuple(changed_since_loaded),
    )


def current_metadata_url_for_bulk_row(
    database: Path,
    album_id: str,
    track_ids: tuple[int, ...],
) -> tuple[str, bool]:
    connection = connect_existing_database(database)
    try:
        placeholders = placeholders_for(track_ids)
        rows = list(
            connection.execute(
                f"""
                SELECT track_id, album_id, path
                FROM library_tracks
                WHERE track_id IN ({placeholders})
                ORDER BY track_id
                """,
                track_ids,
            )
        )
        rows_by_id = {int(row["track_id"]): row for row in rows}
        missing_track_ids = [
            track_id
            for track_id in track_ids
            if track_id not in rows_by_id
        ]
        if missing_track_ids:
            raise TrackNotFoundError(missing_track_ids[0])
        for track_id in track_ids:
            row_album_id = str(rows_by_id[track_id]["album_id"] or "")
            if row_album_id != album_id:
                raise ValueError(f"track does not belong to album: {track_id}")

        paths = tuple(str(row["path"]) for row in rows)
        track_links = load_album_metadata_track_links(connection, paths)
        link_values = tuple(
            dict.fromkeys(
                (
                    link.provider,
                    link.entity_type,
                    link.entity_id,
                )
                for path in paths
                for link in (track_links.get(path),)
                if link is not None and link.provider and link.entity_id
            )
        )
        if len(link_values) == 1:
            return metadata_url_for_values(*link_values[0]), False
        if len(link_values) > 1:
            return "", True

        album_track_rows = list(
            connection.execute(
                """
                SELECT track_id
                FROM library_tracks
                WHERE album_id = ?
                """,
                (album_id,),
            )
        )
        album_track_ids = {int(row["track_id"]) for row in album_track_rows}
        if album_track_ids == set(track_ids):
            return metadata_url_for_link(
                album_metadata_link_for_album_id(connection, album_id)
            ), False
        return "", False
    finally:
        connection.close()


def metadata_url_for_link(link: object) -> str:
    if link is None:
        return ""
    return metadata_url_for_values(
        getattr(link, "provider", None),
        getattr(link, "entity_type", None),
        getattr(link, "entity_id", None),
    )


def metadata_url_for_values(
    provider: object,
    entity_type: object,
    entity_id: object,
) -> str:
    provider_text = str(provider or "")
    entity_type_text = str(entity_type or "")
    entity_id_text = str(entity_id or "")
    if not provider_text or not entity_type_text or not entity_id_text:
        return ""
    if provider_text == METADATA_PROVIDER_MUSICBRAINZ:
        if entity_type_text == METADATA_ENTITY_RELEASE:
            return f"https://musicbrainz.org/release/{entity_id_text}"
        if entity_type_text == METADATA_ENTITY_RELEASE_GROUP:
            return f"https://musicbrainz.org/release-group/{entity_id_text}"
    if provider_text == METADATA_PROVIDER_DISCOGS:
        if entity_type_text == METADATA_ENTITY_RELEASE:
            return f"https://www.discogs.com/release/{entity_id_text}"
        if entity_type_text == METADATA_ENTITY_MASTER:
            return f"https://www.discogs.com/master/{entity_id_text}"
    return ""


def bulk_row_album_label(
    database: Path,
    row: BulkAlbumMetadataEditRowRequest,
) -> str:
    if row.album_label:
        return row.album_label
    connection = connect_existing_database(database)
    try:
        album_row = connection.execute(
            """
            SELECT album
            FROM library_albums
            WHERE album_id = ?
            """,
            (row.album_id,),
        ).fetchone()
        if album_row is None:
            return row.album_id
        artist_label = album_artist_display_text(connection, row.album_id)
        album_name = str(album_row["album"]) if album_row["album"] else "<unknown album>"
        return album_display_label(artist_label, album_name)
    finally:
        connection.close()


def brief_album_metadata_row_error(error: BaseException) -> str:
    reason = str(error).strip()
    if not reason:
        return error.__class__.__name__
    return reason.splitlines()[0][:240]


def run_edit_album_job(
    runtime: PlayerRuntime,
    job: AlbumTagEditJob,
    cancel_token: PlayerJobCancelToken,
) -> PlayerJobResult:
    started_at = perf_counter()
    result = edit_library_album_tags(
        runtime.database,
        job,
        cancel_check=cancel_token.raise_if_canceled,
    )
    duration_seconds = perf_counter() - started_at
    LOGGER.info(
        "tag edit completed for %s (tracks_updated=%s, duration=%.2fs)",
        result.album_label,
        result.tracks_updated,
        duration_seconds,
    )
    return PlayerJobResult(
        message=(
            f"Tags saved for {job.album_label}. "
            "Rescan the library to update library filters, artists, and stats."
        ),
        context={
            "album": job.album_name,
            "album_artist": job.request.album_artist,
            "tracks_updated": result.tracks_updated,
            "duration_seconds": duration_seconds,
            "rescan_recommended": True,
        },
    )


def run_edit_album_edit_job(
    runtime: PlayerRuntime,
    job: AlbumEditJob,
    cancel_token: PlayerJobCancelToken,
) -> PlayerJobResult:
    started_at = perf_counter()
    result = edit_library_album_edit(
        runtime.database,
        job,
        album_artist_split_patterns=runtime.album_artist_split_patterns,
        prefer_musicbrainz_english_aliases=runtime.prefer_musicbrainz_english_aliases,
        cancel_check=cancel_token.raise_if_canceled,
    )
    duration_seconds = perf_counter() - started_at
    LOGGER.info(
        "combined tag edit completed for %s (tracks_updated=%s, duration=%.2fs)",
        result.album_label,
        result.tracks_updated,
        duration_seconds,
    )
    return PlayerJobResult(
        message=(
            f"Tags saved for {job.album_label}. "
            "Rescan the library to update library filters, artists, and stats."
        ),
        context={
            "album": result.album,
            "album_artist": result.album_artist,
            "tracks_updated": result.tracks_updated,
            "duration_seconds": duration_seconds,
            "rescan_recommended": True,
        },
    )


def run_edit_album_musicbrainz_job(
    runtime: PlayerRuntime,
    job: AlbumMusicBrainzEditJob,
    cancel_token: PlayerJobCancelToken,
) -> PlayerJobResult:
    started_at = perf_counter()
    result = edit_library_album_musicbrainz(
        runtime.database,
        job,
        album_artist_split_patterns=runtime.album_artist_split_patterns,
        prefer_musicbrainz_english_aliases=runtime.prefer_musicbrainz_english_aliases,
        cancel_check=cancel_token.raise_if_canceled,
    )
    duration_seconds = perf_counter() - started_at
    if result.ids_cleared:
        LOGGER.info(
            "Metadata override cleared for %s (duration=%.2fs)",
            result.album_label,
            duration_seconds,
        )
        return PlayerJobResult(
            message=f"Metadata override cleared for {job.request.album_label}.",
            context={
                "album": job.request.album_name,
                "duration_seconds": duration_seconds,
            },
        )

    LOGGER.info(
        "MusicBrainz tag edit completed for %s (tracks_updated=%s, duration=%.2fs)",
        result.album_label,
        result.tracks_updated,
        duration_seconds,
    )
    return PlayerJobResult(
        message=(
            f"Tags saved for {job.request.album_label}. "
            "Rescan the library to update library filters, artists, and stats."
        ),
        context={
            "album": result.album,
            "album_artist": result.album_artist,
            "tracks_updated": result.tracks_updated,
            "duration_seconds": duration_seconds,
            "rescan_recommended": True,
        },
    )


def run_bulk_album_metadata_edit_job(
    runtime: PlayerRuntime,
    job: BulkAlbumMetadataEditJob,
    cancel_token: PlayerJobCancelToken,
) -> PlayerJobResult:
    started_at = perf_counter()
    result = edit_library_bulk_album_metadata_urls(
        runtime.database,
        job,
        album_artist_split_patterns=runtime.album_artist_split_patterns,
        prefer_musicbrainz_english_aliases=runtime.prefer_musicbrainz_english_aliases,
        cancel_check=cancel_token.raise_if_canceled,
    )
    duration_seconds = perf_counter() - started_at
    LOGGER.info(
        "bulk metadata URL edit completed (updated=%s, cleared=%s, skipped=%s, failed=%s, duration=%.2fs)",
        result.rows_updated,
        result.rows_cleared,
        result.rows_skipped,
        result.rows_failed,
        duration_seconds,
    )
    context: dict[str, object] = {
        "rows_updated": result.rows_updated,
        "rows_cleared": result.rows_cleared,
        "rows_skipped": result.rows_skipped,
        "rows_failed": result.rows_failed,
        "tracks_updated": result.tracks_updated,
        "duration_seconds": duration_seconds,
    }
    if result.rows_updated:
        context["rescan_recommended"] = True
    failed_rows = format_bulk_metadata_failures(result.failures)
    if failed_rows:
        context["failed_rows"] = failed_rows
    changed_rows = format_bulk_metadata_current_changes(result.changed_since_loaded)
    if changed_rows:
        context["changed_since_loaded"] = changed_rows

    return PlayerJobResult(
        message=bulk_album_metadata_edit_message(result),
        context=context,
    )


def bulk_album_metadata_edit_message(result: BulkAlbumMetadataEditResult) -> str:
    parts = [
        f"{result.rows_updated} updated",
        f"{result.rows_cleared} cleared",
        f"{result.rows_skipped} skipped",
        f"{result.rows_failed} failed",
    ]
    message = f"Bulk metadata URL edit finished: {', '.join(parts)}."
    if result.rows_updated:
        message += " Rescan the library to update library filters, artists, and stats."
    return message


def format_bulk_metadata_failures(
    failures: tuple[BulkAlbumMetadataEditFailure, ...],
) -> str:
    return "; ".join(
        bulk_metadata_failure_label(failure)
        for failure in failures
    )


def bulk_metadata_failure_label(failure: BulkAlbumMetadataEditFailure) -> str:
    label = failure.album_label
    if failure.group_label:
        label = f"{label} ({failure.group_label})"
    metadata_url = failure.metadata_url or "<cleared>"
    return f"{label}: {metadata_url} - {failure.reason}"


def format_bulk_metadata_current_changes(
    changes: tuple[BulkAlbumMetadataEditCurrentChange, ...],
) -> str:
    return "; ".join(
        bulk_metadata_current_change_label(change)
        for change in changes
    )


def bulk_metadata_current_change_label(
    change: BulkAlbumMetadataEditCurrentChange,
) -> str:
    label = change.album_label
    if change.group_label:
        label = f"{label} ({change.group_label})"
    loaded = "mixed" if change.loaded_metadata_mixed else change.loaded_metadata_url or "<empty>"
    current = "mixed" if change.current_metadata_mixed else change.current_metadata_url or "<empty>"
    return f"{label}: loaded {loaded}, current {current}"
