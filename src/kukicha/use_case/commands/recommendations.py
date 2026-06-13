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
    RECOMMENDATION_MODE_ARTIST_ONLY,
    RECOMMENDATION_MODE_DEFAULT,
    RECOMMENDATION_MODE_DISCOVERY,
    RecommendationResult,
    RecommendationService,
    RecommendationModeError,
    normalize_recommendation_mode,
)

RecommendationPlaylistKind = Literal[
    "track_radio",
    "album_radio",
    "artist_radio",
    "genre_radio",
    "random_playlist",
]

RECOMMENDATION_PLAYLIST_JOB_KIND = "generate_playlist"


def start_recommendation_playlist(
    runtime: PlayerRuntime,
    kind: RecommendationPlaylistKind,
    seed: object | None = None,
    *,
    mode: object | None = None,
) -> dict[str, object]:
    normalized_mode = recommendation_playlist_mode(kind, mode)
    normalized_limit = runtime_recommendation_limit(runtime)
    request = {
        "kind": kind,
        "seed": seed,
        "limit": normalized_limit,
    }
    if normalized_mode is not None:
        request["mode"] = normalized_mode
    source_text = recommendation_playlist_source_text(
        runtime.database,
        kind,
        seed,
    )
    title = recommendation_playlist_title(kind, source_text)
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
    limit = int(request["limit"])
    if kind == "track_radio":
        mode = str(request["mode"])
        results = service.get_track_radio(int(request["seed"]), mode=mode, limit=limit)
    elif kind == "album_radio":
        mode = str(request["mode"])
        results = service.get_album_radio(str(request["seed"]), mode=mode, limit=limit)
    elif kind == "artist_radio":
        mode = str(request["mode"])
        results = service.get_artist_radio(str(request["seed"]), mode=mode, limit=limit)
    elif kind == "genre_radio":
        mode = str(request["mode"])
        results = service.get_genre_radio(str(request["seed"]), mode=mode, limit=limit)
    elif kind == "random_playlist":
        mode = str(request["mode"])
        results = service.get_random_playlist(mode=mode, limit=limit)
    else:
        raise ValueError(f"unsupported recommendation playlist kind: {kind}")
    cancel_token.raise_if_canceled()

    track_ids = recommendation_track_ids(results)
    context = recommendation_playlist_job_context(
        title=title,
        source_text=source_text,
        mode=request.get("mode"),
        limit=limit,
        track_ids=track_ids,
    )
    count_text = format_count_label(len(track_ids), "track", "tracks")
    if source_text and kind in {"track_radio", "album_radio", "artist_radio"}:
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
    mode: object | None,
    limit: int,
    track_ids: list[int] | None = None,
) -> dict[str, object]:
    context: dict[str, object] = {
        "operation": title,
        "playlist": source_text,
        "limit": limit,
    }
    if mode is not None:
        context["mode"] = str(mode)
    if track_ids is not None:
        context["tracks_generated"] = len(track_ids)
        context["queue_track_ids"] = track_ids
    return context


def runtime_recommendation_limit(runtime: PlayerRuntime) -> int:
    try:
        raw_limit = getattr(runtime, "radio_limit")
    except AttributeError:
        raw_limit = RECOMMENDATION_CONFIG.default_limit
    try:
        return RECOMMENDATION_CONFIG.normalize_limit(raw_limit)
    except (TypeError, ValueError):
        return RECOMMENDATION_CONFIG.default_limit


def recommendation_playlist_mode(
    kind: RecommendationPlaylistKind | str,
    mode: object | None,
) -> str | None:
    if str(kind) in {"track_radio", "album_radio", "artist_radio"}:
        return normalize_recommendation_mode(mode)
    if str(kind) in {"genre_radio", "random_playlist"}:
        normalized_mode = normalize_recommendation_mode(mode)
        if normalized_mode == RECOMMENDATION_MODE_ARTIST_ONLY:
            supported = ", ".join(
                repr(value)
                for value in (
                    RECOMMENDATION_MODE_DEFAULT,
                    RECOMMENDATION_MODE_DISCOVERY,
                )
            )
            raise RecommendationModeError(
                f"unsupported {kind} recommendation mode: {normalized_mode!r}; "
                f"expected one of: {supported}"
            )
        return normalized_mode
    return None


def recommendation_playlist_title(
    kind: RecommendationPlaylistKind | str,
    source_text: object | None = None,
) -> str:
    if str(kind) == "genre_radio":
        genre = str(source_text or "").strip()
        return f"{genre} Radio" if genre else "Genre Radio"
    labels = {
        "track_radio": "Track Radio",
        "album_radio": "Album Radio",
        "artist_radio": "Artist Radio",
        "random_playlist": "Random Playlist",
    }
    label = labels.get(str(kind))
    if label is None:
        raise ValueError(f"unsupported recommendation playlist kind: {kind}")
    return label


def recommendation_playlist_source_text(
    database: Path,
    kind: RecommendationPlaylistKind,
    seed: object | None,
) -> str:
    if kind == "track_radio":
        queries = LibraryQueries(database)
        track = queries.get_track(int(seed))
        title = track.title or Path(track.path).name
        artist = track.artist or track.album_artist
        return f"{artist} - {title}" if artist else title
    if kind == "album_radio":
        queries = LibraryQueries(database)
        album = queries.get_album(str(seed))
        title = display_album_title(album.album)
        return f"{album.artist} - {title}" if album.artist else title
    if kind == "artist_radio":
        return str(seed or "").strip()
    if kind == "genre_radio":
        return str(seed or "").strip()
    if kind == "random_playlist":
        return ""
    raise ValueError(f"unsupported recommendation playlist kind: {kind}")
