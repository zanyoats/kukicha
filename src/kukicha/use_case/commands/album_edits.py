from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, fields
import logging
from pathlib import Path
import sqlite3
from time import perf_counter
from typing import Any
import urllib.parse

from ..queries import AlbumNotFoundError, TrackNotFoundError
from ..database import connect_database
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
from ..library import (
    CoverArtResolutionStats,
    GenreResolutionStats,
    load_taxonomy_genre_matcher_from_connection,
    update_genre_resolution_stats,
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
    delete_album_musicbrainz_track_links,
    get_musicbrainz_entity,
    musicbrainz_genres,
    musicbrainz_release_group_mbid,
    normalize_musicbrainz_mbid,
    store_album_musicbrainz_link,
    store_album_musicbrainz_track_link,
)
from ...player_common import optional_int, placeholders_for
from ...player_runtime import PlayerJobCancelToken, PlayerJobResult, PlayerRuntime
from ...scanner import write_album_audio_tags, write_track_audio_tags
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
    musicbrainz_release_mbid: str | None
    musicbrainz_release_group_mbid: str | None
    track_ids: tuple[int, ...] = ()


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


@dataclass(frozen=True, slots=True)
class MusicBrainzPayload:
    entity_type: str
    mbid: str
    payload: dict[str, object]


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

def prepare_album_musicbrainz_edit_request(
    database: Path,
    album_id: str,
    payload: dict[str, Any],
) -> AlbumMusicBrainzEditRequest:
    groups = parse_album_musicbrainz_group_requests(payload)

    connection = connect_database(database, create=False)
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
        raise ValueError("at least one MusicBrainz group is required")

    groups: list[AlbumMusicBrainzEditGroupRequest] = []
    seen_track_ids: set[int] = set()
    for item in raw_groups:
        if not isinstance(item, dict):
            raise ValueError("invalid MusicBrainz group payload")
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
    raw_musicbrainz_url = payload.get("musicbrainz_url")
    if raw_musicbrainz_url is not None and not isinstance(raw_musicbrainz_url, str):
        raise ValueError("MusicBrainz URL must be a string")

    raw_release_mbid = payload.get("musicbrainz_release_mbid")
    if raw_release_mbid is not None and not isinstance(raw_release_mbid, str):
        raise ValueError("MusicBrainz release ID must be a string")
    raw_release_group_mbid = payload.get("musicbrainz_release_group_mbid")
    if raw_release_group_mbid is not None and not isinstance(raw_release_group_mbid, str):
        raise ValueError("MusicBrainz release group ID must be a string")

    if raw_musicbrainz_url and raw_musicbrainz_url.strip():
        if (raw_release_mbid and raw_release_mbid.strip()) or (
            raw_release_group_mbid and raw_release_group_mbid.strip()
        ):
            raise ValueError("MusicBrainz URL cannot be combined with separate IDs")
        release_mbid, release_group_mbid = parse_musicbrainz_album_url(
            raw_musicbrainz_url
        )
    else:
        release_mbid = normalize_musicbrainz_mbid(
            raw_release_mbid or "",
            entity_type="release",
        )
        release_group_mbid = normalize_musicbrainz_mbid(
            raw_release_group_mbid or "",
            entity_type="release-group",
        )
    track_ids = parse_album_musicbrainz_track_ids(payload.get("track_ids"))
    if require_tracks and not track_ids:
        raise ValueError("at least one track is required")

    return AlbumMusicBrainzEditGroupRequest(
        musicbrainz_release_mbid=release_mbid,
        musicbrainz_release_group_mbid=release_group_mbid,
        track_ids=track_ids,
    )


def parse_musicbrainz_album_url(value: str) -> tuple[str | None, str | None]:
    text = value.strip()
    if not text:
        return None, None

    parts = urllib.parse.urlsplit(text)
    if not parts.scheme and not parts.netloc:
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
    connection = connect_database(database, create=False)
    try:
        requested_track_ids = list(request.track_ids)
        if not requested_track_ids and len(request.groups) == 1:
            ordered_track_rows = list(
                connection.execute(
                    """
                    SELECT
                        track_id,
                        album_id,
                        root_position,
                        path,
                        album,
                        title
                    FROM library_tracks
                    WHERE album_id = ?
                    ORDER BY track_id
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
                    musicbrainz_release_mbid=request.groups[0].musicbrainz_release_mbid,
                    musicbrainz_release_group_mbid=request.groups[0].musicbrainz_release_group_mbid,
                    track_ids=tuple(rows_by_id),
                ),
            )
        elif requested_track_ids:
            placeholders = placeholders_for(requested_track_ids)
            track_rows = list(
                connection.execute(
                    f"""
                    SELECT
                        track_id,
                        album_id,
                        root_position,
                        path,
                        album,
                        title
                    FROM library_tracks
                    WHERE track_id IN ({placeholders})
                    ORDER BY track_id
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
        album=str(row["album"]) if row["album"] else "",
        title=str(row["title"]) if row["title"] else "",
        genres=(),
        styles=(),
        track_artwork=None,
        album_artwork=None,
    )


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
    connection = connect_database(database, create=False)
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
                    track_id,
                    album_id,
                    root_position,
                    path,
                    album,
                    title
                FROM library_tracks
                WHERE track_id IN ({placeholders})
                ORDER BY track_id
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


def edit_library_album_musicbrainz(
    database: Path,
    job: AlbumMusicBrainzEditJob,
    *,
    album_artist_split_patterns: Iterable[str | None] = DEFAULT_ALBUM_ARTIST_SPLIT_PATTERNS,
    cancel_check: Callable[[], None] | None = None,
) -> AlbumMusicBrainzEditResult:
    connection = connect_database(database, create=False)
    try:
        connection.execute("SAVEPOINT edit_album_musicbrainz")
        try:
            if cancel_check is not None:
                cancel_check()

            request_file_album_id = file_album_id_from_album_id(job.request.album_id)
            connection.execute(
                """
                DELETE FROM album_musicbrainz_links
                WHERE file_album_id IN (?, ?)
                """,
                (request_file_album_id, job.request.album_id),
            )
            delete_album_musicbrainz_track_links(
                connection,
                (snapshot.path for snapshot in job.tracks),
            )

            pending_groups = [
                group
                for group in job.groups
                if (
                    group.request.musicbrainz_release_mbid is not None
                    or group.request.musicbrainz_release_group_mbid is not None
                )
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

                lookup_stats = MusicBrainzLookupStats()
                payloads, release_group_mbid = load_album_musicbrainz_edit_payloads(
                    connection,
                    group.request,
                    lookup_stats,
                )
                tag_values, genre_resolution = album_musicbrainz_audio_tags(
                    connection,
                    payloads,
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
                    write_album_audio_tags(
                        Path(snapshot.path),
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
                store_album_musicbrainz_link(
                    connection,
                    target_file_album_id,
                    release_mbid=group.request.musicbrainz_release_mbid,
                    release_group_mbid=release_group_mbid,
                )
                for snapshot in group.tracks:
                    store_album_musicbrainz_track_link(
                        connection,
                        snapshot.path,
                        target_file_album_id,
                        release_mbid=group.request.musicbrainz_release_mbid,
                        release_group_mbid=release_group_mbid,
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


def load_album_musicbrainz_edit_payloads(
    connection: sqlite3.Connection,
    request: AlbumMusicBrainzEditGroupRequest,
    stats: MusicBrainzLookupStats,
) -> tuple[tuple[MusicBrainzPayload, ...], str | None]:
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
                    entity_type="release",
                    mbid=request.musicbrainz_release_mbid,
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
                    entity_type="release-group",
                    mbid=release_group_mbid,
                    payload=release_group_payload,
                )
            )

    if not payloads:
        raise ValueError("No MusicBrainz data available for the saved IDs.")

    return tuple(payloads), release_group_mbid


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
) -> tuple[AlbumMusicBrainzAudioTags, GenreResolutionStats]:
    tag_payload = preferred_musicbrainz_tag_payload(payloads)
    genres, stats = musicbrainz_audio_genre_values(connection, payloads)
    return (
        AlbumMusicBrainzAudioTags(
            album=musicbrainz_album_tag_title(tag_payload),
            album_artist=musicbrainz_album_artist_tag_value(tag_payload),
            genres=genres,
        ),
        stats,
    )


def preferred_musicbrainz_tag_payload(
    payloads: tuple[MusicBrainzPayload, ...],
) -> MusicBrainzPayload:
    for payload in payloads:
        if payload.entity_type == "release":
            return payload
    return payloads[0]


def musicbrainz_album_tag_title(payload: MusicBrainzPayload) -> str:
    title = payload.payload.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError(f"MusicBrainz {payload.entity_type} payload is missing a title.")
    return title.strip()


def musicbrainz_album_artist_tag_value(payload: MusicBrainzPayload) -> str:
    artist_credit = payload.payload.get("artist-credit")
    if not isinstance(artist_credit, list):
        raise ValueError(
            f"MusicBrainz {payload.entity_type} payload is missing artist credit."
        )

    parts: list[str] = []
    for item in artist_credit:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name:
            continue
        joinphrase = item.get("joinphrase")
        parts.append(name + (joinphrase if isinstance(joinphrase, str) else ""))

    artist = "".join(parts).strip()
    if not artist:
        raise ValueError(
            f"MusicBrainz {payload.entity_type} payload is missing artist credit."
        )
    return artist


def musicbrainz_audio_genre_values(
    connection: sqlite3.Connection,
    payloads: tuple[MusicBrainzPayload, ...],
) -> tuple[tuple[str, ...], GenreResolutionStats]:
    matcher = load_taxonomy_genre_matcher_from_connection(connection)
    if not matcher.candidates:
        raise ValueError("No taxonomy genres are available for MusicBrainz genre matching.")

    source_genres: dict[str, tuple[str, str, str]] = {}
    for payload in payloads:
        for genre in musicbrainz_genres(payload.payload):
            source_genres.setdefault(
                genre.casefold(),
                (genre, payload.entity_type, payload.mbid),
            )

    if not source_genres:
        raise ValueError("MusicBrainz returned no genres for the saved IDs.")

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
        raise ValueError("No MusicBrainz genres matched the taxonomy for the saved IDs.")
    return genre_values, stats


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
        write_track_audio_tags(
            Path(snapshot.path),
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
        cancel_check=cancel_token.raise_if_canceled,
    )
    duration_seconds = perf_counter() - started_at
    if result.ids_cleared:
        LOGGER.info(
            "MusicBrainz IDs cleared for %s (duration=%.2fs)",
            result.album_label,
            duration_seconds,
        )
        return PlayerJobResult(
            message=f"MusicBrainz IDs cleared for {job.request.album_label}.",
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
