from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..database import connect_database
from ..queries import LibraryQueries

if TYPE_CHECKING:
    from ...models import TrackArtwork
    from ...player_runtime import PlayerRuntime


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
            errored_track_ids=payload.get("errored_track_ids", ()),
        )
        return queue_state_payload(runtime.queue_state)


def update_playback(runtime: PlayerRuntime, payload: dict[str, Any]) -> dict[str, object]:
    from ...player_common import clamp_int, optional_int
    from ...player_presenters import normalized_queue_error_ids, queue_state_payload

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
        if "errored_track_ids" in payload:
            state.errored_track_ids = normalized_queue_error_ids(
                payload.get("errored_track_ids"),
                state.track_ids,
            )
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
        queued_job = start_playlist_file_update_job(runtime, job)
        from ...player_jobs import job_payload

        response["job"] = job_payload(queued_job)
    return response


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

    with connect_database(runtime.database) as connection:
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


def delete_stale_album_musicbrainz_override(
    runtime: PlayerRuntime,
    album_id: str,
) -> dict[str, object]:
    from ...player_errors import PlayerConflictError, PlayerNotFoundError

    album_id = str(album_id or "").strip()
    if not album_id:
        raise ValueError("album id is required")

    with connect_database(runtime.database) as connection:
        current_album = connection.execute(
            """
            SELECT 1
            FROM library_albums
            WHERE album_id = ?
            """,
            (album_id,),
        ).fetchone()
        if current_album is not None:
            raise PlayerConflictError(
                "MusicBrainz override belongs to a current album; edit it from the album page"
            )

        override = connection.execute(
            """
            SELECT 1
            FROM album_musicbrainz_links
            WHERE album_id = ?
                AND (
                    COALESCE(TRIM(release_mbid), '') != ''
                    OR COALESCE(TRIM(release_group_mbid), '') != ''
                )
            """,
            (album_id,),
        ).fetchone()
        if override is None:
            raise PlayerNotFoundError(f"MusicBrainz override does not exist: {album_id}")

        connection.execute(
            "DELETE FROM album_musicbrainz_links WHERE album_id = ?",
            (album_id,),
        )

    return {
        "album_id": album_id,
        "message": f"Deleted MusicBrainz override for {album_id}.",
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


def start_sync(runtime: PlayerRuntime, configured_roots: Iterable[Path]) -> object:
    from .roots import run_sync_job

    roots = tuple(Path(root) for root in configured_roots)
    return runtime.enqueue_job(
        kind="sync",
        queued_message="Sync queued.",
        running_message="Sync running.",
        canceled_message="Sync canceled.",
        failed_message="Sync failed.",
        context={
            "roots_configured": len(roots),
        },
        runner=lambda cancel_token: run_sync_job(runtime, roots, cancel_token),
    )


def start_album_musicbrainz_edit(
    runtime: PlayerRuntime,
    album_id: str,
    payload: dict[str, Any],
) -> dict[str, object]:
    from ...player_jobs import job_payload
    from .album_edits import (
        prepare_album_musicbrainz_edit_job,
        run_edit_album_musicbrainz_job,
    )

    job = prepare_album_musicbrainz_edit_job(runtime.database, album_id, payload)
    queued_job = runtime.enqueue_job(
        kind="edit_album_musicbrainz",
        queued_message=f"MusicBrainz ID edit queued for {job.request.album_label}.",
        running_message=f"MusicBrainz ID edit running for {job.request.album_label}.",
        canceled_message=f"MusicBrainz ID edit canceled for {job.request.album_label}.",
        failed_message=f"MusicBrainz ID edit failed for {job.request.album_label}.",
        context={
            "album": job.request.album_name,
            "tracks_scanned": len(job.tracks),
        },
        runner=lambda cancel_token: run_edit_album_musicbrainz_job(runtime, job, cancel_token),
    )

    return {
        "message": f"MusicBrainz ID edit queued for {job.request.album_label}.",
        "job": job_payload(queued_job),
    }


def start_album_tag_edit(
    runtime: PlayerRuntime,
    album_id: str,
    payload: dict[str, Any],
) -> dict[str, object]:
    from ...player_jobs import job_payload
    from .album_edits import prepare_album_tag_edit_job, run_edit_album_job

    job = prepare_album_tag_edit_job(runtime.database, album_id, payload)
    queued_job = runtime.enqueue_job(
        kind="edit_album",
        queued_message=f"Tag edit queued for {job.album_label}.",
        running_message=f"Tag edit running for {job.album_label}.",
        canceled_message=f"Tag edit canceled for {job.album_label}.",
        failed_message=f"Tag edit failed for {job.album_label}.",
        context={
            "album": job.album_name,
            "album_artist": job.request.album_artist,
            "tracks_updated": len(job.request.tracks),
        },
        runner=lambda cancel_token: run_edit_album_job(runtime, job, cancel_token),
    )

    return {
        "message": f"Tag edit queued for {job.album_label}.",
        "job": job_payload(queued_job),
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
