from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from ..database import connect_database
from ...player_common import placeholders_for
from ..queries import PlaylistNotFoundError, TrackNotFoundError


@dataclass(frozen=True, slots=True)
class PlaylistMenuOption:
    playlist_id: int
    name: str
    path: str
    checked: bool


@dataclass(frozen=True, slots=True)
class PlaylistFileUpdateJob:
    playlist_id: int
    playlist_name: str
    playlist_path: str
    track_id: int
    track_path: str
    artist: str
    album_artist: str
    title: str
    duration_seconds: float | None
    checked: bool


def playlist_menu_options_by_track_id(
    database: Path,
    track_ids: Iterable[int],
) -> dict[int, tuple[PlaylistMenuOption, ...]]:
    requested_ids = tuple(dict.fromkeys(int(track_id) for track_id in track_ids if int(track_id) > 0))
    if not requested_ids:
        return {}
    with connect_database(database, create=False) as connection:
        playlist_rows = list(
            connection.execute(
                """
                SELECT
                    playlist_id,
                    name,
                    path
                FROM library_playlists
                ORDER BY name COLLATE NOCASE, playlist_id
                """
            )
        )
        membership_pairs: set[tuple[int, int]] = set()
        if playlist_rows:
            placeholders = placeholders_for(requested_ids)
            membership_pairs = {
                (int(row["track_id"]), int(row["playlist_id"]))
                for row in connection.execute(
                    f"""
                    SELECT DISTINCT track_id, playlist_id
                    FROM library_playlist_items
                    WHERE track_id IN ({placeholders})
                    """,
                    requested_ids,
                )
            }
    return {
        track_id: tuple(
            PlaylistMenuOption(
                playlist_id=int(row["playlist_id"]),
                name=str(row["name"]),
                path=str(row["path"]),
                checked=(track_id, int(row["playlist_id"])) in membership_pairs,
            )
            for row in playlist_rows
        )
        for track_id in requested_ids
    }


def set_track_playlist_membership(
    database: Path,
    track_id: int,
    playlist_id: int,
    checked: bool,
) -> dict[str, object]:
    from ...player_playlists import update_playlist_file_for_membership

    response, job = set_track_playlist_membership_database(
        database,
        track_id,
        playlist_id,
        checked,
    )
    if job is not None:
        update_playlist_file_for_membership(job)
    return response


def set_track_playlist_membership_database(
    database: Path,
    track_id: int,
    playlist_id: int,
    checked: bool,
) -> tuple[dict[str, object], PlaylistFileUpdateJob | None]:
    job: PlaylistFileUpdateJob | None = None
    with connect_database(database, create=False) as connection:
        track_row = connection.execute(
            """
            SELECT track_id, path, artist, album_artist, title, duration_seconds
            FROM library_tracks
            WHERE track_id = ?
            """,
            (track_id,),
        ).fetchone()
        if track_row is None:
            raise TrackNotFoundError(track_id)
        playlist_row = connection.execute(
            "SELECT playlist_id, name, path FROM library_playlists WHERE playlist_id = ?",
            (playlist_id,),
        ).fetchone()
        if playlist_row is None:
            raise PlaylistNotFoundError(playlist_id)
        existing_count = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM library_playlist_items
                WHERE playlist_id = ? AND track_id = ?
                """,
                (playlist_id, track_id),
            ).fetchone()[0]
        )
        changed = (checked and existing_count == 0) or (not checked and existing_count > 0)
        if checked and existing_count == 0:
            max_position = connection.execute(
                """
                SELECT MAX(position)
                FROM library_playlist_items
                WHERE playlist_id = ?
                """,
                (playlist_id,),
            ).fetchone()[0]
            position = int(max_position) + 1 if max_position is not None else 0
            connection.execute(
                """
                INSERT INTO library_playlist_items (
                    playlist_id,
                    position,
                    path,
                    track_id
                ) VALUES (?, ?, ?, ?)
                """,
                (playlist_id, position, str(track_row["path"]), track_id),
            )
        elif not checked and existing_count > 0:
            connection.execute(
                """
                DELETE FROM library_playlist_items
                WHERE playlist_id = ? AND track_id = ?
                """,
                (playlist_id, track_id),
            )
        if changed:
            job = PlaylistFileUpdateJob(
                playlist_id=playlist_id,
                playlist_name=str(playlist_row["name"]),
                playlist_path=str(playlist_row["path"]),
                track_id=track_id,
                track_path=str(track_row["path"]),
                artist=str(track_row["artist"] or ""),
                album_artist=str(track_row["album_artist"] or ""),
                title=str(track_row["title"] or Path(str(track_row["path"])).stem),
                duration_seconds=(
                    float(track_row["duration_seconds"])
                    if track_row["duration_seconds"] is not None
                    else None
                ),
                checked=checked,
            )
        connection.commit()

    return {
        "track_id": track_id,
        "playlist_id": playlist_id,
        "name": str(playlist_row["name"]),
        "path": str(playlist_row["path"]),
        "checked": checked,
    }, job
