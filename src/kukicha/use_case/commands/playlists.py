from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
import logging
from pathlib import Path
import sqlite3

from ...audio_types import KNOWN_IMAGE_MIME_TYPES, content_type_for_name
from ..database import connect_database, utc_now_iso
from ...models import TrackRecord
from ...player_common import placeholders_for
from ...playlist_art import playlist_cover_svg
from ...scanner import is_url_resource, parse_uploaded_playlist_file, sniff_image_mime_type
from ..queries import PlaylistNotFoundError, TrackNotFoundError


LOGGER = logging.getLogger("kukicha.player")
PLAYLIST_KIND_LOCAL = "local"
PLAYLIST_KIND_REMOTE = "remote"
PLAYLIST_SOURCE_MANUAL = "manual"
PLAYLIST_SOURCE_FILE_IMPORT = "file_import"


@dataclass(frozen=True, slots=True)
class PlaylistMenuOption:
    playlist_id: int
    name: str
    checked: bool


@dataclass(frozen=True, slots=True)
class PlaylistMutationResult:
    playlist_id: int
    name: str
    kind: str
    source: str
    item_count: int
    skipped_relative_paths: tuple[str, ...] = ()

    def payload(self, *, message: str | None = None) -> dict[str, object]:
        result: dict[str, object] = {
            "playlist_id": self.playlist_id,
            "name": self.name,
            "kind": self.kind,
            "source": self.source,
            "item_count": self.item_count,
            "skipped_relative_paths": list(self.skipped_relative_paths),
        }
        if message:
            result["message"] = message
        return result


@dataclass(frozen=True, slots=True)
class PlaylistCover:
    playlist_id: int
    name: str
    cover_svg: str
    cover_mime_type: str
    cover_data: bytes | None = None

    @property
    def has_uploaded_cover(self) -> bool:
        return bool(self.cover_mime_type and self.cover_data)


@dataclass(frozen=True, slots=True)
class PlaylistCoverUploadResult:
    playlist_id: int
    name: str
    cover_mime_type: str

    def payload(self, *, message: str | None = None) -> dict[str, object]:
        result: dict[str, object] = {
            "playlist_id": self.playlist_id,
            "name": self.name,
            "cover_mime_type": self.cover_mime_type,
        }
        if message:
            result["message"] = message
        return result


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
                    name
                FROM library_playlists
                WHERE source = ?
                ORDER BY name COLLATE NOCASE, playlist_id
                """,
                (PLAYLIST_SOURCE_MANUAL,),
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
                checked=(track_id, int(row["playlist_id"])) in membership_pairs,
            )
            for row in playlist_rows
        )
        for track_id in requested_ids
    }


def create_or_replace_manual_playlist(
    database: Path,
    *,
    name: str | None = None,
    track_ids: Sequence[int] = (),
    playlist_id: int | None = None,
) -> PlaylistMutationResult:
    if playlist_id is None:
        return create_manual_playlist(database, name=name or "", track_ids=track_ids)
    return replace_manual_playlist(
        database,
        playlist_id,
        name=name,
        track_ids=track_ids,
    )


def create_manual_playlist(
    database: Path,
    *,
    name: str,
    track_ids: Sequence[int] = (),
) -> PlaylistMutationResult:
    playlist_name = normalized_playlist_name(name)
    now = utc_now_iso()
    with connect_database(database, create=False) as connection:
        track_rows = track_rows_for_playlist(connection, track_ids)
        cursor = connection.execute(
            """
            INSERT INTO library_playlists (
                name,
                kind,
                source,
                cover_svg,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                playlist_name,
                PLAYLIST_KIND_LOCAL,
                PLAYLIST_SOURCE_MANUAL,
                playlist_cover_svg(playlist_name),
                now,
                now,
            ),
        )
        playlist_id = int(cursor.lastrowid)
        replace_playlist_items(connection, playlist_id, track_rows)
        connection.commit()
    return PlaylistMutationResult(
        playlist_id=playlist_id,
        name=playlist_name,
        kind=PLAYLIST_KIND_LOCAL,
        source=PLAYLIST_SOURCE_MANUAL,
        item_count=len(track_rows),
    )


def replace_manual_playlist(
    database: Path,
    playlist_id: int,
    *,
    name: str | None = None,
    track_ids: Sequence[int] = (),
) -> PlaylistMutationResult:
    now = utc_now_iso()
    with connect_database(database, create=False) as connection:
        playlist_row = editable_playlist_row(connection, playlist_id)
        playlist_name = (
            normalized_playlist_name(name)
            if name is not None and str(name).strip()
            else str(playlist_row["name"])
        )
        track_rows = track_rows_for_playlist(connection, track_ids)
        connection.execute(
            """
            UPDATE library_playlists
            SET name = ?,
                kind = ?,
                cover_svg = ?,
                updated_at = ?
            WHERE playlist_id = ?
            """,
            (
                playlist_name,
                PLAYLIST_KIND_LOCAL,
                playlist_cover_svg(playlist_name),
                now,
                playlist_id,
            ),
        )
        replace_playlist_items(connection, playlist_id, track_rows)
        connection.commit()
    return PlaylistMutationResult(
        playlist_id=playlist_id,
        name=playlist_name,
        kind=PLAYLIST_KIND_LOCAL,
        source=PLAYLIST_SOURCE_MANUAL,
        item_count=len(track_rows),
    )


def update_manual_playlist(
    database: Path,
    playlist_id: int,
    *,
    name: str | None = None,
    track_ids_to_add: Sequence[int] = (),
    item_indexes_to_remove: Sequence[int] = (),
) -> PlaylistMutationResult:
    now = utc_now_iso()
    with connect_database(database, create=False) as connection:
        playlist_row = editable_playlist_row(connection, playlist_id)
        playlist_name = (
            normalized_playlist_name(name)
            if name is not None and str(name).strip()
            else str(playlist_row["name"])
        )
        current_items = list(
            connection.execute(
                """
                SELECT playlist_item_id
                FROM library_playlist_items
                WHERE playlist_id = ?
                ORDER BY position, playlist_item_id
                """,
                (playlist_id,),
            )
        )
        remove_indexes = normalized_playlist_item_indexes(
            item_indexes_to_remove,
            item_count=len(current_items),
        )
        if remove_indexes:
            remove_item_ids = tuple(
                int(row["playlist_item_id"])
                for index, row in enumerate(current_items)
                if index in remove_indexes
            )
            placeholders = placeholders_for(remove_item_ids)
            connection.execute(
                f"""
                DELETE FROM library_playlist_items
                WHERE playlist_id = ?
                    AND playlist_item_id IN ({placeholders})
                """,
                (playlist_id, *remove_item_ids),
            )
            compact_playlist_positions(connection, playlist_id)

        track_rows = track_rows_for_playlist(connection, track_ids_to_add)
        append_playlist_track_rows(connection, playlist_id, track_rows)
        kind = playlist_kind(connection, playlist_id)
        connection.execute(
            """
            UPDATE library_playlists
            SET name = ?,
                kind = ?,
                cover_svg = ?,
                updated_at = ?
            WHERE playlist_id = ?
            """,
            (
                playlist_name,
                kind,
                playlist_cover_svg(playlist_name),
                now,
                playlist_id,
            ),
        )
        item_count = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM library_playlist_items
                WHERE playlist_id = ?
                """,
                (playlist_id,),
            ).fetchone()[0]
        )
        connection.commit()
    return PlaylistMutationResult(
        playlist_id=playlist_id,
        name=playlist_name,
        kind=kind,
        source=PLAYLIST_SOURCE_MANUAL,
        item_count=item_count,
    )


def import_playlist_file(
    database: Path,
    *,
    filename: str,
    data: bytes,
    name: str | None = None,
) -> PlaylistMutationResult:
    uploaded_name = str(filename or "").strip()
    if not uploaded_name:
        raise ValueError("playlist file must have a filename")
    with connect_database(database, create=False) as connection:
        tracks = tuple(
            TrackRecord(path=str(row["path"]), track_id=int(row["track_id"]))
            for row in connection.execute(
                """
                SELECT track_id, path
                FROM library_tracks
                ORDER BY track_id
                """
            )
        )
        parsed = parse_uploaded_playlist_file(uploaded_name, data, tracks)
        playlist_name = (
            normalized_playlist_name(name, required=False)
            or normalized_playlist_name(parsed.name, required=False)
            or normalized_playlist_name(Path(uploaded_name).stem, required=False)
            or "Playlist"
        )
        for skipped_path in parsed.skipped_relative_paths:
            LOGGER.warning(
                "Ignoring relative playlist item path in uploaded playlist %s: %s",
                uploaded_name,
                skipped_path,
            )
        now = utc_now_iso()
        kind = playlist_kind_for_paths(item.path for item in parsed.items)
        cursor = connection.execute(
            """
            INSERT INTO library_playlists (
                name,
                kind,
                source,
                cover_svg,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                playlist_name,
                kind,
                PLAYLIST_SOURCE_FILE_IMPORT,
                playlist_cover_svg(playlist_name),
                now,
                now,
            ),
        )
        playlist_id = int(cursor.lastrowid)
        replace_playlist_items(connection, playlist_id, parsed.items)
        connection.commit()
    return PlaylistMutationResult(
        playlist_id=playlist_id,
        name=playlist_name,
        kind=kind,
        source=PLAYLIST_SOURCE_FILE_IMPORT,
        item_count=len(parsed.items),
        skipped_relative_paths=parsed.skipped_relative_paths,
    )


def delete_playlist(database: Path, playlist_id: int) -> PlaylistMutationResult:
    with connect_database(database, create=False) as connection:
        row = playlist_row(connection, playlist_id)
        item_count = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM library_playlist_items
                WHERE playlist_id = ?
                """,
                (playlist_id,),
            ).fetchone()[0]
        )
        connection.execute(
            "DELETE FROM library_playlists WHERE playlist_id = ?",
            (playlist_id,),
        )
        connection.execute(
            """
            DELETE FROM play_playlist_stats
            WHERE playlist_id = ? OR playlist_key = ?
            """,
            (playlist_id, str(playlist_id)),
        )
        connection.commit()
    return PlaylistMutationResult(
        playlist_id=playlist_id,
        name=str(row["name"]),
        kind=str(row["kind"] or PLAYLIST_KIND_LOCAL),
        source=str(row["source"] or PLAYLIST_SOURCE_MANUAL),
        item_count=item_count,
    )


def upload_playlist_cover(
    database: Path,
    playlist_id: int,
    *,
    filename: str,
    data: bytes,
) -> PlaylistCoverUploadResult:
    name = str(filename or "").strip()
    if not name:
        raise ValueError("cover file must have a filename")
    if not data:
        raise ValueError("cover file is empty")
    if Path(name).suffix.casefold() not in KNOWN_IMAGE_MIME_TYPES:
        raise ValueError("cover must be a GIF, JPEG, PNG, or WebP image")
    mime_type = sniff_image_mime_type(data, content_type_for_name(name))
    if mime_type not in set(KNOWN_IMAGE_MIME_TYPES.values()):
        raise ValueError("cover must be a GIF, JPEG, PNG, or WebP image")
    now = utc_now_iso()
    with connect_database(database, create=False) as connection:
        row = playlist_row(connection, playlist_id)
        connection.execute(
            """
            UPDATE library_playlists
            SET cover_mime_type = ?,
                cover_data = ?,
                updated_at = ?
            WHERE playlist_id = ?
            """,
            (mime_type, data, now, playlist_id),
        )
        connection.commit()
    return PlaylistCoverUploadResult(
        playlist_id=playlist_id,
        name=str(row["name"]),
        cover_mime_type=mime_type,
    )


def playlist_cover(database: Path, playlist_id: int) -> PlaylistCover:
    with connect_database(database, create=False) as connection:
        row = connection.execute(
            """
            SELECT playlist_id, name, cover_svg, cover_mime_type, cover_data
            FROM library_playlists
            WHERE playlist_id = ?
            """,
            (playlist_id,),
        ).fetchone()
    if row is None:
        raise PlaylistNotFoundError(playlist_id)
    cover_data = row["cover_data"]
    return PlaylistCover(
        playlist_id=int(row["playlist_id"]),
        name=str(row["name"]),
        cover_svg=str(row["cover_svg"] or ""),
        cover_mime_type=str(row["cover_mime_type"] or ""),
        cover_data=bytes(cover_data) if cover_data is not None else None,
    )


def set_track_playlist_membership(
    database: Path,
    track_id: int,
    playlist_id: int,
    checked: bool,
) -> dict[str, object]:
    return set_track_playlist_membership_database(
        database,
        track_id,
        playlist_id,
        checked,
    )


def set_track_playlist_membership_database(
    database: Path,
    track_id: int,
    playlist_id: int,
    checked: bool,
) -> dict[str, object]:
    with connect_database(database, create=False) as connection:
        track_row = connection.execute(
            """
            SELECT track_id, path
            FROM library_tracks
            WHERE track_id = ?
            """,
            (track_id,),
        ).fetchone()
        if track_row is None:
            raise TrackNotFoundError(track_id)
        playlist_row = editable_playlist_row(connection, playlist_id)
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
            compact_playlist_positions(connection, playlist_id)
        if changed:
            touch_playlist(connection, playlist_id)
        connection.commit()

    return {
        "track_id": track_id,
        "playlist_id": playlist_id,
        "name": str(playlist_row["name"]),
        "checked": checked,
    }


def normalized_playlist_name(value: str | None, *, required: bool = True) -> str:
    name = " ".join(str(value or "").split())
    if required and not name:
        raise ValueError("playlist name is required")
    return name


def editable_playlist_row(connection: sqlite3.Connection, playlist_id: int) -> sqlite3.Row:
    row = playlist_row(connection, playlist_id)
    if str(row["source"]) != PLAYLIST_SOURCE_MANUAL:
        raise ValueError("file-import playlists are read-only")
    return row


def playlist_row(connection: sqlite3.Connection, playlist_id: int) -> sqlite3.Row:
    row = connection.execute(
        """
        SELECT playlist_id, name, kind, source
        FROM library_playlists
        WHERE playlist_id = ?
        """,
        (playlist_id,),
    ).fetchone()
    if row is None:
        raise PlaylistNotFoundError(playlist_id)
    return row


def normalized_playlist_item_indexes(
    values: Sequence[int],
    *,
    item_count: int,
) -> set[int]:
    indexes = {int(value) for value in values}
    if any(index < 0 for index in indexes):
        raise ValueError("playlist item indexes must be non-negative")
    out_of_range = sorted(index for index in indexes if index >= item_count)
    if out_of_range:
        raise ValueError("playlist item index is out of range")
    return indexes


def track_rows_for_playlist(
    connection: sqlite3.Connection,
    track_ids: Sequence[int],
) -> tuple[sqlite3.Row, ...]:
    requested_ids = [int(track_id) for track_id in track_ids]
    if any(track_id <= 0 for track_id in requested_ids):
        raise ValueError("playlist song ids must be positive track ids")
    if not requested_ids:
        return ()
    unique_ids = tuple(dict.fromkeys(requested_ids))
    placeholders = placeholders_for(unique_ids)
    rows_by_id = {
        int(row["track_id"]): row
        for row in connection.execute(
            f"""
            SELECT track_id, path
            FROM library_tracks
            WHERE track_id IN ({placeholders})
            """,
            unique_ids,
        )
    }
    for track_id in requested_ids:
        if track_id not in rows_by_id:
            raise TrackNotFoundError(track_id)
    return tuple(rows_by_id[track_id] for track_id in requested_ids)


def replace_playlist_items(
    connection: sqlite3.Connection,
    playlist_id: int,
    items: Sequence[sqlite3.Row | TrackRecord | object],
) -> None:
    connection.execute(
        "DELETE FROM library_playlist_items WHERE playlist_id = ?",
        (playlist_id,),
    )
    for position, item in enumerate(items):
        path = str(get_item_value(item, "path"))
        track_id = get_item_value(item, "track_id")
        is_tracked = track_id is not None
        duration_is_indeterminate = bool(
            get_item_value(item, "duration_is_indeterminate", False)
        )
        connection.execute(
            """
            INSERT INTO library_playlist_items (
                playlist_id,
                position,
                path,
                track_id,
                title,
                duration_seconds,
                duration_is_indeterminate,
                genre,
                cover_url
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                playlist_id,
                position,
                path,
                int(track_id) if track_id is not None else None,
                None if is_tracked else get_item_value(item, "title", path),
                (
                    None
                    if is_tracked or duration_is_indeterminate
                    else get_item_value(item, "duration_seconds")
                ),
                0 if is_tracked else 1 if duration_is_indeterminate else 0,
                None if is_tracked else get_item_value(item, "genre"),
                None if is_tracked else get_item_value(item, "cover_url"),
            ),
        )


def append_playlist_track_rows(
    connection: sqlite3.Connection,
    playlist_id: int,
    track_rows: Sequence[sqlite3.Row],
) -> None:
    if not track_rows:
        return
    max_position = connection.execute(
        """
        SELECT MAX(position)
        FROM library_playlist_items
        WHERE playlist_id = ?
        """,
        (playlist_id,),
    ).fetchone()[0]
    next_position = int(max_position) + 1 if max_position is not None else 0
    for offset, row in enumerate(track_rows):
        connection.execute(
            """
            INSERT INTO library_playlist_items (
                playlist_id,
                position,
                path,
                track_id
            ) VALUES (?, ?, ?, ?)
            """,
            (
                playlist_id,
                next_position + offset,
                str(row["path"]),
                int(row["track_id"]),
            ),
        )


def get_item_value(item: object, key: str, default: object = None) -> object:
    if isinstance(item, sqlite3.Row):
        return item[key] if key in item.keys() else default
    return getattr(item, key, default)


def compact_playlist_positions(connection: sqlite3.Connection, playlist_id: int) -> None:
    rows = list(
        connection.execute(
            """
            SELECT playlist_item_id
            FROM library_playlist_items
            WHERE playlist_id = ?
            ORDER BY position, playlist_item_id
            """,
            (playlist_id,),
        )
    )
    for position, row in enumerate(rows):
        connection.execute(
            """
            UPDATE library_playlist_items
            SET position = ?
            WHERE playlist_item_id = ?
            """,
            (position, int(row["playlist_item_id"])),
        )


def touch_playlist(connection: sqlite3.Connection, playlist_id: int) -> None:
    kind = playlist_kind(connection, playlist_id)
    connection.execute(
        """
        UPDATE library_playlists
        SET kind = ?,
            updated_at = ?
        WHERE playlist_id = ?
        """,
        (kind, utc_now_iso(), playlist_id),
    )


def playlist_kind(connection: sqlite3.Connection, playlist_id: int) -> str:
    paths = (
        str(row["path"])
        for row in connection.execute(
            """
            SELECT path
            FROM library_playlist_items
            WHERE playlist_id = ?
            """,
            (playlist_id,),
        )
    )
    return playlist_kind_for_paths(paths)


def playlist_kind_for_paths(paths: Iterable[str]) -> str:
    return (
        PLAYLIST_KIND_REMOTE
        if any(is_url_resource(path) for path in paths)
        else PLAYLIST_KIND_LOCAL
    )
