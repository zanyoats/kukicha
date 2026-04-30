from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from pathlib import Path
import sqlite3
from time import perf_counter
from typing import Any

from ..queries import AlbumNotFoundError, TrackNotFoundError
from ..database import connect_database, rebuild_album_search_index
from ...discogs import (
    most_common_artist_values,
    most_common_value,
    most_common_year,
    parse_year,
)
from ...album_artists import (
    DEFAULT_ALBUM_ARTIST_SPLIT_PATTERNS,
    display_album_artists,
    track_album_artist_values,
)
from ...display import display_album_title
from ..library import (
    apply_album_artist_mappings,
    CoverArtResolutionStats,
    GenreResolutionStats,
    resolve_library_cover_art,
    resolve_library_genres,
    track_album_id,
)
from ...models import (
    ALBUM_ARTWORK_HEIGHT,
    TRACK_ARTWORK_HEIGHT,
    MusicLibrary,
    TrackArtwork,
    TrackRecord,
)
from ..musicbrainz import (
    load_album_musicbrainz_links,
    normalize_musicbrainz_mbid,
    store_album_musicbrainz_link,
)
from ...player_common import optional_int, placeholders_for
from .roots import (
    library_job_detail_lines,
    library_job_summary_text,
    reconcile_library_albums,
)
from ...player_runtime import PlayerJobCancelToken, PlayerJobResult, PlayerRuntime
from ...scanner import missing_required_tags, scan_track, write_track_audio_tags

LOGGER = logging.getLogger("kukicha.player")

@dataclass(frozen=True, slots=True)
class AlbumTrackTagEdit:
    track_id: int
    artist: str
    album: str


@dataclass(frozen=True, slots=True)
class AlbumMusicBrainzEditRequest:
    album_id: str
    album_label: str
    album_name: str
    musicbrainz_release_mbid: str | None
    musicbrainz_release_group_mbid: str | None


@dataclass(frozen=True, slots=True)
class AlbumMusicBrainzEditJob:
    request: AlbumMusicBrainzEditRequest
    tracks: tuple[AlbumEditSnapshot, ...]


@dataclass(frozen=True, slots=True)
class AlbumMusicBrainzEditResult:
    album_label: str
    tracks_scanned: int
    albums_scanned: int
    affected_album_ids: tuple[str, ...]
    genre_resolution: GenreResolutionStats
    cover_art_resolution: CoverArtResolutionStats


@dataclass(frozen=True, slots=True)
class AlbumTagEditRequest:
    album_id: str
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
    raw_release_mbid = payload.get("musicbrainz_release_mbid")
    if raw_release_mbid is not None and not isinstance(raw_release_mbid, str):
        raise ValueError("MusicBrainz release ID must be a string")
    raw_release_group_mbid = payload.get("musicbrainz_release_group_mbid")
    if raw_release_group_mbid is not None and not isinstance(raw_release_group_mbid, str):
        raise ValueError("MusicBrainz release group ID must be a string")

    release_mbid = normalize_musicbrainz_mbid(
        raw_release_mbid or "",
        entity_type="release",
    )
    release_group_mbid = normalize_musicbrainz_mbid(
        raw_release_group_mbid or "",
        entity_type="release-group",
    )

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
        musicbrainz_release_mbid=release_mbid,
        musicbrainz_release_group_mbid=release_group_mbid,
    )


def prepare_album_musicbrainz_edit_job(
    database: Path,
    album_id: str,
    payload: dict[str, Any],
) -> AlbumMusicBrainzEditJob:
    request = prepare_album_musicbrainz_edit_request(database, album_id, payload)
    connection = connect_database(database, create=False)
    try:
        track_rows = list(
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
        if not track_rows:
            raise AlbumNotFoundError(album_id)

        track_ids = [int(row["track_id"]) for row in track_rows]
        placeholders = placeholders_for(track_ids)

        genre_rows: dict[int, list[str]] = {}
        for row in connection.execute(
            f"""
            SELECT track_id, genre
            FROM library_track_genres
            WHERE track_id IN ({placeholders})
            ORDER BY track_id, position
            """,
            track_ids,
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
            track_ids,
        ):
            style_rows.setdefault(int(row["track_id"]), []).append(str(row["style"]))

        artwork_rows: dict[int, dict[int, TrackArtwork]] = {}
        for row in connection.execute(
            f"""
            SELECT track_id, height_px, mime_type, data
            FROM library_track_artwork
            WHERE track_id IN ({placeholders})
            """,
            track_ids,
        ):
            artwork_rows.setdefault(int(row["track_id"]), {})[int(row["height_px"])] = TrackArtwork(
                mime_type=str(row["mime_type"]),
                data=bytes(row["data"]),
            )

        snapshots: list[AlbumEditSnapshot] = []
        for row in track_rows:
            track_id = int(row["track_id"])
            track_artworks = artwork_rows.get(track_id, {})
            snapshots.append(
                AlbumEditSnapshot(
                    track_id=track_id,
                    album_id=str(row["album_id"]) if row["album_id"] else "",
                    root_position=int(row["root_position"]) if row["root_position"] is not None else None,
                    path=str(row["path"]),
                    album=str(row["album"]) if row["album"] else "",
                    title=str(row["title"]) if row["title"] else "",
                    genres=tuple(genre_rows.get(track_id, ())),
                    styles=tuple(style_rows.get(track_id, ())),
                    track_artwork=track_artworks.get(TRACK_ARTWORK_HEIGHT),
                    album_artwork=track_artworks.get(ALBUM_ARTWORK_HEIGHT),
                )
            )
    finally:
        connection.close()

    return AlbumMusicBrainzEditJob(
        request=request,
        tracks=tuple(snapshots),
    )


def parse_album_tag_edit_request(
    album_id: str,
    payload: dict[str, Any],
) -> AlbumTagEditRequest:
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
                album=str(item.get("album") or "").strip(),
            )
        )
    return AlbumTagEditRequest(
        album_id=album_id,
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

    for track_edit, snapshot in zip(request.tracks, snapshots, strict=True):
        title = snapshot.title or Path(snapshot.path).name
        if not track_edit.album:
            raise ValueError(f"track requires album title: {title}")
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
    rescanned_tracks: list[TrackRecord] = []
    for snapshot in job.tracks:
        if cancel_check is not None:
            cancel_check()
        track = scan_track(Path(snapshot.path))
        if track.scan_error:
            raise OSError(f"failed to rescan {snapshot.path}: {track.scan_error}")
        missing_fields = missing_required_tags(track)
        if missing_fields:
            joined = ", ".join(missing_fields)
            raise ValueError(f"rescanned track is missing required fields for {snapshot.path}: {joined}")
        track.track_id = snapshot.track_id
        track.root_position = snapshot.root_position
        rescanned_tracks.append(track)

    connection = connect_database(database, create=False)
    try:
        if cancel_check is not None:
            cancel_check()
        apply_album_artist_mappings(
            connection,
            rescanned_tracks,
            split_patterns=album_artist_split_patterns,
        )
        grouped_new_tracks: dict[str, list[TrackRecord]] = {}
        for track in rescanned_tracks:
            album_id = track_album_id(track)
            if album_id:
                grouped_new_tracks.setdefault(album_id, []).append(track)
        albums_scanned = len(grouped_new_tracks)
        if albums_scanned != 1:
            raise ValueError(
                "MusicBrainz IDs can only be edited when the rescanned tracks remain a single album"
            )

        targeted_library = MusicLibrary(
            roots=[],
            tracks=rescanned_tracks,
            supported_extensions=[],
            generated_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        )
        affected_album_ids = tuple(
            sorted(
                {
                    *(snapshot.album_id for snapshot in job.tracks if snapshot.album_id),
                    *grouped_new_tracks.keys(),
                }
            )
        )
        target_album_id = next(iter(grouped_new_tracks))

        connection.execute("SAVEPOINT edit_album_musicbrainz")
        try:
            if cancel_check is not None:
                cancel_check()
            store_album_musicbrainz_link(
                connection,
                target_album_id,
                release_mbid=job.request.musicbrainz_release_mbid,
                release_group_mbid=job.request.musicbrainz_release_group_mbid,
            )
            genre_resolution = resolve_library_genres(
                targeted_library,
                database,
                connection=connection,
                album_artist_split_patterns=album_artist_split_patterns,
            ) or GenreResolutionStats()
            cover_art_resolution = resolve_library_cover_art(
                targeted_library,
                database,
                connection=connection,
                album_artist_split_patterns=album_artist_split_patterns,
            ) or CoverArtResolutionStats()
            ensure_album_rows_exist(connection, grouped_new_tracks)
            replace_library_tracks(connection, rescanned_tracks)
            reconcile_library_albums(
                connection,
                list(affected_album_ids),
                album_artist_split_patterns=album_artist_split_patterns,
            )
            rebuild_album_search_index(connection)
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
        tracks_scanned=len(rescanned_tracks),
        albums_scanned=albums_scanned,
        affected_album_ids=affected_album_ids,
        genre_resolution=genre_resolution,
        cover_art_resolution=cover_art_resolution,
    )


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
            album=track_edit.album,
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


def ensure_album_rows_exist(
    connection: sqlite3.Connection,
    grouped_tracks: dict[str, list[TrackRecord]],
) -> None:
    for album_id, tracks in grouped_tracks.items():
        artists = most_common_artist_values(
            track_album_artist_values(track)
            for track in tracks
        )
        artist = display_album_artists(artists) or "<unknown artist>"
        album = most_common_value(track.album for track in tracks) or "<unknown album>"
        year = most_common_year(parse_year(track.date) for track in tracks)
        file_created_at = min(
            (track.file_created_at for track in tracks if track.file_created_at),
            default=None,
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO library_albums (
                album_id,
                album,
                year,
                track_count,
                file_created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (album_id, album, year, len(tracks), file_created_at),
        )
        for position, album_artist in enumerate(artists or (artist,)):
            connection.execute(
                """
                INSERT OR IGNORE INTO library_album_artists (album_id, position, artist)
                VALUES (?, ?, ?)
                """,
                (album_id, position, album_artist),
            )


def replace_library_tracks(
    connection: sqlite3.Connection,
    tracks: list[TrackRecord],
) -> None:
    track_ids = [
        track.track_id
        for track in tracks
        if track.track_id is not None
    ]
    if not track_ids:
        return

    placeholders = placeholders_for(track_ids)
    connection.execute(
        f"DELETE FROM library_track_artwork WHERE track_id IN ({placeholders})",
        track_ids,
    )
    connection.execute(
        f"DELETE FROM library_track_styles WHERE track_id IN ({placeholders})",
        track_ids,
    )
    connection.execute(
        f"DELETE FROM library_track_genres WHERE track_id IN ({placeholders})",
        track_ids,
    )

    for track in tracks:
        if track.track_id is None:
            continue
        connection.execute(
            """
            UPDATE library_tracks
            SET album_id = ?,
                root_position = ?,
                path = ?,
                file_created_at = ?,
                file_type = ?,
                scan_error = ?,
                artist = ?,
                album_artist = ?,
                composer = ?,
                album = ?,
                title = ?,
                work = ?,
                grouping = ?,
                movement_name = ?,
                is_compilation = ?,
                track_number = ?,
                disc_number = ?,
                date = ?,
                duration_seconds = ?,
                bitrate = ?
            WHERE track_id = ?
            """,
            (
                track_album_id(track),
                track.root_position,
                track.path,
                track.file_created_at,
                track.file_type,
                track.scan_error,
                track.artist,
                track.album_artist,
                track.composer,
                track.album,
                track.title,
                track.work,
                track.grouping,
                track.movement_name,
                1 if track.is_compilation else 0,
                track.track_number,
                track.disc_number,
                track.date,
                track.duration_seconds,
                track.bitrate,
                track.track_id,
            ),
        )
        for position, genre in enumerate(track.genres):
            connection.execute(
                """
                INSERT INTO library_track_genres (track_id, position, genre)
                VALUES (?, ?, ?)
                """,
                (track.track_id, position, genre),
            )
        for position, style in enumerate(track.styles):
            connection.execute(
                """
                INSERT INTO library_track_styles (track_id, position, style)
                VALUES (?, ?, ?)
                """,
                (track.track_id, position, style),
            )
        for height_px, artwork in (
            (TRACK_ARTWORK_HEIGHT, track.artwork),
            (ALBUM_ARTWORK_HEIGHT, track.album_artwork),
        ):
            if artwork is None or not artwork.data:
                continue
            connection.execute(
                """
                INSERT INTO library_track_artwork (
                    track_id,
                    height_px,
                    mime_type,
                    data
                ) VALUES (?, ?, ?, ?)
                """,
                (track.track_id, height_px, artwork.mime_type, artwork.data),
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
            "Rescan the affected root to update library filters, artists, and stats."
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
    LOGGER.info(
        "%s",
        library_job_summary_text(
            "MusicBrainz ID edit",
            result.album_label,
            tracks_scanned=result.tracks_scanned,
            albums_scanned=result.albums_scanned,
            files_missing_required_tags=0,
            duration_seconds=duration_seconds,
        ),
    )
    for line in library_job_detail_lines(
        tracks_scanned=result.tracks_scanned,
        albums_scanned=result.albums_scanned,
        files_missing_required_tags=0,
        genre_resolution=result.genre_resolution,
        cover_art_resolution=result.cover_art_resolution,
    ):
        LOGGER.info("%s", line)
    return PlayerJobResult(
        message=f"MusicBrainz ID edit completed for {job.request.album_label}.",
        context={
            "album": job.request.album_name,
            "tracks_scanned": result.tracks_scanned,
            "albums_scanned": result.albums_scanned,
            "duration_seconds": duration_seconds,
        },
    )
