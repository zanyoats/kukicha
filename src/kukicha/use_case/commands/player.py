from __future__ import annotations

from collections.abc import Iterable
import json
from pathlib import Path
from sqlite3 import Connection
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from ...discogs import file_album_id_from_album_id
from ..cache import CACHE_TABLE_GROUP_BY_KEY
from ..database import connect_existing_database
from ..queries import LibraryQueries, album_where_clause
from ..queries.sql import placeholders_for

if TYPE_CHECKING:
    from ...models import TrackArtwork
    from ...player_runtime import PlayerQueueState
    from ...player_runtime import PlayerRuntime
    from ..queries import AlbumListQuery


QUEUE_STATE_ID = 1


def load_queue_state_database(database: Path) -> PlayerQueueState:
    with connect_existing_database(database) as connection:
        state = load_queue_state_connection(connection)
    refreshed_snapshots = refreshed_queue_snapshots(database, state)
    if refreshed_snapshots != state.snapshots:
        with connect_existing_database(database) as connection:
            state = write_queue_connection(
                connection,
                list(state.track_ids),
                refreshed_snapshots,
                position=state.position,
                paused=state.paused,
                errored_track_ids=state.errored_track_ids,
                unavailable_track_ids=state.unavailable_track_ids,
            )
    return state


def clear_queue_database(database: Path) -> PlayerQueueState:
    from ...player_presenters import normalized_queue_state

    with connect_existing_database(database) as connection:
        connection.execute("DELETE FROM player_queue_items")
        connection.execute("DELETE FROM player_queue_state")
    return normalized_queue_state([])


def load_queue_state_connection(connection: Connection) -> PlayerQueueState:
    from ...player_presenters import normalized_queue_state

    rows = list(
        connection.execute(
            """
            SELECT position, playback_id, snapshot_json, errored
            FROM player_queue_items
            ORDER BY position
            """
        )
    )
    state_row = connection.execute(
        """
        SELECT position, paused
        FROM player_queue_state
        WHERE state_id = ?
        """,
        (QUEUE_STATE_ID,),
    ).fetchone()
    track_ids = [int(row["playback_id"]) for row in rows]
    snapshots = [queue_snapshot_from_json(row["snapshot_json"]) for row in rows]
    errored_track_ids = [
        int(row["playback_id"])
        for row in rows
        if bool(row["errored"])
    ]
    persistent_errored_track_ids = persistent_queue_error_ids(
        connection,
        track_ids,
        errored_track_ids,
    )
    if persistent_errored_track_ids != errored_track_ids:
        sync_queue_error_flags(connection, persistent_errored_track_ids)
    position = int(state_row["position"]) if state_row is not None else 0
    paused = bool(state_row["paused"]) if state_row is not None else True
    unavailable_track_ids = unavailable_playback_ids(connection, track_ids)
    state = normalized_queue_state(
        track_ids,
        position=position,
        paused=paused,
        errored_track_ids=persistent_errored_track_ids,
        unavailable_track_ids=unavailable_track_ids,
        snapshots=snapshots,
    )
    if (
        state_row is None
        or state.position != position
        or state.paused != paused
    ):
        save_queue_state_connection(connection, state.position, state.paused)
    return state


def queue_snapshot_from_json(value: object) -> dict[str, object]:
    try:
        payload = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return dict(payload) if isinstance(payload, dict) else {}


def unavailable_playback_ids(
    connection: Connection,
    playback_ids: Iterable[int],
) -> list[int]:
    requested_ids = [int(playback_id) for playback_id in playback_ids]
    if not requested_ids:
        return []
    valid_ids = valid_playback_id_set(connection, requested_ids)
    seen: set[int] = set()
    unavailable_ids: list[int] = []
    for playback_id in requested_ids:
        if playback_id in valid_ids or playback_id in seen:
            continue
        seen.add(playback_id)
        unavailable_ids.append(playback_id)
    return unavailable_ids


def valid_playback_id_set(
    connection: Connection,
    playback_ids: Iterable[int],
) -> set[int]:
    requested_ids = {int(playback_id) for playback_id in playback_ids}
    valid_ids: set[int] = set()
    track_ids = sorted(playback_id for playback_id in requested_ids if playback_id > 0)
    if track_ids:
        placeholders = placeholders_for(track_ids)
        valid_ids.update(
            int(row["track_id"])
            for row in connection.execute(
                f"""
                SELECT track_id
                FROM library_tracks
                WHERE track_id IN ({placeholders})
                """,
                track_ids,
            )
        )
    playlist_item_ids = sorted(
        -playback_id for playback_id in requested_ids if playback_id < 0
    )
    if playlist_item_ids:
        placeholders = placeholders_for(playlist_item_ids)
        valid_ids.update(
            -int(row["playlist_item_id"])
            for row in connection.execute(
                f"""
                SELECT playlist_item_id
                FROM library_playlist_items
                WHERE playlist_item_id IN ({placeholders})
                """,
                playlist_item_ids,
            )
        )
    return valid_ids


def save_queue_state_connection(
    connection: Connection,
    position: int,
    paused: bool,
) -> None:
    from .jobs import utc_now_iso

    now = utc_now_iso()
    connection.execute(
        """
        INSERT INTO player_queue_state (
            state_id,
            position,
            paused,
            updated_at
        ) VALUES (?, ?, ?, ?)
        ON CONFLICT(state_id) DO UPDATE SET
            position = excluded.position,
            paused = excluded.paused,
            updated_at = excluded.updated_at
        """,
        (QUEUE_STATE_ID, position, 1 if paused else 0, now),
    )


def write_queue_database(
    database: Path,
    track_ids: list[int],
    snapshots: list[dict[str, object]],
    *,
    position: object = 0,
    paused: object = True,
    errored_track_ids: object = (),
    unavailable_track_ids: object | None = None,
) -> PlayerQueueState:
    with connect_existing_database(database) as connection:
        return write_queue_connection(
            connection,
            track_ids,
            snapshots,
            position=position,
            paused=paused,
            errored_track_ids=errored_track_ids,
            unavailable_track_ids=unavailable_track_ids,
        )


def write_queue_connection(
    connection: Connection,
    track_ids: list[int],
    snapshots: list[dict[str, object]],
    *,
    position: object = 0,
    paused: object = True,
    errored_track_ids: object = (),
    unavailable_track_ids: object | None = None,
) -> PlayerQueueState:
    from ...player_presenters import normalized_queue_error_ids, normalized_queue_state

    aligned_snapshots = aligned_queue_snapshots(track_ids, snapshots)
    persistent_errored_track_ids = persistent_queue_error_ids(
        connection,
        track_ids,
        normalized_queue_error_ids(errored_track_ids, track_ids),
    )
    resolved_unavailable_track_ids = (
        unavailable_playback_ids(connection, track_ids)
        if unavailable_track_ids is None
        else unavailable_track_ids
    )
    state = normalized_queue_state(
        track_ids,
        position=position,
        paused=paused,
        errored_track_ids=persistent_errored_track_ids,
        unavailable_track_ids=resolved_unavailable_track_ids,
        snapshots=aligned_snapshots,
    )
    errored_ids = set(state.errored_track_ids)
    connection.execute("DELETE FROM player_queue_items")
    for item_position, (playback_id, snapshot) in enumerate(
        zip(state.track_ids, state.snapshots, strict=True)
    ):
        connection.execute(
            """
            INSERT INTO player_queue_items (
                position,
                playback_id,
                snapshot_json,
                errored
            ) VALUES (?, ?, ?, ?)
            """,
            (
                item_position,
                playback_id,
                json.dumps(snapshot, sort_keys=True),
                1 if playback_id in errored_ids else 0,
            ),
        )
    save_queue_state_connection(connection, state.position, state.paused)
    return state


def persistent_queue_error_ids(
    connection: Connection,
    playback_ids: Iterable[int],
    errored_playback_ids: Iterable[int],
) -> list[int]:
    transient_error_ids = remote_playlist_playback_ids(connection, playback_ids)
    return [
        int(playback_id)
        for playback_id in errored_playback_ids
        if int(playback_id) not in transient_error_ids
    ]


def remote_playlist_playback_ids(
    connection: Connection,
    playback_ids: Iterable[int],
) -> set[int]:
    playlist_item_ids = sorted(
        {-int(playback_id) for playback_id in playback_ids if int(playback_id) < 0}
    )
    if not playlist_item_ids:
        return set()
    placeholders = placeholders_for(playlist_item_ids)
    return {
        -int(row["playlist_item_id"])
        for row in connection.execute(
            f"""
            SELECT playlist_item_id, path
            FROM library_playlist_items
            WHERE playlist_item_id IN ({placeholders})
                AND track_id IS NULL
            """,
            playlist_item_ids,
        )
        if is_remote_url(str(row["path"]))
    }


def is_remote_url(value: str) -> bool:
    parsed = urlsplit(value.strip())
    return parsed.scheme.casefold() in {"http", "https"} and bool(parsed.netloc)


def sync_queue_error_flags(
    connection: Connection,
    errored_playback_ids: Iterable[int],
) -> None:
    errored_ids = sorted(set(int(playback_id) for playback_id in errored_playback_ids))
    connection.execute("UPDATE player_queue_items SET errored = 0")
    if not errored_ids:
        return
    placeholders = placeholders_for(errored_ids)
    connection.execute(
        f"""
        UPDATE player_queue_items
        SET errored = 1
        WHERE playback_id IN ({placeholders})
        """,
        errored_ids,
    )


def aligned_queue_snapshots(
    track_ids: list[int],
    snapshots: list[dict[str, object]],
) -> list[dict[str, object]]:
    aligned = [dict(snapshot) for snapshot in snapshots[: len(track_ids)]]
    while len(aligned) < len(track_ids):
        aligned.append({})
    return aligned


def refreshed_queue_snapshots(
    database: Path,
    state: PlayerQueueState,
) -> list[dict[str, object]]:
    if not state.track_ids:
        return []
    from ...player_presenters import queue_track_snapshot, track_views_for_playback_ids

    available_ids = [
        playback_id
        for playback_id in state.track_ids
        if playback_id not in state.unavailable_track_ids
    ]
    live_snapshots_by_id = {
        track.track_id: queue_track_snapshot(track)
        for track in track_views_for_playback_ids(LibraryQueries(database), available_ids)
    }
    snapshots: list[dict[str, object]] = []
    for position, playback_id in enumerate(state.track_ids):
        previous = state.snapshots[position] if position < len(state.snapshots) else {}
        snapshot = dict(live_snapshots_by_id.get(playback_id) or previous)
        snapshot.update(queue_snapshot_display_overrides(previous))
        snapshots.append(snapshot)
    return snapshots


def playback_snapshots(
    database: Path,
    playback_ids: list[int],
) -> list[dict[str, object]]:
    from ...player_presenters import queue_track_snapshot, track_views_for_playback_ids

    api = LibraryQueries(database)
    return [
        queue_track_snapshot(track)
        for track in track_views_for_playback_ids(api, playback_ids)
    ]


def queue_snapshots_from_payload(
    payload: dict[str, Any],
    track_ids: list[int],
    fallback_snapshots: list[dict[str, object]],
) -> list[dict[str, object]]:
    payload_snapshots = payload.get("track_snapshots", ())
    if not isinstance(payload_snapshots, Iterable) or isinstance(
        payload_snapshots,
        (str, bytes),
    ):
        payload_snapshots = ()
    snapshots_by_id = {
        track_id: snapshot
        for snapshot in payload_snapshots
        if isinstance(snapshot, dict)
        and (track_id := queue_snapshot_track_id(snapshot)) in track_ids
    }
    merged_snapshots: list[dict[str, object]] = []
    for track_id, fallback in zip(track_ids, fallback_snapshots, strict=True):
        snapshot = dict(fallback)
        if override := snapshots_by_id.get(track_id):
            snapshot.update(queue_snapshot_display_overrides(override))
        merged_snapshots.append(snapshot)
    return merged_snapshots


def queue_snapshot_track_id(snapshot: dict[str, object]) -> int:
    try:
        return int(snapshot.get("trackId", 0))
    except (TypeError, ValueError):
        return 0


def queue_snapshot_display_overrides(
    snapshot: dict[str, object],
) -> dict[str, object]:
    track_number = snapshot.get("trackNumber")
    if isinstance(track_number, str):
        return {"trackNumber": track_number}
    return {}


def update_queue(runtime: PlayerRuntime, payload: dict[str, Any]) -> dict[str, object]:
    from ...player_common import safe_ints
    from ...player_presenters import queue_state_payload, valid_playback_ids

    requested_track_ids = safe_ints(payload.get("track_ids", []))
    track_ids = valid_playback_ids(
        LibraryQueries(runtime.database),
        requested_track_ids,
    )
    snapshots = queue_snapshots_from_payload(
        payload,
        track_ids,
        playback_snapshots(runtime.database, track_ids),
    )
    with runtime.queue_lock:
        runtime.queue_state = write_queue_database(
            runtime.database,
            track_ids,
            snapshots,
            position=payload.get("position", 0),
            paused=payload.get("paused", True),
            errored_track_ids=payload.get("errored_track_ids", ()),
            unavailable_track_ids=(),
        )
        return queue_state_payload(runtime.queue_state)


def append_queue(runtime: PlayerRuntime, payload: dict[str, Any]) -> dict[str, object]:
    from ...player_common import safe_ints
    from ...player_presenters import queue_state_payload, valid_playback_ids

    requested_track_ids = safe_ints(payload.get("track_ids", []))
    track_ids = valid_playback_ids(
        LibraryQueries(runtime.database),
        requested_track_ids,
    )
    snapshots = queue_snapshots_from_payload(
        payload,
        track_ids,
        playback_snapshots(runtime.database, track_ids),
    )
    with runtime.queue_lock:
        state = load_queue_state_database(runtime.database)
        if not track_ids:
            runtime.queue_state = state
            return queue_state_payload(state)
        was_empty = not state.track_ids
        position = 0 if was_empty else state.position
        paused = True if was_empty else state.paused
        runtime.queue_state = write_queue_database(
            runtime.database,
            list(state.track_ids) + track_ids,
            [dict(snapshot) for snapshot in state.snapshots] + snapshots,
            position=position,
            paused=paused,
            errored_track_ids=state.errored_track_ids,
            unavailable_track_ids=state.unavailable_track_ids,
        )
        return queue_state_payload(runtime.queue_state)


def remove_queue_item(runtime: PlayerRuntime, payload: dict[str, Any]) -> dict[str, object]:
    from ...player_common import optional_int
    from ...player_presenters import queue_state_payload

    requested_position = optional_int(payload.get("position"))
    with runtime.queue_lock:
        state = load_queue_state_database(runtime.database)
        if (
            requested_position is None
            or requested_position < 0
            or requested_position >= len(state.track_ids)
        ):
            runtime.queue_state = state
            return {
                "queue": queue_state_payload(state),
                "play_next": False,
                "stop_playback": False,
            }

        track_ids = list(state.track_ids)
        snapshots = [dict(snapshot) for snapshot in state.snapshots]
        removing_current = requested_position == state.position
        was_paused = state.paused
        track_ids.pop(requested_position)
        snapshots.pop(requested_position)

        position = state.position
        if requested_position < position:
            position -= 1

        play_next = False
        stop_playback = False
        if removing_current:
            next_position = next_available_position(
                track_ids,
                state.unavailable_track_ids,
                requested_position - 1,
            )
            if next_position == -1:
                position = len(track_ids)
                paused = True
                stop_playback = state.loaded_track_id is not None
            else:
                position = next_position
                paused = was_paused
                play_next = not was_paused
        else:
            paused = was_paused

        runtime.queue_state = write_queue_database(
            runtime.database,
            track_ids,
            snapshots,
            position=position,
            paused=paused,
            errored_track_ids=state.errored_track_ids,
            unavailable_track_ids=state.unavailable_track_ids,
        )
        return {
            "queue": queue_state_payload(runtime.queue_state),
            "play_next": play_next and runtime.queue_state.loaded_track_id is not None,
            "stop_playback": stop_playback,
        }


def next_available_position(
    track_ids: list[int],
    unavailable_track_ids: Iterable[int],
    position: int,
) -> int:
    unavailable_ids = set(unavailable_track_ids)
    for index in range(max(-1, position) + 1, len(track_ids)):
        if track_ids[index] not in unavailable_ids:
            return index
    return -1


def update_playback(runtime: PlayerRuntime, payload: dict[str, Any]) -> dict[str, object]:
    from ...player_common import clamp_int, optional_int
    from ...player_presenters import normalized_queue_error_ids, queue_state_payload

    with runtime.queue_lock:
        state = load_queue_state_database(runtime.database)
        position = state.position
        paused = state.paused
        errored_track_ids = state.errored_track_ids
        if "position" in payload:
            position = clamp_int(
                payload.get("position"),
                0,
                len(state.track_ids),
            )
        if "loaded_track_id" in payload:
            loaded_track_id = optional_int(payload.get("loaded_track_id"))
            if (
                loaded_track_id in state.track_ids
                and loaded_track_id not in state.unavailable_track_ids
            ):
                position = state.track_ids.index(loaded_track_id)
        if "paused" in payload:
            paused = bool(payload.get("paused"))
        if "errored_track_ids" in payload:
            errored_track_ids = normalized_queue_error_ids(
                payload.get("errored_track_ids"),
                state.track_ids,
            )
        runtime.queue_state = write_queue_database(
            runtime.database,
            list(state.track_ids),
            [dict(snapshot) for snapshot in state.snapshots],
            position=position,
            paused=paused,
            errored_track_ids=errored_track_ids,
            unavailable_track_ids=state.unavailable_track_ids,
        )
        return queue_state_payload(runtime.queue_state)


def pause_queue_for_document_load(runtime: PlayerRuntime) -> dict[str, object]:
    queue_lock = getattr(runtime, "queue_lock", None)
    if not hasattr(queue_lock, "__enter__"):
        from ...player_presenters import queue_state_payload

        return queue_state_payload(runtime.queue_state_copy())
    return update_playback(runtime, {"paused": True})



def update_track_playlist_membership(
    runtime: PlayerRuntime,
    track_id: int,
    playlist_id: int,
    payload: dict[str, Any],
) -> dict[str, object]:
    from .playlists import set_track_playlist_membership_database

    checked = bool(payload.get("checked"))
    return set_track_playlist_membership_database(
        runtime.database,
        track_id,
        playlist_id,
        checked,
    )


def save_album_artist_split_mapping(
    runtime: PlayerRuntime,
    payload: dict[str, Any],
) -> dict[str, object]:
    from ...player_errors import PlayerNotFoundError

    album_artist = str(payload.get("album_artist", "")).strip()
    if not album_artist:
        raise ValueError("album artist is required")

    mapped_artists = mapped_artists_text_from_payload(payload.get("mapped_artists", ""))
    if not mapped_artists:
        raise ValueError("at least one mapped artist is required")

    with connect_existing_database(runtime.database) as connection:
        row = connection.execute(
            """
            SELECT 1
            FROM album_artist_split_mappings
            WHERE album_artist = ?
            """,
            (album_artist,),
        ).fetchone()
        if row is None:
            raise PlayerNotFoundError(f"mapping does not exist: {album_artist}")
        connection.execute(
            """
            UPDATE album_artist_split_mappings
            SET mapped_artists = ?
            WHERE album_artist = ?
            """,
            (mapped_artists, album_artist),
        )

    return {
        "message": (
            f"Saved mapping for {album_artist}. "
            "Rescan the library to update library filters, artists, and stats."
        ),
        "mapping": {
            "album_artist": album_artist,
            "mapped_artists": mapped_artists,
        },
    }


def delete_album_metadata_override(
    runtime: PlayerRuntime,
    album_id: str,
) -> dict[str, object]:
    from ...player_errors import PlayerNotFoundError

    album_id = str(album_id or "").strip()
    file_album_id = file_album_id_from_album_id(album_id)
    if not file_album_id:
        raise ValueError("album id is required")

    with connect_existing_database(runtime.database) as connection:
        override = connection.execute(
            """
            SELECT 1
            FROM album_metadata_links
            WHERE file_album_id = ?
                AND COALESCE(TRIM(entity_id), '') != ''
            """,
            (file_album_id,),
        ).fetchone()
        if override is None:
            raise PlayerNotFoundError(f"metadata override does not exist: {album_id}")

        connection.execute(
            "DELETE FROM album_metadata_links WHERE file_album_id = ?",
            (file_album_id,),
        )
        connection.execute(
            "DELETE FROM album_metadata_track_links WHERE file_album_id = ?",
            (file_album_id,),
        )

    return {
        "album_id": album_id,
        "message": f"Deleted metadata override for {album_id}.",
    }


def delete_album_musicbrainz_override(
    runtime: PlayerRuntime,
    album_id: str,
) -> dict[str, object]:
    return delete_album_metadata_override(runtime, album_id)


def clear_cache_tables(
    runtime: PlayerRuntime,
    cache_key: str,
) -> dict[str, object]:
    from ...player_errors import PlayerNotFoundError

    cache_key = str(cache_key or "").strip()
    if not cache_key:
        raise ValueError("cache key is required")
    group = CACHE_TABLE_GROUP_BY_KEY.get(cache_key)
    if group is None:
        raise PlayerNotFoundError(f"cache target does not exist: {cache_key}")

    cleared_entries = 0
    with connect_existing_database(runtime.database) as connection:
        for table_name in group.table_names:
            row = connection.execute(
                f"SELECT COUNT(*) AS count FROM {table_name}"
            ).fetchone()
            cleared_entries += int(row["count"])
        for table_name in group.table_names:
            connection.execute(f"DELETE FROM {table_name}")

    return {
        "cache_key": group.key,
        "cleared_entries": cleared_entries,
        "message": f"Cleared {group.display_label} cache.",
    }


def update_album_star(
    runtime: PlayerRuntime,
    album_id: str,
    payload: dict[str, Any],
) -> dict[str, object]:
    from ...player_errors import PlayerNotFoundError
    from .jobs import utc_now_iso

    album_id = str(album_id or "").strip()
    if not album_id:
        raise ValueError("album id is required")
    starred = payload.get("starred") is True
    starred_at = utc_now_iso() if starred else None

    with connect_existing_database(runtime.database) as connection:
        row = connection.execute(
            """
            SELECT 1
            FROM library_albums
            WHERE album_id = ?
            """,
            (album_id,),
        ).fetchone()
        if row is None:
            raise PlayerNotFoundError(f"album does not exist: {album_id}")
        if starred_at is None:
            connection.execute(
                "DELETE FROM album_user_state WHERE album_id = ?",
                (album_id,),
            )
        else:
            connection.execute(
                """
                INSERT INTO album_user_state (album_id, starred_at)
                VALUES (?, ?)
                ON CONFLICT(album_id) DO UPDATE SET
                    starred_at = excluded.starred_at
                """,
                (album_id, starred_at),
            )
        connection.execute(
            """
            UPDATE library_albums
            SET starred_at = ?
            WHERE album_id = ?
            """,
            (starred_at, album_id),
        )

    return {
        "album_id": album_id,
        "starred": starred,
        "starred_at": starred_at,
    }


def update_filtered_album_stars(
    runtime: PlayerRuntime,
    query: AlbumListQuery,
    payload: dict[str, Any],
) -> dict[str, object]:
    from .jobs import utc_now_iso

    starred = payload.get("starred") is True
    query = LibraryQueries(runtime.database).expand_album_list_query(query)
    where_sql, params = album_where_clause(query)
    starred_at = utc_now_iso() if starred else None

    with connect_existing_database(runtime.database) as connection:
        rows = tuple(
            connection.execute(
                f"""
                SELECT albums.album_id, albums.starred_at
                FROM library_albums AS albums
                {where_sql}
                """,
                params,
            )
        )
        if starred:
            changed_album_ids = tuple(
                str(row["album_id"])
                for row in rows
                if row["starred_at"] is None
            )
        else:
            changed_album_ids = tuple(
                str(row["album_id"])
                for row in rows
                if row["starred_at"] is not None
            )

        if changed_album_ids:
            placeholders = placeholders_for(changed_album_ids)
            if starred_at is None:
                connection.execute(
                    f"""
                    DELETE FROM album_user_state
                    WHERE album_id IN ({placeholders})
                    """,
                    changed_album_ids,
                )
            else:
                connection.executemany(
                    """
                    INSERT INTO album_user_state (album_id, starred_at)
                    VALUES (?, ?)
                    ON CONFLICT(album_id) DO UPDATE SET
                        starred_at = excluded.starred_at
                    """,
                    ((album_id, starred_at) for album_id in changed_album_ids),
                )
            connection.execute(
                f"""
                UPDATE library_albums
                SET starred_at = ?
                WHERE album_id IN ({placeholders})
                """,
                (starred_at, *changed_album_ids),
            )

    action = "Starred" if starred else "Unstarred"
    changed_count = len(changed_album_ids)
    changed_album_label = "album" if changed_count == 1 else "albums"
    return {
        "starred": starred,
        "matched_count": len(rows),
        "changed_count": changed_count,
        "message": f"{action} {changed_count} filtered {changed_album_label}.",
    }


def update_artist_star(
    runtime: PlayerRuntime,
    artist: str,
    payload: dict[str, Any],
) -> dict[str, object]:
    from ...player_errors import PlayerNotFoundError
    from .jobs import utc_now_iso

    requested_artist = str(artist or "").strip()
    if not requested_artist:
        raise ValueError("artist is required")
    starred = payload.get("starred") is True
    starred_at = utc_now_iso() if starred else None

    with connect_existing_database(runtime.database) as connection:
        row = connection.execute(
            """
            SELECT album_artist
            FROM library_album_artist_stats
            WHERE album_artist = ? COLLATE NOCASE
            """,
            (requested_artist,),
        ).fetchone()
        if row is None:
            raise PlayerNotFoundError(f"artist does not exist: {requested_artist}")
        artist = str(row["album_artist"])
        if starred_at is None:
            connection.execute(
                "DELETE FROM artist_user_state WHERE artist = ?",
                (artist,),
            )
        else:
            connection.execute(
                """
                INSERT INTO artist_user_state (artist, starred_at)
                VALUES (?, ?)
                ON CONFLICT(artist) DO UPDATE SET
                    starred_at = excluded.starred_at
                """,
                (artist, starred_at),
            )

    return {
        "artist": artist,
        "starred": starred,
        "starred_at": starred_at,
    }


def update_track_star(
    runtime: PlayerRuntime,
    track_id: int,
    payload: dict[str, Any],
) -> dict[str, object]:
    from ...player_errors import PlayerNotFoundError
    from .jobs import utc_now_iso

    try:
        track_id = int(track_id)
    except (TypeError, ValueError) as error:
        raise ValueError("track id is required") from error
    starred = payload.get("starred") is True
    starred_at = utc_now_iso() if starred else None

    with connect_existing_database(runtime.database) as connection:
        row = connection.execute(
            """
            SELECT path
            FROM library_tracks
            WHERE track_id = ?
            """,
            (track_id,),
        ).fetchone()
        if row is None:
            raise PlayerNotFoundError(f"track does not exist: {track_id}")
        track_path = str(row["path"])
        if starred_at is None:
            connection.execute(
                "DELETE FROM track_user_state WHERE track_path = ?",
                (track_path,),
            )
        else:
            connection.execute(
                """
                INSERT INTO track_user_state (track_path, starred_at)
                VALUES (?, ?)
                ON CONFLICT(track_path) DO UPDATE SET
                    starred_at = excluded.starred_at
                """,
                (track_path, starred_at),
            )

    return {
        "track_id": track_id,
        "track_path": track_path,
        "starred": starred,
        "starred_at": starred_at,
    }


def mapped_artists_text_from_payload(value: object) -> str:
    if isinstance(value, list):
        lines = (str(item).strip() for item in value)
    else:
        lines = (line.strip() for line in str(value or "").splitlines())
    return "\n".join(line for line in lines if line)


def start_rescan_library(runtime: PlayerRuntime) -> dict[str, object]:
    from ...player_jobs import job_payload
    from .roots import (
        library_root_count,
        run_rescan_library_job,
    )

    root_count = library_root_count(runtime.database)
    if root_count <= 0:
        raise ValueError("no roots configured")

    queued_job = runtime.enqueue_job(
        kind="rescan_library",
        queued_message="Rescan queued.",
        running_message="Rescan running.",
        canceled_message="Rescan canceled.",
        failed_message="Rescan failed.",
        context={
            "roots_scanned": root_count,
        },
        runner=lambda cancel_token: run_rescan_library_job(runtime, cancel_token),
    )

    return {
        "message": "Rescan queued.",
        "job": job_payload(queued_job),
    }


def start_sync(
    runtime: PlayerRuntime,
    configured_roots: Iterable[Path],
    remote_roots: Iterable[object] = (),
) -> object:
    from .roots import run_sync_job

    roots = tuple(Path(root) for root in configured_roots)
    remote_root_tuple = tuple(remote_roots)
    return runtime.enqueue_job(
        kind="sync",
        queued_message="Sync queued.",
        running_message="Sync running.",
        canceled_message="Sync canceled.",
        failed_message="Sync failed.",
        context={
            "roots_configured": len(roots) + len(remote_root_tuple),
        },
        runner=lambda cancel_token: run_sync_job(
            runtime,
            roots,
            remote_root_tuple,
            cancel_token,
        ),
    )


def start_album_edit(
    runtime: PlayerRuntime,
    album_id: str,
    payload: dict[str, Any],
) -> dict[str, object]:
    from ...player_jobs import job_payload
    from .album_edits import (
        prepare_album_edit_job,
        prepare_album_musicbrainz_edit_job,
        run_edit_album_edit_job,
        run_edit_album_musicbrainz_job,
    )

    metadata_only_payload = None
    if "tags" not in payload:
        raw_metadata = payload.get("metadata", payload.get("musicbrainz"))
        if isinstance(raw_metadata, dict):
            metadata_only_payload = raw_metadata
        elif any(
            key in payload
            for key in (
                "groups",
                "metadata_url",
                "musicbrainz_url",
                "musicbrainz_release_mbid",
                "musicbrainz_release_group_mbid",
            )
        ):
            metadata_only_payload = payload

    if metadata_only_payload is not None:
        job = prepare_album_musicbrainz_edit_job(
            runtime.database,
            album_id,
            metadata_only_payload,
        )
        queued_job = runtime.enqueue_job(
            kind="edit_album_musicbrainz",
            queued_message=f"Metadata URL edit queued for {job.request.album_label}.",
            running_message=f"Metadata URL edit running for {job.request.album_label}.",
            canceled_message=f"Metadata URL edit canceled for {job.request.album_label}.",
            failed_message=f"Metadata URL edit failed for {job.request.album_label}.",
            context={
                "album": job.request.album_name,
                "track_links_updated": len(job.tracks),
            },
            runner=lambda cancel_token: run_edit_album_musicbrainz_job(
                runtime,
                job,
                cancel_token,
            ),
        )

        return {
            "message": f"Metadata URL edit queued for {job.request.album_label}.",
            "job": job_payload(queued_job),
        }

    job = prepare_album_edit_job(runtime.database, album_id, payload)
    queued_job = runtime.enqueue_job(
        kind="edit_album",
        queued_message=f"Tag edit queued for {job.album_label}.",
        running_message=f"Tag edit running for {job.album_label}.",
        canceled_message=f"Tag edit canceled for {job.album_label}.",
        failed_message=f"Tag edit failed for {job.album_label}.",
        context={
            "album": job.album_name,
            "album_artist": job.tag_job.request.album_artist,
            "tracks_updated": len(job.tag_job.request.tracks),
        },
        runner=lambda cancel_token: run_edit_album_edit_job(runtime, job, cancel_token),
    )

    return {
        "message": f"Tag edit queued for {job.album_label}.",
        "job": job_payload(queued_job),
    }


def start_bulk_album_metadata_edit(
    runtime: PlayerRuntime,
    payload: dict[str, Any],
) -> dict[str, object]:
    from ...player_jobs import job_payload
    from .album_edits import (
        prepare_bulk_album_metadata_edit_job,
        run_bulk_album_metadata_edit_job,
    )

    job = prepare_bulk_album_metadata_edit_job(payload)
    queued_job = runtime.enqueue_job(
        kind="bulk_album_metadata_urls",
        queued_message="Bulk metadata URL edit queued.",
        running_message="Bulk metadata URL edit running.",
        canceled_message="Bulk metadata URL edit canceled.",
        failed_message="Bulk metadata URL edit failed.",
        context={
            "rows_changed": len(job.rows),
        },
        runner=lambda cancel_token: run_bulk_album_metadata_edit_job(
            runtime,
            job,
            cancel_token,
        ),
    )
    return {
        "message": "Bulk metadata URL edit queued.",
        "job": job_payload(queued_job),
    }


def start_album_delete(
    runtime: PlayerRuntime,
    album_id: str,
) -> dict[str, object]:
    from ...player_jobs import job_payload
    from .album_deletes import prepare_album_delete_job, run_delete_album_job

    job = prepare_album_delete_job(runtime.database, album_id)
    queued_job = runtime.enqueue_job(
        kind="delete_album",
        queued_message=f"Delete queued for {job.album_label}.",
        running_message=f"Delete running for {job.album_label}.",
        canceled_message=f"Delete canceled for {job.album_label}.",
        failed_message=f"Delete failed for {job.album_label}.",
        context={
            "album": job.album_name,
            "tracks_deleted": len(job.tracks),
        },
        runner=lambda cancel_token: run_delete_album_job(runtime, job, cancel_token),
    )

    return {
        "message": f"Delete queued for {job.album_label}.",
        "job": job_payload(queued_job),
    }


def start_album_cover_upload(
    runtime: PlayerRuntime,
    album_id: str,
    *,
    filename: str,
    data: bytes,
) -> dict[str, object]:
    from ...player_jobs import job_payload
    from .album_covers import (
        prepare_album_cover_upload_job,
        run_upload_album_cover_job,
    )

    job = prepare_album_cover_upload_job(
        runtime.database,
        album_id,
        filename=filename,
        data=data,
    )
    queued_job = runtime.enqueue_job(
        kind="upload_album_cover",
        queued_message=f"Cover upload queued for {job.album_label}.",
        running_message=f"Cover upload running for {job.album_label}.",
        canceled_message=f"Cover upload canceled for {job.album_label}.",
        failed_message=f"Cover upload failed for {job.album_label}.",
        context={
            "album": job.album_name,
            "cover_filename": job.cover_filename,
            "cover_targets": len(job.targets),
        },
        runner=lambda cancel_token: run_upload_album_cover_job(
            runtime,
            job,
            cancel_token,
        ),
    )

    return {
        "message": f"Cover upload queued for {job.album_label}.",
        "job": job_payload(queued_job),
    }


def track_audio_path(runtime: PlayerRuntime, track_id: int) -> Path:
    return LibraryQueries(runtime.database).get_track_audio_path(track_id)


def track_audio_resource(runtime: PlayerRuntime, track_id: int) -> object:
    return LibraryQueries(runtime.database).get_track_audio_resource(track_id)


def playlist_audio_path(runtime: PlayerRuntime, playlist_item_id: int) -> Path:
    from ...scanner import is_url_resource

    item = LibraryQueries(runtime.database).get_playlist_item(playlist_item_id)
    if is_url_resource(item.path):
        raise FileNotFoundError("Playlist URL audio uses its source URL directly")
    return Path(item.path)


def playlist_audio_resource(runtime: PlayerRuntime, playlist_item_id: int) -> object:
    return LibraryQueries(runtime.database).get_playlist_item_audio_resource(
        playlist_item_id
    )


def track_artwork(
    runtime: PlayerRuntime,
    height_px: int,
    track_id: int,
) -> TrackArtwork | None:
    from ...player_media import extract_and_store_artwork

    queries = LibraryQueries(runtime.database)
    artwork = queries.get_track_artwork(track_id, height_px=height_px)
    missing_key = (height_px, track_id)
    if artwork is None and missing_key not in runtime.missing_artwork_keys:
        path = queries.get_track_audio_path(track_id)
        artwork_by_height = extract_and_store_artwork(runtime.database, str(path))
        artwork = artwork_by_height.get(height_px)
        if artwork is None:
            artwork = queries.get_track_artwork(track_id, height_px=height_px)
        if artwork is None:
            runtime.missing_artwork_keys.add(missing_key)
    return artwork
