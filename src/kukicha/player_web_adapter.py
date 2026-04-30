from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
import json
from pathlib import Path
from queue import Empty, Queue
import re
import sqlite3
from typing import Any

from flask import Flask, Response, abort, current_app, redirect, request, stream_with_context
from werkzeug.serving import make_server

from .use_case import (
    delete_stale_album_musicbrainz_override,
    mark_stale_player_jobs_canceled,
    playlist_audio_path,
    save_album_artist_split_mapping,
    start_add_root,
    start_album_musicbrainz_edit,
    start_album_tag_edit,
    start_delete_root,
    start_rescan_library,
    track_artwork,
    track_audio_path,
    update_playback as update_playback_command,
    update_queue as update_queue_command,
    update_track_playlist_membership as update_track_playlist_membership_command,
)
from .use_case import (
    AlbumNotFoundError,
    PlaylistItemNotFoundError,
    PlaylistNotFoundError,
    TrackNotFoundError,
)
from .player_config import (
    LOGGER,
    PlayerServerOptions,
    build_template_environment,
    player_accent_color,
    validate_player_startup,
)
from .player_errors import PlayerConfigError, PlayerConflictError, PlayerNotFoundError
from .player_media import audio_mime_type
from .player_navigation import PLAYER_PAGE_ROUTE_KEYS
from .player_platform import (
    register_player_signal_handlers,
    restore_signal_handlers,
)
from .player_runtime import PlayerRuntime
from .player_views import (
    album_playback_payload,
    build_album_context,
    build_album_edit_context,
    build_artist_split_rules_page_context,
    build_artists_page_context,
    build_cache_page_context,
    build_help_page_context,
    build_index_context,
    build_jobs_page_context,
    build_musicbrainz_overrides_page_context,
    build_playlist_context,
    build_playlist_index_context,
    build_queue_context,
    build_roots_page_context,
    build_simple_page_context,
    playlist_playback_payload,
)

BYTE_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)$")
CHUNK_SIZE = 1024 * 512
MAX_POST_BYTES = 1024 * 64
FRAGMENT_HEADER = "X-Kukicha-Fragment"
PLAYER_CONTEXT_KEY = "kukicha_player_context"
STATIC_CONTENT_TYPES = {
    "player.css": "text/css; charset=utf-8",
    "player.js": "application/javascript; charset=utf-8",
    "favicon.svg": "image/svg+xml",
}


@dataclass(frozen=True, slots=True)
class PlayerWebContext:
    options: PlayerServerOptions
    runtime: PlayerRuntime
    database: Path


def parse_byte_range(header: str | None, file_size: int) -> tuple[int, int] | None:
    if not header:
        return None

    match = BYTE_RANGE_RE.fullmatch(header.strip())
    if not match:
        return None

    start_text, end_text = match.groups()
    if not start_text and not end_text:
        return None

    if start_text:
        start = int(start_text)
        end = int(end_text) if end_text else file_size - 1
    else:
        suffix_length = int(end_text)
        if suffix_length <= 0:
            return None
        start = max(file_size - suffix_length, 0)
        end = file_size - 1

    end = min(end, file_size - 1)
    if start < 0 or start > end or start >= file_size:
        return None
    return start, end


def create_player_app(options: PlayerServerOptions) -> Flask:
    app = Flask("kukicha", static_folder=None)
    template_environment = build_template_environment()
    app.jinja_env.filters.update(template_environment.filters)
    runtime = PlayerRuntime(options)
    mark_stale_player_jobs_canceled(runtime.database)
    app.extensions[PLAYER_CONTEXT_KEY] = PlayerWebContext(
        options=options,
        runtime=runtime,
        database=runtime.database,
    )

    @app.context_processor
    def inject_player_theme() -> dict[str, object]:
        return {"accent_color": player_accent_color(options.accent_color)}

    @app.errorhandler(PlayerConflictError)
    def handle_conflict(error: PlayerConflictError) -> Response:
        return json_response({"error": str(error)}, status=409)

    @app.errorhandler(PlayerNotFoundError)
    @app.errorhandler(AlbumNotFoundError)
    @app.errorhandler(PlaylistNotFoundError)
    @app.errorhandler(PlaylistItemNotFoundError)
    @app.errorhandler(TrackNotFoundError)
    def handle_not_found(error: Exception) -> Response:
        if not wants_json_error():
            abort(404, str(error))
        return json_response({"error": str(error)}, status=404)

    @app.errorhandler(ValueError)
    def handle_bad_request(error: ValueError) -> Response:
        if not wants_json_error():
            abort(400, str(error))
        return json_response({"error": str(error)}, status=400)

    @app.errorhandler(sqlite3.Error)
    @app.errorhandler(OSError)
    def handle_server_error(error: Exception) -> Response:
        if not wants_json_error():
            abort(500, str(error))
        return json_response({"error": str(error)}, status=500)

    @app.get("/")
    @app.get("/index.html")
    def index() -> Response:
        reset_playback_for_document_load()
        return rendered_response(build_index_context(player_context().runtime, query_string()))

    @app.get("/api/jobs/events")
    def job_events() -> Response:
        return job_events_response(player_context().runtime)

    @app.get("/api/albums/<path:album_id>/playback")
    def album_playback(album_id: str) -> Response:
        payload = album_playback_payload(player_context().database, album_id, query_string())
        return json_response(payload, cache_control="private, max-age=60")

    @app.get("/api/playlists/<int:playlist_id>/playback")
    def playlist_playback(playlist_id: int) -> Response:
        payload = playlist_playback_payload(player_context().database, playlist_id)
        return json_response(payload, cache_control="private, max-age=60")

    @app.get("/healthz")
    def healthz() -> Response:
        return Response(status=204)

    @app.get("/albums/<path:album_id>/edit")
    def album_edit(album_id: str) -> Response:
        reset_playback_for_document_load()
        return rendered_response(
            build_album_edit_context(player_context().runtime, album_id, query_string())
        )

    @app.get("/albums/<path:album_id>")
    def album(album_id: str) -> Response:
        reset_playback_for_document_load()
        return rendered_response(
            build_album_context(player_context().runtime, album_id, query_string())
        )

    @app.get("/playlists/<int:playlist_id>")
    def playlist(playlist_id: int) -> Response:
        reset_playback_for_document_load()
        return rendered_response(
            build_playlist_context(player_context().runtime, playlist_id, query_string())
        )

    @app.get("/queue")
    def queue_page() -> Response:
        reset_playback_for_document_load()
        return rendered_response(build_queue_context(player_context().runtime))

    @app.get("/audio/<int:track_id>")
    def audio(track_id: int) -> Response:
        return audio_file_response(track_audio_path(player_context().runtime, track_id))

    @app.get("/playlist-audio/<int:playlist_item_id>")
    def playlist_audio(playlist_item_id: int) -> Response:
        try:
            path = playlist_audio_path(player_context().runtime, playlist_item_id)
        except FileNotFoundError:
            abort(404, "Playlist URL audio uses its source URL directly")
        return audio_file_response(path)

    @app.get("/art/<int:track_id>")
    def track_artwork(track_id: int) -> Response:
        from .models import TRACK_ARTWORK_HEIGHT

        return artwork_response(TRACK_ARTWORK_HEIGHT, track_id)

    @app.get("/art/<int:height_px>/<int:track_id>")
    def sized_artwork(height_px: int, track_id: int) -> Response:
        from .models import ALBUM_ARTWORK_HEIGHT, TRACK_ARTWORK_HEIGHT

        if height_px not in {TRACK_ARTWORK_HEIGHT, ALBUM_ARTWORK_HEIGHT}:
            abort(404)
        return artwork_response(height_px, track_id)

    @app.get("/static/<path:name>")
    def static_file(name: str) -> Response:
        return static_response(name)

    @app.get("/favicon.ico")
    def favicon() -> Response:
        return static_response("favicon.svg")

    @app.post("/api/albums/<path:album_id>/musicbrainz")
    def edit_album_musicbrainz(album_id: str) -> Response:
        payload = read_json_body()
        result = start_album_musicbrainz_edit(player_context().runtime, album_id, payload)
        return json_response(result, status=202)

    @app.post("/api/musicbrainz-overrides/<path:album_id>/delete")
    def delete_musicbrainz_override(album_id: str) -> Response:
        return json_response(
            delete_stale_album_musicbrainz_override(player_context().runtime, album_id)
        )

    @app.post("/api/albums/<path:album_id>/tags")
    def edit_album_tags(album_id: str) -> Response:
        payload = read_json_body()
        result = start_album_tag_edit(player_context().runtime, album_id, payload)
        return json_response(result, status=202)

    @app.post("/api/roots")
    def add_root() -> Response:
        payload = read_json_body()
        result = start_add_root(player_context().runtime, str(payload.get("path", "")))
        return json_response(result, status=202)

    @app.post("/api/roots/rescan")
    def rescan_library() -> Response:
        return json_response(start_rescan_library(player_context().runtime), status=202)

    @app.post("/api/roots/<int:position>/delete")
    def delete_root(position: int) -> Response:
        return json_response(start_delete_root(player_context().runtime, position), status=202)

    @app.post("/api/album-artist-mappings")
    def edit_album_artist_split_mapping() -> Response:
        result = save_album_artist_split_mapping(
            player_context().runtime,
            read_json_body(),
        )
        return json_response(result)

    @app.post("/api/tracks/<int:track_id>/playlists/<int:playlist_id>")
    def update_track_playlist_membership(track_id: int, playlist_id: int) -> Response:
        payload = read_json_body()
        result = update_track_playlist_membership_command(
            player_context().runtime,
            track_id,
            playlist_id,
            payload,
        )
        return json_response(result)

    @app.post("/api/queue")
    def update_queue() -> Response:
        return json_response(update_queue_command(player_context().runtime, read_json_body()))

    @app.post("/api/playback")
    def update_playback() -> Response:
        return json_response(update_playback_command(player_context().runtime, read_json_body()))

    @app.post("/api/jobs/<int:job_id>/cancel")
    def cancel_job(job_id: int) -> Response:
        from .player_jobs import job_payload

        job = player_context().runtime.cancel_job(job_id)
        return json_response({"job": job_payload(job)})

    @app.get("/settings")
    def legacy_settings_page() -> Response:
        return redirect("/roots")

    for route, page_key in PLAYER_PAGE_ROUTE_KEYS.items():
        endpoint = f"page_{page_key}"
        if page_key == "roots":
            app.add_url_rule(
                route,
                endpoint,
                lambda page_key=page_key: render_roots_page(),
                methods=["GET"],
            )
        elif page_key == "artist-split-rules":
            app.add_url_rule(
                route,
                endpoint,
                lambda page_key=page_key: render_artist_split_rules_page(),
                methods=["GET"],
            )
        elif page_key == "cache":
            app.add_url_rule(
                route,
                endpoint,
                lambda page_key=page_key: render_cache_page(),
                methods=["GET"],
            )
        elif page_key == "musicbrainz-overrides":
            app.add_url_rule(
                route,
                endpoint,
                lambda page_key=page_key: render_musicbrainz_overrides_page(),
                methods=["GET"],
            )
        elif page_key == "artists":
            app.add_url_rule(
                route,
                endpoint,
                lambda page_key=page_key: render_artists_page(),
                methods=["GET"],
            )
        elif page_key == "playlists":
            app.add_url_rule(
                route,
                endpoint,
                lambda page_key=page_key: render_playlist_index_page(),
                methods=["GET"],
            )
        elif page_key == "jobs":
            app.add_url_rule(
                route,
                endpoint,
                lambda page_key=page_key: render_jobs_page(),
                methods=["GET"],
            )
        elif page_key == "help":
            app.add_url_rule(
                route,
                endpoint,
                lambda page_key=page_key: render_help_page(),
                methods=["GET"],
            )
        else:
            app.add_url_rule(
                route,
                endpoint,
                lambda page_key=page_key: render_simple_page(page_key),
                methods=["GET"],
            )

    return app


def serve_player(options: PlayerServerOptions) -> int:
    try:
        validate_player_startup(options)
    except PlayerConfigError as error:
        LOGGER.error("%s", error)
        return 1

    app = create_player_app(options)
    try:
        server = make_server(options.host, options.port, app, threaded=True)
    except OSError as error:
        LOGGER.error(
            "failed to bind player server on %s:%s: %s",
            options.host,
            options.port,
            error,
        )
        return 1

    url = f"http://{options.host}:{server.server_port}/"
    LOGGER.info("using config file %s", options.config_path)
    LOGGER.info("kukicha listening on %s", url)
    stop_reason = {"value": "received interrupt"}
    previous_handlers = register_player_signal_handlers(stop_reason)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("stopping player server: %s", stop_reason["value"])
    finally:
        server.server_close()
        restore_signal_handlers(previous_handlers)
        LOGGER.info("player server stopped")
    return 0


def player_context() -> PlayerWebContext:
    return current_app.extensions[PLAYER_CONTEXT_KEY]


def wants_json_error() -> bool:
    return request.path.startswith("/api/") or request.method == "POST"


def query_string() -> str:
    return request.query_string.decode("utf-8")


def wants_fragment() -> bool:
    if request.headers.get(FRAGMENT_HEADER) == "1":
        return True
    return "text/vnd.kukicha.fragment+html" in request.headers.get("Accept", "")


def reset_playback_for_document_load() -> None:
    if request.method == "HEAD" or wants_fragment():
        return
    player_context().runtime.reset_queue_state()


def render_roots_page() -> Response:
    reset_playback_for_document_load()
    return rendered_response(build_roots_page_context(player_context().runtime))


def render_artist_split_rules_page() -> Response:
    reset_playback_for_document_load()
    return rendered_response(build_artist_split_rules_page_context(player_context().runtime))


def render_cache_page() -> Response:
    reset_playback_for_document_load()
    return rendered_response(build_cache_page_context(player_context().runtime))


def render_musicbrainz_overrides_page() -> Response:
    reset_playback_for_document_load()
    return rendered_response(
        build_musicbrainz_overrides_page_context(player_context().runtime)
    )


def render_artists_page() -> Response:
    reset_playback_for_document_load()
    return rendered_response(build_artists_page_context(player_context().runtime))


def render_playlist_index_page() -> Response:
    reset_playback_for_document_load()
    return rendered_response(
        build_playlist_index_context(player_context().runtime, query_string())
    )


def render_jobs_page() -> Response:
    reset_playback_for_document_load()
    return rendered_response(build_jobs_page_context(player_context().runtime))


def render_help_page() -> Response:
    reset_playback_for_document_load()
    context = player_context()
    return rendered_response(build_help_page_context(context.runtime, context.options))


def render_simple_page(page_key: str) -> Response:
    reset_playback_for_document_load()
    return rendered_response(build_simple_page_context(player_context().runtime, page_key))


def rendered_response(context: dict[str, Any]) -> Response:
    from flask import render_template

    template_name = context["view_template"] if wants_fragment() else "player/base.html"
    html = render_template(template_name, **context)
    return Response(html, content_type="text/html; charset=utf-8")


def json_response(
    payload: object,
    *,
    status: int = 200,
    cache_control: str | None = None,
) -> Response:
    response = Response(
        json.dumps(payload, sort_keys=True),
        status=status,
        content_type="application/json; charset=utf-8",
    )
    if cache_control:
        response.headers["Cache-Control"] = cache_control
    return response


def read_json_body() -> dict[str, Any]:
    try:
        length = int(request.headers.get("Content-Length", "0") or "0")
    except ValueError as error:
        raise ValueError("invalid Content-Length") from error
    if length > MAX_POST_BYTES:
        raise ValueError("request body too large")
    raw = request.get_data(cache=False) if length else b"{}"
    try:
        payload = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError("invalid JSON") from error
    if not isinstance(payload, dict):
        raise ValueError("expected a JSON object")
    return payload


def audio_file_response(path: Path) -> Response:
    if not path.is_file():
        abort(404, "Audio file not found")

    file_size = path.stat().st_size
    if file_size <= 0:
        abort(404, "Audio file is empty")

    byte_range = parse_byte_range(request.headers.get("Range"), file_size)
    if byte_range is None:
        start, end = 0, file_size - 1
        status = 200
    else:
        start, end = byte_range
        status = 206

    length = end - start + 1
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
    }
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"

    if request.method == "HEAD":
        return Response(
            status=status,
            headers=headers,
            content_type=audio_mime_type(path),
        )

    def stream_file() -> Any:
        try:
            with path.open("rb") as handle:
                handle.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = handle.read(min(CHUNK_SIZE, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk
        except (BrokenPipeError, ConnectionResetError):
            return

    return Response(
        stream_file(),
        status=status,
        headers=headers,
        content_type=audio_mime_type(path),
        direct_passthrough=True,
    )


def artwork_response(height_px: int, track_id: int) -> Response:
    artwork = track_artwork(player_context().runtime, height_px, track_id)
    if artwork is None:
        abort(404)
    response = Response(artwork.data, content_type=artwork.mime_type)
    response.headers["Cache-Control"] = "private, max-age=3600"
    return response


def static_response(name: str) -> Response:
    content_type = STATIC_CONTENT_TYPES.get(name)
    if content_type is None:
        abort(404)
    resource = files("kukicha").joinpath("static", name)
    try:
        data = resource.read_bytes()
    except FileNotFoundError:
        abort(404)
    response = Response(data, content_type=content_type)
    response.headers["Cache-Control"] = "private, max-age=60"
    return response


def job_events_response(runtime: PlayerRuntime) -> Response:
    def generate() -> Any:
        subscriber: Queue[dict[str, object]] = Queue()
        runtime.subscribe_jobs(subscriber)
        try:
            yield b"retry: 1000\n\n"
            while True:
                try:
                    payload = subscriber.get(timeout=15.0)
                except Empty:
                    yield b": keepalive\n\n"
                    continue

                yield b"event: job\n"
                yield b"data: "
                yield json.dumps(payload, sort_keys=True).encode("utf-8")
                yield b"\n\n"
        except (BrokenPipeError, ConnectionResetError, GeneratorExit):
            return
        finally:
            runtime.unsubscribe_jobs(subscriber)

    response = Response(
        stream_with_context(generate()),
        content_type="text/event-stream; charset=utf-8",
    )
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Connection"] = "keep-alive"
    return response
