from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
from queue import Empty, Queue
import re
import sqlite3
from typing import Any
from urllib.parse import quote

from flask import Flask, Response, abort, current_app, redirect, request, stream_with_context
from werkzeug.exceptions import NotFound
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.serving import make_server

from ._compat import UTC
from .use_case import (
    NATIVE_PLAYBACK_SOURCE,
    append_queue as append_queue_command,
    clear_cache_tables,
    create_or_replace_manual_playlist,
    delete_playlist as delete_playlist_command,
    delete_album_metadata_override,
    import_playlist_file,
    mark_stale_player_jobs_canceled,
    pause_queue_for_document_load,
    playlist_cover as playlist_cover_command,
    playlist_audio_resource,
    record_playback,
    remove_queue_item as remove_queue_item_command,
    reset_listening_data,
    save_album_artist_split_mapping,
    start_album_cover_upload,
    start_album_delete,
    start_album_edit,
    start_rescan_library,
    start_sync,
    track_artwork,
    track_audio_path,
    track_audio_resource,
    update_album_star as update_album_star_command,
    upload_playlist_cover as upload_playlist_cover_command,
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
    player_theme_context,
    validate_player_startup,
)
from .player_auth import signed_auth_cookie, verify_auth_cookie, verify_password
from .player_common import ARTWORK_CACHE_CONTROL
from .player_errors import PlayerConfigError, PlayerConflictError, PlayerNotFoundError
from .library_sources import resolve_remote_worker_count
from .media_resources import AudioResource, local_audio_resource
from .player_media import audio_resource_head, iter_audio_resource_bytes
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
    build_home_context,
    build_index_context,
    build_jobs_page_context,
    build_listening_data_page_context,
    build_metadata_overrides_page_context,
    build_not_found_context,
    build_playlist_context,
    build_playlist_edit_context,
    build_playlist_index_context,
    build_queue_context,
    build_roots_page_context,
    build_search_context,
    build_simple_page_context,
    playlist_playback_payload,
)
from .static_assets import (
    HTML_CACHE_CONTROL,
    STATIC_ASSET_CACHE_CONTROL,
    STATIC_COMPAT_CACHE_CONTROL,
    resolve_static_asset_request,
)

BYTE_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)$")
MAX_POST_BYTES = 1024 * 64
MAX_COVER_UPLOAD_BYTES = 1024 * 1024 * 25
MAX_PLAYLIST_UPLOAD_BYTES = 1024 * 1024 * 5
FRAGMENT_HEADER = "X-Kukicha-Fragment"
PLAYER_CONTEXT_KEY = "kukicha_player_context"


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
    if options.trusted_proxy_headers:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    template_environment = build_template_environment()
    app.jinja_env.filters.update(template_environment.filters)
    app.jinja_env.globals.update(template_environment.globals)
    runtime = PlayerRuntime(options)
    mark_stale_player_jobs_canceled(runtime.database)
    if type(runtime) is PlayerRuntime:
        runtime.library_filter_options()
    app.extensions[PLAYER_CONTEXT_KEY] = PlayerWebContext(
        options=options,
        runtime=runtime,
        database=runtime.database,
    )

    @app.context_processor
    def inject_player_theme() -> dict[str, object]:
        return player_theme_context(options.accent_color, options.appearance)

    @app.before_request
    def require_player_login() -> Response | None:
        auth = player_context().options.auth
        if auth is None or is_public_path():
            return None
        if verify_auth_cookie(auth, request.cookies.get(auth.cookie_name)):
            return None
        if should_return_auth_unauthorized():
            return auth_required_response()
        return redirect(login_url(), code=302)

    @app.get("/login")
    def login_page() -> Response:
        return login_response()

    @app.post("/login")
    def login_submit() -> Response:
        auth = player_context().options.auth
        if auth is None:
            return redirect(safe_next_url(request.values.get("next")), code=302)
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        next_url = safe_next_url(request.values.get("next"))
        if username != auth.username or not verify_password(auth, password):
            return login_response(error="Invalid username or password.", status=401)
        response = redirect(next_url, code=302)
        response.set_cookie(
            auth.cookie_name,
            signed_auth_cookie(auth),
            max_age=auth.cookie_max_age_seconds,
            httponly=True,
            samesite="Strict",
        )
        return response

    @app.errorhandler(PlayerConflictError)
    def handle_conflict(error: PlayerConflictError) -> Response:
        return json_response({"error": str(error)}, status=409)

    @app.errorhandler(PlayerNotFoundError)
    @app.errorhandler(AlbumNotFoundError)
    @app.errorhandler(PlaylistNotFoundError)
    @app.errorhandler(PlaylistItemNotFoundError)
    @app.errorhandler(TrackNotFoundError)
    def handle_not_found(error: Exception) -> Response:
        return not_found_response(error_message(error))

    @app.errorhandler(NotFound)
    def handle_http_not_found(error: NotFound) -> Response:
        return not_found_response(http_not_found_message(error))

    @app.errorhandler(ValueError)
    def handle_bad_request(error: ValueError) -> Response:
        if not wants_json_error():
            return error_response(error, status=400)
        return json_response({"error": error_message(error)}, status=400)

    @app.errorhandler(sqlite3.Error)
    @app.errorhandler(OSError)
    def handle_server_error(error: Exception) -> Response:
        if not wants_json_error():
            return error_response(error, status=500)
        return json_response({"error": error_message(error)}, status=500)

    @app.get("/")
    @app.get("/index.html")
    def home() -> Response:
        if request.path == "/" and query_string():
            return redirect(f"/albums?{query_string()}", code=302)
        reset_playback_for_document_load()
        return rendered_response(build_home_context(player_context().runtime))

    @app.get("/albums")
    def index() -> Response:
        reset_playback_for_document_load()
        return rendered_response(build_index_context(player_context().runtime, query_string()))

    @app.get("/search")
    def search_page() -> Response:
        reset_playback_for_document_load()
        return rendered_response(build_search_context(player_context().runtime, query_string()))

    @app.get("/api/jobs/events")
    def job_events() -> Response:
        return job_events_response(player_context().runtime)

    @app.get("/api/albums/<path:album_id>/playback")
    def album_playback(album_id: str) -> Response:
        payload = album_playback_payload(player_context().database, album_id)
        return json_response(payload, cache_control="private, max-age=60")

    @app.get("/api/playlists/<int:playlist_id>/playback")
    def playlist_playback(playlist_id: int) -> Response:
        payload = playlist_playback_payload(player_context().database, playlist_id)
        return json_response(payload, cache_control="private, max-age=60")

    @app.get("/api/playlists/<int:playlist_id>/cover")
    def playlist_cover(playlist_id: int) -> Response:
        return playlist_cover_response(playlist_id)

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

    @app.get("/playlists/<int:playlist_id>/edit")
    def playlist_edit(playlist_id: int) -> Response:
        reset_playback_for_document_load()
        return rendered_response(
            build_playlist_edit_context(player_context().runtime, playlist_id, query_string())
        )

    @app.get("/queue")
    def queue_page() -> Response:
        reset_playback_for_document_load()
        return rendered_response(build_queue_context(player_context().runtime))

    @app.get("/audio/<int:track_id>")
    def audio(track_id: int) -> Response:
        try:
            resource = track_audio_resource(player_context().runtime, track_id)
        except TrackNotFoundError:
            return audio_file_response(track_audio_path(player_context().runtime, track_id))
        return audio_resource_response(resource)

    @app.get("/playlist-audio/<int:playlist_item_id>")
    def playlist_audio(playlist_item_id: int) -> Response:
        try:
            resource = playlist_audio_resource(player_context().runtime, playlist_item_id)
        except FileNotFoundError:
            abort(404, "Playlist URL audio uses its source URL directly")
        return audio_resource_response(resource)

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

    @app.post("/api/metadata-overrides/<path:album_id>/delete")
    def delete_metadata_override(album_id: str) -> Response:
        return json_response(
            delete_album_metadata_override(player_context().runtime, album_id)
        )

    @app.post("/api/cache/<cache_key>/clear")
    def clear_cache(cache_key: str) -> Response:
        return json_response(clear_cache_tables(player_context().runtime, cache_key))

    @app.post("/api/listening-data/reset")
    def reset_player_listening_data() -> Response:
        return json_response(reset_listening_data(player_context().database))

    @app.post("/api/albums/<path:album_id>/edit")
    def edit_album(album_id: str) -> Response:
        payload = read_json_body()
        result = start_album_edit(player_context().runtime, album_id, payload)
        return json_response(result, status=202)

    @app.post("/api/albums/<path:album_id>/cover")
    def upload_album_cover(album_id: str) -> Response:
        filename, data = read_cover_upload()
        result = start_album_cover_upload(
            player_context().runtime,
            album_id,
            filename=filename,
            data=data,
        )
        return json_response(result, status=202)

    @app.post("/api/albums/<path:album_id>/delete")
    def delete_album(album_id: str) -> Response:
        result = start_album_delete(player_context().runtime, album_id)
        return json_response(result, status=202)

    @app.post("/api/albums/<path:album_id>/star")
    def update_album_star(album_id: str) -> Response:
        result = update_album_star_command(
            player_context().runtime,
            album_id,
            read_json_body(),
        )
        return json_response(result)

    @app.post("/api/roots/rescan")
    def rescan_library() -> Response:
        return json_response(start_rescan_library(player_context().runtime), status=202)

    @app.post("/api/playlists")
    def create_playlist() -> Response:
        payload = read_json_body()
        track_ids = payload.get("track_ids") or ()
        if not isinstance(track_ids, list | tuple):
            raise ValueError("track_ids must be a list")
        playlist_id_value = payload.get("playlist_id")
        playlist_id = int(playlist_id_value) if playlist_id_value not in (None, "") else None
        result = create_or_replace_manual_playlist(
            player_context().database,
            name=str(payload.get("name") or ""),
            track_ids=tuple(int(track_id) for track_id in track_ids),
            playlist_id=playlist_id,
        )
        action = "updated" if playlist_id is not None else "created"
        return json_response(
            result.payload(message=f"Playlist {action}: {result.name}."),
            status=200 if playlist_id is not None else 201,
        )

    @app.post("/api/playlists/import")
    def import_playlist() -> Response:
        filename, data, name = read_playlist_upload()
        result = import_playlist_file(
            player_context().database,
            filename=filename,
            data=data,
            name=name,
        )
        skipped_count = len(result.skipped_relative_paths)
        message = (
            f"Imported {result.item_count} playlist item(s); "
            f"skipped {skipped_count} relative path(s)."
            if skipped_count
            else f"Imported {result.item_count} playlist item(s)."
        )
        return json_response(result.payload(message=message), status=201)

    @app.post("/api/playlists/<int:playlist_id>/cover")
    def upload_playlist_cover(playlist_id: int) -> Response:
        filename, data = read_cover_upload()
        result = upload_playlist_cover_command(
            player_context().database,
            playlist_id,
            filename=filename,
            data=data,
        )
        return json_response(
            result.payload(message=f"Playlist cover updated for {result.name}.")
        )

    @app.post("/api/playlists/<int:playlist_id>/delete")
    def delete_playlist(playlist_id: int) -> Response:
        result = delete_playlist_command(player_context().database, playlist_id)
        return json_response(
            result.payload(message=f"Playlist deleted: {result.name}.")
            | {"redirect_url": "/playlists"}
        )

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

    @app.post("/api/queue/append")
    def append_queue() -> Response:
        return json_response(append_queue_command(player_context().runtime, read_json_body()))

    @app.post("/api/queue/remove")
    def remove_queue_item() -> Response:
        return json_response(remove_queue_item_command(player_context().runtime, read_json_body()))

    @app.post("/api/scrobble")
    def scrobble() -> Response:
        payload = read_json_body()
        try:
            playback_id = int(payload.get("playback_id"))
        except (TypeError, ValueError) as error:
            raise ValueError("invalid playback id") from error
        record_playback(
            player_context().database,
            playback_id,
            submission=bool_payload_value(payload.get("submission", True)),
            played_at=epoch_millis_payload_time(payload.get("time")),
            source=NATIVE_PLAYBACK_SOURCE,
        )
        return json_response({})

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
        elif page_key == "listening-data":
            app.add_url_rule(
                route,
                endpoint,
                lambda page_key=page_key: render_listening_data_page(),
                methods=["GET"],
            )
        elif page_key == "metadata-overrides":
            app.add_url_rule(
                route,
                endpoint,
                lambda page_key=page_key: render_metadata_overrides_page(),
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

    if options.opensubsonic is not None:
        from .opensubsonic_web_adapter import mount_open_subsonic

        mount_open_subsonic(
            app,
            options=options,
            runtime=runtime,
            database=runtime.database,
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

    start_player_sync(app)
    url = f"http://{options.host}:{server.server_port}/"
    LOGGER.info("using config file %s", options.config_path)
    LOGGER.info(
        "remote workers: %s (%s)",
        resolve_remote_worker_count(options.remote_workers),
        "configured" if options.remote_workers is not None else "auto",
    )
    LOGGER.info("kukicha listening on %s", url)
    if options.opensubsonic is not None:
        client_url = url
        if options.opensubsonic.mount_prefix != "/":
            client_url = f"{url.rstrip('/')}{options.opensubsonic.mount_prefix}"
        LOGGER.info(
            "OpenSubsonic server URL for clients: %s (mount prefix %s)",
            client_url,
            options.opensubsonic.mount_prefix,
        )
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


def start_player_sync(app: Flask) -> None:
    context = app.extensions[PLAYER_CONTEXT_KEY]
    start_sync(context.runtime, context.options.roots, context.options.remote_roots)


def player_context() -> PlayerWebContext:
    return current_app.extensions[PLAYER_CONTEXT_KEY]


def wants_json_error() -> bool:
    return request.path.startswith("/api/") or request.method == "POST"


def is_public_path() -> bool:
    if request.path in {"/login", "/healthz", "/favicon.ico"}:
        return True
    if (
        player_context().options.opensubsonic is None
        and (request.path == "/rest" or request.path.startswith("/rest/"))
    ):
        return True
    if is_mounted_open_subsonic_path():
        return True
    return request.path.startswith("/static/")


def is_mounted_open_subsonic_path() -> bool:
    options = player_context().options.opensubsonic
    if options is None:
        return False
    from .opensubsonic_web_adapter import open_subsonic_rest_prefix

    rest_prefix = open_subsonic_rest_prefix(options.mount_prefix)
    return request.path == rest_prefix or request.path.startswith(f"{rest_prefix}/")


def should_return_auth_unauthorized() -> bool:
    if request.method not in {"GET", "HEAD"}:
        return True
    return request.path.startswith(("/api/", "/audio/", "/playlist-audio/", "/art/"))


def auth_required_response() -> Response:
    if request.path.startswith("/api/"):
        return json_response({"error": "authentication required"}, status=401)
    return Response(
        "authentication required",
        status=401,
        content_type="text/plain; charset=utf-8",
    )


def login_url() -> str:
    next_url = request.full_path
    if next_url.endswith("?"):
        next_url = request.path
    return f"/login?next={quote(next_url, safe='')}"


def safe_next_url(value: str | None) -> str:
    if (
        value
        and value.startswith("/")
        and not value.startswith("//")
        and "\r" not in value
        and "\n" not in value
    ):
        return value
    return "/"


def login_response(error: str = "", *, status: int = 200) -> Response:
    from flask import render_template

    next_url = safe_next_url(request.values.get("next"))
    html = render_template(
        "player/login.html",
        app_title="Kukicha Login",
        page_name="login",
        error=error,
        next_url=next_url,
    )
    return html_response(html, status=status)


def query_string() -> str:
    return request.query_string.decode("utf-8")


def epoch_millis_payload_time(value: object) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        millis = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError("invalid scrobble time") from error
    return datetime.fromtimestamp(millis / 1000, tz=UTC)


def bool_payload_value(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return True
    if isinstance(value, int):
        return bool(value)
    text = str(value).strip().casefold()
    if text in {"1", "true", "yes"}:
        return True
    if text in {"0", "false", "no"}:
        return False
    raise ValueError("invalid scrobble submission")


def wants_fragment() -> bool:
    if request.headers.get(FRAGMENT_HEADER) == "1":
        return True
    return "text/vnd.kukicha.fragment+html" in request.headers.get("Accept", "")


def reset_playback_for_document_load() -> None:
    if request.method == "HEAD" or wants_fragment():
        return
    pause_queue_for_document_load(player_context().runtime)


def render_roots_page() -> Response:
    reset_playback_for_document_load()
    return rendered_response(build_roots_page_context(player_context().runtime))


def render_artist_split_rules_page() -> Response:
    reset_playback_for_document_load()
    return rendered_response(build_artist_split_rules_page_context(player_context().runtime))


def render_cache_page() -> Response:
    reset_playback_for_document_load()
    return rendered_response(build_cache_page_context(player_context().runtime))


def render_listening_data_page() -> Response:
    reset_playback_for_document_load()
    return rendered_response(build_listening_data_page_context(player_context().runtime))


def render_metadata_overrides_page() -> Response:
    reset_playback_for_document_load()
    return rendered_response(
        build_metadata_overrides_page_context(player_context().runtime)
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
    auth = context.options.auth
    return rendered_response(
        build_help_page_context(
            context.runtime,
            context.options,
            browser_cookie=request.cookies.get(auth.cookie_name) if auth else None,
            user_agent=request.headers.get("User-Agent", ""),
            client_ip=request.remote_addr or "",
        )
    )


def render_simple_page(page_key: str) -> Response:
    reset_playback_for_document_load()
    return rendered_response(build_simple_page_context(player_context().runtime, page_key))


def rendered_response(context: dict[str, Any], *, status: int = 200) -> Response:
    from flask import render_template

    template_name = context["view_template"] if wants_fragment() else "player/base.html"
    html = render_template(template_name, **context)
    return html_response(html, status=status)


def html_response(html: str, *, status: int = 200) -> Response:
    response = Response(html, status=status, content_type="text/html; charset=utf-8")
    response.headers["Cache-Control"] = HTML_CACHE_CONTROL
    return response


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


def not_found_response(message: str) -> Response:
    if wants_json_error():
        return json_response({"error": message}, status=404)
    return rendered_response(
        build_not_found_context(player_context().runtime, message),
        status=404,
    )


def http_not_found_message(error: NotFound) -> str:
    description = str(error.description).strip()
    if description and description != NotFound.description:
        return description
    return f"page not found: {request.path}"


def error_response(error: Exception, *, status: int) -> Response:
    return Response(
        error_message(error),
        status=status,
        content_type="text/plain; charset=utf-8",
    )


def error_message(error: Exception) -> str:
    if isinstance(error, KeyError) and error.args:
        return str(error.args[0])
    return str(error)


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


def read_cover_upload() -> tuple[str, bytes]:
    length = request.content_length
    if length is not None and length > MAX_COVER_UPLOAD_BYTES:
        raise ValueError("cover upload is too large")
    uploaded = request.files.get("cover")
    if uploaded is None:
        raise ValueError("cover file is required")
    filename = str(uploaded.filename or "").strip()
    if not filename:
        raise ValueError("cover file must have a filename")
    data = uploaded.stream.read(MAX_COVER_UPLOAD_BYTES + 1)
    if len(data) > MAX_COVER_UPLOAD_BYTES:
        raise ValueError("cover upload is too large")
    if not data:
        raise ValueError("cover file is empty")
    return filename, data


def read_playlist_upload() -> tuple[str, bytes, str]:
    length = request.content_length
    if length is not None and length > MAX_PLAYLIST_UPLOAD_BYTES:
        raise ValueError("playlist upload is too large")
    uploaded = request.files.get("playlist")
    if uploaded is None:
        raise ValueError("playlist file is required")
    filename = str(uploaded.filename or "").strip()
    if not filename:
        raise ValueError("playlist file must have a filename")
    data = uploaded.stream.read(MAX_PLAYLIST_UPLOAD_BYTES + 1)
    if len(data) > MAX_PLAYLIST_UPLOAD_BYTES:
        raise ValueError("playlist upload is too large")
    if not data:
        raise ValueError("playlist file is empty")
    return filename, data, str(request.form.get("name") or "")


def audio_file_response(path: Path) -> Response:
    return audio_resource_response(local_audio_resource(path))


def audio_resource_response(resource: AudioResource) -> Response:
    try:
        file_size, content_type = audio_resource_head(resource)
    except FileNotFoundError:
        abort(404, "Audio file not found")

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
            content_type=content_type,
        )

    def stream_resource() -> Any:
        try:
            yield from iter_audio_resource_bytes(resource, start=start, length=length)
        except (BrokenPipeError, ConnectionResetError):
            return

    return Response(
        stream_resource(),
        status=status,
        headers=headers,
        content_type=content_type,
        direct_passthrough=True,
    )


def artwork_response(height_px: int, track_id: int) -> Response:
    artwork = track_artwork(player_context().runtime, height_px, track_id)
    if artwork is None:
        abort(404)
    response = Response(artwork.data, content_type=artwork.mime_type)
    response.headers["Cache-Control"] = ARTWORK_CACHE_CONTROL
    return response


def playlist_cover_response(playlist_id: int) -> Response:
    from .playlist_art import playlist_cover_svg

    cover = playlist_cover_command(player_context().database, playlist_id)
    if cover.has_uploaded_cover:
        response = Response(cover.cover_data or b"", content_type=cover.cover_mime_type)
    else:
        svg = cover.cover_svg or playlist_cover_svg(cover.name)
        response = Response(svg.encode("utf-8"), content_type="image/svg+xml; charset=utf-8")
    response.headers["Cache-Control"] = ARTWORK_CACHE_CONTROL
    return response


def static_response(name: str) -> Response:
    try:
        resolved = resolve_static_asset_request(name)
    except FileNotFoundError:
        abort(404)
    if resolved is None:
        abort(404)
    asset, is_fingerprinted = resolved
    data = asset.data
    response = Response(data, content_type=asset.content_type)
    response.headers["Cache-Control"] = (
        STATIC_ASSET_CACHE_CONTROL if is_fingerprinted else STATIC_COMPAT_CACHE_CONTROL
    )
    return response


def job_events_response(runtime: PlayerRuntime) -> Response:
    def generate() -> Any:
        subscriber: Queue[dict[str, object]] = Queue()
        runtime.subscribe_jobs(subscriber)
        try:
            yield b"retry: 1000\n\n"
            for payload in runtime.active_job_payloads():
                yield b"event: job\n"
                yield b"data: "
                yield json.dumps(payload, sort_keys=True).encode("utf-8")
                yield b"\n\n"
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
