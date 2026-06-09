from __future__ import annotations

from pathlib import Path
from typing import Literal

from ...display import display_album_title
from ...player_common import format_count_label
from ...player_jobs import job_payload
from ...player_runtime import PlayerJobCancelToken, PlayerJobResult, PlayerRuntime
from ..queries import LibraryQueries
from ..recommendations import (
    RECOMMENDATION_CONFIG,
    RecommendationResult,
    RecommendationService,
    normalize_recommendation_limit,
    normalize_recommendation_mode,
)

RecommendationPlaylistKind = Literal[
    "track_radio",
    "album_radio",
    "artist_radio",
]

RECOMMENDATION_PLAYLIST_JOB_KIND = "generate_playlist"


def start_recommendation_playlist(
    runtime: PlayerRuntime,
    kind: RecommendationPlaylistKind,
    seed: object | None = None,
    *,
    mode: object | None = None,
    limit: object | None = None,
) -> dict[str, object]:
    normalized_mode = normalize_recommendation_mode(mode)
    normalized_limit = normalize_recommendation_limit(
        limit,
        default=RECOMMENDATION_CONFIG.default_limit,
        max_limit=RECOMMENDATION_CONFIG.max_limit,
    )
    request = {
        "kind": kind,
        "seed": seed,
        "mode": normalized_mode,
        "limit": normalized_limit,
    }
    title = recommendation_playlist_title(kind)
    source_text = recommendation_playlist_source_text(
        runtime.database,
        kind,
        seed,
    )
    context = recommendation_playlist_job_context(
        title=title,
        source_text=source_text,
        mode=normalized_mode,
        limit=normalized_limit,
    )
    queued_message = f"{title} queued."
    queued_job = runtime.enqueue_job(
        kind=RECOMMENDATION_PLAYLIST_JOB_KIND,
        queued_message=queued_message,
        running_message=f"{title} running.",
        canceled_message=f"{title} canceled.",
        failed_message=f"{title} failed.",
        context=context,
        runner=lambda cancel_token: run_recommendation_playlist_job(
            runtime,
            request,
            title=title,
            source_text=source_text,
            cancel_token=cancel_token,
        ),
    )
    return {
        "message": queued_message,
        "job": job_payload(queued_job),
    }


def run_recommendation_playlist_job(
    runtime: PlayerRuntime,
    request: dict[str, object],
    *,
    title: str,
    source_text: str,
    cancel_token: PlayerJobCancelToken,
) -> PlayerJobResult:
    cancel_token.raise_if_canceled()
    service = RecommendationService(runtime.database)
    kind = str(request["kind"])
    mode = str(request["mode"])
    limit = int(request["limit"])
    if kind == "track_radio":
        results = service.get_track_radio(int(request["seed"]), mode=mode, limit=limit)
    elif kind == "album_radio":
        results = service.get_album_radio(str(request["seed"]), mode=mode, limit=limit)
    elif kind == "artist_radio":
        results = service.get_artist_radio(str(request["seed"]), mode=mode, limit=limit)
    else:
        raise ValueError(f"unsupported recommendation playlist kind: {kind}")
    cancel_token.raise_if_canceled()

    track_ids = recommendation_track_ids(results)
    context = recommendation_playlist_job_context(
        title=title,
        source_text=source_text,
        mode=mode,
        limit=limit,
        track_ids=track_ids,
    )
    count_text = format_count_label(len(track_ids), "track", "tracks")
    if source_text:
        message = f"{title} generated {count_text} for {source_text}."
    else:
        message = f"{title} generated {count_text}."
    return PlayerJobResult(message=message, context=context)


def recommendation_track_ids(
    results: tuple[RecommendationResult, ...],
) -> list[int]:
    return [
        result.candidate.metadata.track_id
        for result in results
        if result.candidate.metadata.track_id > 0
    ]


def recommendation_playlist_job_context(
    *,
    title: str,
    source_text: str,
    mode: str,
    limit: int,
    track_ids: list[int] | None = None,
) -> dict[str, object]:
    context: dict[str, object] = {
        "operation": title,
        "playlist": source_text,
        "mode": mode,
        "limit": limit,
    }
    if track_ids is not None:
        context["tracks_generated"] = len(track_ids)
        context["queue_track_ids"] = track_ids
    return context


def recommendation_playlist_title(kind: RecommendationPlaylistKind | str) -> str:
    labels = {
        "track_radio": "Track Radio",
        "album_radio": "Album Radio",
        "artist_radio": "Artist Radio",
    }
    try:
        return labels[str(kind)]
    except KeyError as error:
        raise ValueError(f"unsupported recommendation playlist kind: {kind}") from error


def recommendation_playlist_source_text(
    database: Path,
    kind: RecommendationPlaylistKind,
    seed: object | None,
) -> str:
    queries = LibraryQueries(database)
    if kind == "track_radio":
        track = queries.get_track(int(seed))
        title = track.title or Path(track.path).name
        artist = track.artist or track.album_artist
        return f"{artist} - {title}" if artist else title
    if kind == "album_radio":
        album = queries.get_album(str(seed))
        title = display_album_title(album.album)
        return f"{album.artist} - {title}" if album.artist else title
    if kind == "artist_radio":
        return str(seed or "").strip()
    raise ValueError(f"unsupported recommendation playlist kind: {kind}")
