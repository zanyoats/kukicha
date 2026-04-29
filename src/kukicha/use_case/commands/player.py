from __future__ import annotations

from pathlib import Path
import logging
from threading import Thread
from typing import TYPE_CHECKING, Any

from ..queries import LibraryQueries

if TYPE_CHECKING:
    from ...models import TrackArtwork
    from ...player_runtime import PlayerRuntime


LOGGER = logging.getLogger("kukicha.player")


def update_queue(runtime: PlayerRuntime, payload: dict[str, Any]) -> dict[str, object]:
    from ...player_common import safe_ints
    from ...player_presenters import normalized_queue_state, queue_state_payload, valid_playback_ids

    requested_track_ids = safe_ints(payload.get("track_ids", []))
    track_ids = valid_playback_ids(
        LibraryQueries(runtime.database),
        requested_track_ids,
    )
    with runtime.queue_lock:
        runtime.queue_state = normalized_queue_state(
            track_ids,
            position=payload.get("position", 0),
            loaded_track_id=payload.get("loaded_track_id"),
            paused=payload.get("paused", True),
        )
        return queue_state_payload(runtime.queue_state)


def update_playback(runtime: PlayerRuntime, payload: dict[str, Any]) -> dict[str, object]:
    from ...player_common import clamp_int, optional_int
    from ...player_presenters import queue_state_payload

    with runtime.queue_lock:
        state = runtime.queue_state
        if "position" in payload:
            state.position = clamp_int(
                payload.get("position"),
                0,
                len(state.track_ids),
            )
        if "loaded_track_id" in payload:
            loaded_track_id = optional_int(payload.get("loaded_track_id"))
            state.loaded_track_id = loaded_track_id
            if loaded_track_id in state.track_ids:
                state.position = state.track_ids.index(loaded_track_id)
        if "paused" in payload:
            state.paused = bool(payload.get("paused"))
        return queue_state_payload(state)


def update_track_playlist_membership(
    runtime: PlayerRuntime,
    track_id: int,
    playlist_id: int,
    payload: dict[str, Any],
) -> dict[str, object]:
    from ...player_playlists import start_playlist_file_update_job
    from .playlists import set_track_playlist_membership_database

    checked = bool(payload.get("checked"))
    response, job = set_track_playlist_membership_database(
        runtime.database,
        track_id,
        playlist_id,
        checked,
    )
    if job is not None:
        start_playlist_file_update_job(runtime, job)
    return response


def start_add_root(runtime: PlayerRuntime, path: str) -> dict[str, object]:
    from ...player_errors import PlayerConflictError
    from .roots import (
        prepare_library_root,
        root_display_label,
        root_payload,
        run_add_root_job,
    )
    from .actions import record_player_action

    root = prepare_library_root(runtime.database, path)
    root_label = root_display_label(root)
    if not runtime.begin_library_job("add_root"):
        raise PlayerConflictError("another library job is already running")

    try:
        accepted_action = record_player_action(
            runtime.database,
            kind="add_root",
            status="accepted",
            message=f"Add and scan accepted for {root_label}.",
            context={
                "path": root.path,
                "root_position": root.position,
            },
        )
        runtime.publish_notification(accepted_action)
        scan_thread = Thread(
            target=run_add_root_job,
            args=(runtime, root),
            daemon=True,
        )
        scan_thread.start()
    except Exception:
        runtime.finish_library_job()
        try:
            failed_action = record_player_action(
                runtime.database,
                kind="add_root",
                status="failed",
                message=f"Add and scan could not start for {root_label}.",
                context={
                    "path": root.path,
                    "root_position": root.position,
                },
            )
        except Exception:
            LOGGER.exception("failed to record add-root start failure for %s", root.path)
        else:
            runtime.publish_notification(failed_action)
        raise

    return {
        "message": f"Add and scan accepted for {root_label}. See Notifications for updates.",
        "root": root_payload(root),
    }


def start_rescan_root(runtime: PlayerRuntime, position: int) -> dict[str, object]:
    from ...player_errors import PlayerConflictError
    from .roots import (
        library_root_by_position,
        root_display_label,
        root_payload,
        run_rescan_root_job,
    )
    from .actions import record_player_action

    root = library_root_by_position(runtime.database, position)
    root_label = root_display_label(root)
    if not runtime.begin_library_job("rescan_root"):
        raise PlayerConflictError("another library job is already running")

    try:
        accepted_action = record_player_action(
            runtime.database,
            kind="rescan_root",
            status="accepted",
            message=f"Rescan accepted for {root_label}.",
            context={
                "path": root.path,
                "root_position": root.position,
            },
        )
        runtime.publish_notification(accepted_action)
        rescan_thread = Thread(
            target=run_rescan_root_job,
            args=(runtime, root),
            daemon=True,
        )
        rescan_thread.start()
    except Exception:
        runtime.finish_library_job()
        try:
            failed_action = record_player_action(
                runtime.database,
                kind="rescan_root",
                status="failed",
                message=f"Rescan could not start for {root_label}.",
                context={
                    "path": root.path,
                    "root_position": root.position,
                },
            )
        except Exception:
            LOGGER.exception("failed to record rescan start failure for %s", root.path)
        else:
            runtime.publish_notification(failed_action)
        raise

    return {
        "message": f"Rescan accepted for {root_label}. See Notifications for updates.",
        "root": root_payload(root),
    }


def start_delete_root(runtime: PlayerRuntime, position: int) -> dict[str, object]:
    from ...player_errors import PlayerConflictError
    from .roots import (
        library_root_by_position,
        root_display_label,
        root_payload,
        run_delete_root_job,
    )
    from .actions import record_player_action

    root = library_root_by_position(runtime.database, position)
    root_label = root_display_label(root)
    if not runtime.begin_library_job("delete_root"):
        raise PlayerConflictError("another library job is already running")

    try:
        accepted_action = record_player_action(
            runtime.database,
            kind="delete_root",
            status="accepted",
            message=f"Delete accepted for {root_label}.",
            context={
                "path": root.path,
                "root_position": root.position,
            },
        )
        runtime.publish_notification(accepted_action)
        delete_thread = Thread(
            target=run_delete_root_job,
            args=(runtime, root),
            daemon=True,
        )
        delete_thread.start()
    except Exception:
        runtime.finish_library_job()
        try:
            failed_action = record_player_action(
                runtime.database,
                kind="delete_root",
                status="failed",
                message=f"Delete could not start for {root_label}.",
                context={
                    "path": root.path,
                    "root_position": root.position,
                },
            )
        except Exception:
            LOGGER.exception("failed to record delete start failure for %s", root.path)
        else:
            runtime.publish_notification(failed_action)
        raise

    return {
        "message": f"Delete accepted for {root_label}. See Notifications for updates.",
        "root": root_payload(root),
    }


def start_album_musicbrainz_edit(
    runtime: PlayerRuntime,
    album_id: str,
    payload: dict[str, Any],
) -> dict[str, object]:
    from .album_edits import (
        prepare_album_musicbrainz_edit_job,
        run_edit_album_musicbrainz_job,
    )
    from ...player_errors import PlayerConflictError
    from .actions import record_player_action

    job = prepare_album_musicbrainz_edit_job(runtime.database, album_id, payload)
    if not runtime.begin_library_job("edit_album_musicbrainz"):
        raise PlayerConflictError("another library job is already running")

    try:
        accepted_action = record_player_action(
            runtime.database,
            kind="edit_album_musicbrainz",
            status="accepted",
            message=f"MusicBrainz ID edit accepted for {job.request.album_label}.",
            context={
                "album": job.request.album_name,
                "tracks_scanned": len(job.tracks),
            },
        )
        runtime.publish_notification(accepted_action)
        edit_thread = Thread(
            target=run_edit_album_musicbrainz_job,
            args=(runtime, job),
            daemon=True,
        )
        edit_thread.start()
    except Exception:
        runtime.finish_library_job()
        try:
            failed_action = record_player_action(
                runtime.database,
                kind="edit_album_musicbrainz",
                status="failed",
                message=f"MusicBrainz ID edit could not start for {job.request.album_label}.",
                context={
                    "album": job.request.album_name,
                    "tracks_scanned": len(job.tracks),
                },
            )
        except Exception:
            LOGGER.exception("failed to record MusicBrainz edit start failure for %s", album_id)
        else:
            runtime.publish_notification(failed_action)
        raise

    return {
        "message": f"MusicBrainz ID edit accepted for {job.request.album_label}. See Notifications for updates.",
    }


def start_album_tag_edit(
    runtime: PlayerRuntime,
    album_id: str,
    payload: dict[str, Any],
) -> dict[str, object]:
    from .album_edits import prepare_album_tag_edit_job, run_edit_album_job
    from ...player_errors import PlayerConflictError
    from .actions import record_player_action

    job = prepare_album_tag_edit_job(runtime.database, album_id, payload)
    if not runtime.begin_library_job("edit_album"):
        raise PlayerConflictError("another library job is already running")

    try:
        accepted_action = record_player_action(
            runtime.database,
            kind="edit_album",
            status="accepted",
            message=f"Tag edit accepted for {job.album_label}.",
            context={
                "album": job.album_name,
                "album_artist": job.request.album_artist,
                "tracks_updated": len(job.request.tracks),
            },
        )
        runtime.publish_notification(accepted_action)
        edit_thread = Thread(
            target=run_edit_album_job,
            args=(runtime, job),
            daemon=True,
        )
        edit_thread.start()
    except Exception:
        runtime.finish_library_job()
        try:
            failed_action = record_player_action(
                runtime.database,
                kind="edit_album",
                status="failed",
                message=f"Tag edit could not start for {job.album_label}.",
                context={
                    "album": job.album_name,
                    "album_artist": job.request.album_artist,
                    "tracks_updated": len(job.request.tracks),
                },
            )
        except Exception:
            LOGGER.exception("failed to record tag-edit start failure for %s", album_id)
        else:
            runtime.publish_notification(failed_action)
        raise

    return {
        "message": f"Tag edit accepted for {job.album_label}. See Notifications for updates.",
    }


def track_audio_path(runtime: PlayerRuntime, track_id: int) -> Path:
    return LibraryQueries(runtime.database).get_track_audio_path(track_id)


def playlist_audio_path(runtime: PlayerRuntime, playlist_item_id: int) -> Path:
    from ...scanner import is_url_resource

    item = LibraryQueries(runtime.database).get_playlist_item(playlist_item_id)
    if is_url_resource(item.path):
        raise FileNotFoundError("Playlist URL audio uses its source URL directly")
    return Path(item.path)


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
