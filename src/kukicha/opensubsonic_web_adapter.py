from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import hmac
import json
from pathlib import Path
import re
from typing import Any
from xml.sax.saxutils import escape, quoteattr

from flask import Flask, Response, current_app, request, stream_with_context
from werkzeug.datastructures import MultiDict
from werkzeug.serving import make_server

from .app_metadata import kukicha_version
from .models import ALBUM_ARTWORK_HEIGHT, TRACK_ARTWORK_HEIGHT
from .player_config import LOGGER, PlayerServerOptions, validate_player_startup
from .player_errors import PlayerConfigError
from .player_media import audio_mime_type
from .player_platform import register_player_signal_handlers, restore_signal_handlers
from .player_runtime import PlayerRuntime
from .use_case import (
    AlbumNotFoundError,
    LibraryQueries,
    TrackNotFoundError,
    connect_database,
    record_playback,
    track_artwork,
    track_audio_path,
)
from .use_case.queries.library import album_artist_display_text, album_artists_by_album


OPEN_SUBSONIC_CONTEXT_KEY = "kukicha_open_subsonic_context"
OPEN_SUBSONIC_PROTOCOL_VERSION = "1.16.1"
OPEN_SUBSONIC_TYPE = "kukicha"
BYTE_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)$")
CHUNK_SIZE = 1024 * 512
ERROR_GENERIC = 0
ERROR_REQUIRED_PARAMETER_MISSING = 10
ERROR_WRONG_USERNAME_OR_PASSWORD = 40
ERROR_UNSUPPORTED_AUTHENTICATION = 42
ERROR_CONFLICTING_AUTHENTICATION = 43
ERROR_NOT_FOUND = 70
IGNORED_ARTICLES = ("The", "An", "A", "Die", "Das", "Ein", "Eine", "Les", "Le", "La")
IGNORED_ARTICLES_TEXT = " ".join(IGNORED_ARTICLES)


@dataclass(frozen=True, slots=True)
class OpenSubsonicWebContext:
    options: PlayerServerOptions
    runtime: PlayerRuntime
    database: Path


class OpenSubsonicApiError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def create_open_subsonic_app(options: PlayerServerOptions) -> Flask:
    app = Flask("kukicha-opensubsonic", static_folder=None)
    runtime = PlayerRuntime(options)
    app.extensions[OPEN_SUBSONIC_CONTEXT_KEY] = OpenSubsonicWebContext(
        options=options,
        runtime=runtime,
        database=runtime.database,
    )

    @app.get("/healthz")
    def healthz() -> Response:
        return Response(status=204)

    @app.get("/")
    @app.get("/rest")
    @app.get("/rest/")
    def service_root() -> Response:
        return Response(
            "kukicha OpenSubsonic\n",
            content_type="text/plain; charset=utf-8",
        )

    @app.route("/rest/<path:endpoint>", methods=["GET", "POST"])
    def open_subsonic_endpoint(endpoint: str) -> Response:
        endpoint_name = normalized_endpoint_name(endpoint)
        params = request_parameters()
        format_error = response_format_error(params)
        if format_error is not None:
            return subsonic_error_response(format_error.code, format_error.message)

        handler = open_subsonic_handlers().get(endpoint_name.casefold())
        if endpoint_name.casefold() != "getopensubsonicextensions":
            auth_error = authentication_error(open_subsonic_context().options, params)
            if auth_error is not None:
                return subsonic_error_response(auth_error.code, auth_error.message)

        if handler is None:
            return subsonic_error_response(ERROR_GENERIC, "Endpoint not implemented")

        try:
            result = handler(params)
        except OpenSubsonicApiError as error:
            return subsonic_error_response(error.code, error.message)
        except (AlbumNotFoundError, TrackNotFoundError, FileNotFoundError):
            return subsonic_error_response(ERROR_NOT_FOUND, "The requested data was not found.")
        if isinstance(result, Response):
            return result
        return subsonic_success_response(result)

    return app


def serve_open_subsonic(options: PlayerServerOptions) -> int:
    try:
        validate_player_startup(options)
    except PlayerConfigError as error:
        LOGGER.error("%s", error)
        return 1

    app = create_open_subsonic_app(options)
    try:
        server = make_server(
            options.open_subsonic_host,
            options.open_subsonic_port,
            app,
            threaded=True,
        )
    except OSError as error:
        LOGGER.error(
            "failed to bind OpenSubsonic server on %s:%s: %s",
            options.open_subsonic_host,
            options.open_subsonic_port,
            error,
        )
        return 1

    url = f"http://{options.open_subsonic_host}:{server.server_port}/"
    LOGGER.info("using config file %s", options.config_path)
    LOGGER.info("kukicha OpenSubsonic listening on %s", url)
    stop_reason = {"value": "received interrupt"}
    previous_handlers = register_player_signal_handlers(stop_reason)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("stopping OpenSubsonic server: %s", stop_reason["value"])
    finally:
        server.server_close()
        restore_signal_handlers(previous_handlers)
        LOGGER.info("OpenSubsonic server stopped")
    return 0


def open_subsonic_handlers() -> dict[str, Any]:
    return {
        "ping": handle_ping,
        "getlicense": handle_get_license,
        "getopensubsonicextensions": handle_get_open_subsonic_extensions,
        "getmusicfolders": handle_get_music_folders,
        "getartists": handle_get_artists,
        "getartist": handle_get_artist,
        "getalbumlist2": handle_get_album_list2,
        "getalbum": handle_get_album,
        "getsong": handle_get_song,
        "stream": handle_stream,
        "download": handle_download,
        "getcoverart": handle_get_cover_art,
        "scrobble": handle_scrobble,
    }


def open_subsonic_context() -> OpenSubsonicWebContext:
    return current_app.extensions[OPEN_SUBSONIC_CONTEXT_KEY]


def normalized_endpoint_name(endpoint: str) -> str:
    if endpoint.endswith(".view"):
        return endpoint[: -len(".view")]
    return endpoint


def request_parameters() -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    for source in (request.args, request.form if request.method == "POST" else MultiDict()):
        for key, items in source.lists():
            values.setdefault(key, []).extend(str(item) for item in items)
    return values


def first_param(params: Mapping[str, list[str]], key: str) -> str | None:
    values = params.get(key)
    if not values:
        return None
    return values[0]


def require_param(params: Mapping[str, list[str]], key: str) -> str:
    value = first_param(params, key)
    if value is None or not value:
        raise OpenSubsonicApiError(
            ERROR_REQUIRED_PARAMETER_MISSING,
            f"Required parameter is missing: {key}",
        )
    return value


def response_format_error(
    params: Mapping[str, list[str]],
) -> OpenSubsonicApiError | None:
    response_format = first_param(params, "f")
    if response_format is None or response_format.casefold() in {"json", "xml"}:
        return None
    return OpenSubsonicApiError(
        ERROR_GENERIC,
        "Response format is not supported",
    )


def authentication_error(
    options: PlayerServerOptions,
    params: Mapping[str, list[str]],
) -> OpenSubsonicApiError | None:
    if any(key.casefold() == "apikey" for key in params):
        return OpenSubsonicApiError(
            ERROR_UNSUPPORTED_AUTHENTICATION,
            "API key authentication is not supported",
        )

    username = first_param(params, "u")
    client_version = first_param(params, "v")
    client_name = first_param(params, "c")
    password = first_param(params, "p")
    token = first_param(params, "t")
    salt = first_param(params, "s")
    has_password_auth = password is not None
    has_token_auth = token is not None or salt is not None

    for value in (username, client_version, client_name):
        if value is None or not value:
            return OpenSubsonicApiError(
                ERROR_REQUIRED_PARAMETER_MISSING,
                "Required authentication parameter is missing",
            )
    if has_password_auth and has_token_auth:
        return OpenSubsonicApiError(
            ERROR_CONFLICTING_AUTHENTICATION,
            "Multiple authentication mechanisms were provided",
        )
    if not has_password_auth and not has_token_auth:
        return OpenSubsonicApiError(
            ERROR_REQUIRED_PARAMETER_MISSING,
            "Required authentication parameter is missing",
        )

    if not hmac.compare_digest(str(username), options.open_subsonic_username):
        return OpenSubsonicApiError(
            ERROR_WRONG_USERNAME_OR_PASSWORD,
            "Wrong username or password",
        )

    if has_password_auth:
        if password is None or not hmac.compare_digest(password, options.open_subsonic_password):
            return OpenSubsonicApiError(
                ERROR_WRONG_USERNAME_OR_PASSWORD,
                "Wrong username or password",
            )
        return None

    if token is None or salt is None or not token or not salt:
        return OpenSubsonicApiError(
            ERROR_REQUIRED_PARAMETER_MISSING,
            "Required authentication parameter is missing",
        )
    expected = hashlib.md5(
        f"{options.open_subsonic_password}{salt}".encode("utf-8")
    ).hexdigest()
    if not hmac.compare_digest(token.casefold(), expected):
        return OpenSubsonicApiError(
            ERROR_WRONG_USERNAME_OR_PASSWORD,
            "Wrong username or password",
        )
    return None


def handle_ping(params: Mapping[str, list[str]]) -> dict[str, object]:
    return {}


def handle_get_license(params: Mapping[str, list[str]]) -> dict[str, object]:
    return {"license": {"valid": True}}


def handle_get_open_subsonic_extensions(
    params: Mapping[str, list[str]],
) -> dict[str, object]:
    return {"openSubsonicExtensions": [{"name": "formPost", "versions": [1]}]}


def handle_get_music_folders(params: Mapping[str, list[str]]) -> dict[str, object]:
    folders = [
        {
            "id": str(root.position),
            "name": music_folder_name(root.path),
        }
        for root in LibraryQueries(open_subsonic_context().database).library_roots()
    ]
    return {"musicFolders": {"musicFolder": folders}}


def handle_get_artists(params: Mapping[str, list[str]]) -> dict[str, object]:
    root_position = optional_int_param(params, "musicFolderId")
    artists = artist_stats_payloads(
        open_subsonic_context().database,
        root_position=root_position,
    )
    return {"artists": {"ignoredArticles": IGNORED_ARTICLES_TEXT, "index": artists}}


def handle_get_artist(params: Mapping[str, list[str]]) -> dict[str, object]:
    artist = require_param(params, "id")
    return {"artist": artist_payload(open_subsonic_context().database, artist)}


def handle_get_album_list2(params: Mapping[str, list[str]]) -> dict[str, object]:
    require_param(params, "type")
    size = int_param(params, "size", default=10, minimum=0, maximum=500)
    offset = int_param(params, "offset", default=0, minimum=0)
    albums = album_list2_payloads(open_subsonic_context().database, size=size, offset=offset)
    return {"albumList2": {"album": albums}}


def handle_get_album(params: Mapping[str, list[str]]) -> dict[str, object]:
    album_id = require_param(params, "id")
    album = LibraryQueries(open_subsonic_context().database).get_album(album_id)
    return {"album": album_payload(album, include_songs=True)}


def handle_get_song(params: Mapping[str, list[str]]) -> dict[str, object]:
    track_id = int_required_param(params, "id")
    song = LibraryQueries(open_subsonic_context().database).get_track(track_id)
    return {"song": song_payload(song)}


def handle_stream(params: Mapping[str, list[str]]) -> Response:
    track_id = int_required_param(params, "id")
    path = track_audio_path(open_subsonic_context().runtime, track_id)
    return audio_file_response(path)


def handle_download(params: Mapping[str, list[str]]) -> Response:
    track_id = int_required_param(params, "id")
    path = track_audio_path(open_subsonic_context().runtime, track_id)
    response = audio_file_response(path)
    response.headers["Content-Disposition"] = (
        f'attachment; filename="{content_disposition_filename(path.name)}"'
    )
    return response


def handle_get_cover_art(params: Mapping[str, list[str]]) -> Response:
    cover_id = require_param(params, "id")
    context = open_subsonic_context()
    track_ids = cover_art_track_ids(context.database, cover_id)
    for track_id in track_ids:
        artwork = track_artwork(context.runtime, ALBUM_ARTWORK_HEIGHT, track_id)
        if artwork is None:
            artwork = track_artwork(context.runtime, TRACK_ARTWORK_HEIGHT, track_id)
        if artwork is not None:
            response = Response(artwork.data, content_type=artwork.mime_type)
            response.headers["Cache-Control"] = "private, max-age=3600"
            return response
    raise OpenSubsonicApiError(ERROR_NOT_FOUND, "The requested data was not found.")


def handle_scrobble(params: Mapping[str, list[str]]) -> dict[str, object]:
    id_values = params.get("id") or []
    if not id_values:
        raise OpenSubsonicApiError(
            ERROR_REQUIRED_PARAMETER_MISSING,
            "Required parameter is missing: id",
        )
    time_values = params.get("time") or []
    submission = bool_param(params, "submission", default=True)
    for index, id_value in enumerate(id_values):
        try:
            track_id = int(id_value)
        except ValueError as error:
            raise OpenSubsonicApiError(
                ERROR_REQUIRED_PARAMETER_MISSING,
                "Required parameter is invalid: id",
            ) from error
        played_at = (
            scrobble_time_from_epoch_millis(time_values[index])
            if index < len(time_values)
            else None
        )
        record_playback(
            open_subsonic_context().database,
            track_id,
            submission=submission,
            played_at=played_at,
            source="opensubsonic",
        )
    return {}


def int_required_param(params: Mapping[str, list[str]], key: str) -> int:
    value = require_param(params, key)
    try:
        return int(value)
    except ValueError as error:
        raise OpenSubsonicApiError(
            ERROR_REQUIRED_PARAMETER_MISSING,
            f"Required parameter is invalid: {key}",
        ) from error


def int_param(
    params: Mapping[str, list[str]],
    key: str,
    *,
    default: int,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    value = first_param(params, key)
    if value is None or value == "":
        parsed = default
    else:
        try:
            parsed = int(value)
        except ValueError as error:
            raise OpenSubsonicApiError(
                ERROR_REQUIRED_PARAMETER_MISSING,
                f"Required parameter is invalid: {key}",
            ) from error
    if minimum is not None:
        parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def optional_int_param(params: Mapping[str, list[str]], key: str) -> int | None:
    value = first_param(params, key)
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError as error:
        raise OpenSubsonicApiError(
            ERROR_REQUIRED_PARAMETER_MISSING,
            f"Required parameter is invalid: {key}",
        ) from error


def bool_param(
    params: Mapping[str, list[str]],
    key: str,
    *,
    default: bool,
) -> bool:
    value = first_param(params, key)
    if value is None or value == "":
        return default
    folded = value.casefold()
    if folded in {"1", "true", "yes"}:
        return True
    if folded in {"0", "false", "no"}:
        return False
    raise OpenSubsonicApiError(
        ERROR_REQUIRED_PARAMETER_MISSING,
        f"Required parameter is invalid: {key}",
    )


def scrobble_time_from_epoch_millis(value: str) -> datetime:
    try:
        millis = int(value)
    except ValueError as error:
        raise OpenSubsonicApiError(
            ERROR_REQUIRED_PARAMETER_MISSING,
            "Required parameter is invalid: time",
        ) from error
    return datetime.fromtimestamp(millis / 1000, tz=UTC)


def artist_stats_payloads(
    database: Path,
    *,
    root_position: int | None = None,
) -> list[dict[str, object]]:
    with connect_database(database, create=False) as connection:
        if root_position is None:
            rows = list(
                connection.execute(
                    """
                    SELECT album_artist, albums_scanned
                    FROM library_album_artist_stats
                    WHERE COALESCE(album_artist, '') != ''
                    ORDER BY album_artist COLLATE NOCASE
                    """
                )
            )
        else:
            rows = list(
                connection.execute(
                    """
                    SELECT album_artist, albums_scanned
                    FROM library_root_album_artist_stats
                    WHERE root_position = ?
                        AND COALESCE(album_artist, '') != ''
                    ORDER BY album_artist COLLATE NOCASE
                    """,
                    (root_position,),
                )
            )
    artists = [
        artist_summary_payload(
            database,
            str(row["album_artist"]),
            album_count=int(row["albums_scanned"]),
            root_position=root_position,
        )
        for row in rows
    ]
    return artist_indexes(artists)


def artist_payload(database: Path, artist_id_value: str) -> dict[str, object]:
    requested_artist = artist_name_from_id(artist_id_value)
    with connect_database(database, create=False) as connection:
        artist_row = connection.execute(
            """
            SELECT album_artist, albums_scanned
            FROM library_album_artist_stats
            WHERE album_artist = ? COLLATE NOCASE
            """,
            (requested_artist,),
        ).fetchone()
    if artist_row is None:
        raise OpenSubsonicApiError(ERROR_NOT_FOUND, "The requested data was not found.")

    artist_name = str(artist_row["album_artist"])
    payload = artist_summary_payload(
        database,
        artist_name,
        album_count=int(artist_row["albums_scanned"]),
    )
    payload["album"] = artist_album_payloads(database, artist_name)
    return payload


def artist_album_payloads(database: Path, artist: str) -> list[dict[str, object]]:
    with connect_database(database, create=False) as connection:
        rows = list(
            connection.execute(
                """
                SELECT
                    albums.rowid,
                    albums.album_id,
                    albums.album,
                    albums.year,
                    albums.track_count,
                    albums.file_created_at,
                    albums.art_track_id,
                    COALESCE(
                        (
                            SELECT SUM(COALESCE(tracks.duration_seconds, 0))
                            FROM library_tracks AS tracks
                            WHERE tracks.album_id = albums.album_id
                        ),
                        0
                    ) AS duration,
                    (
                        SELECT genres.genre
                        FROM library_album_genres AS genres
                        WHERE genres.album_id = albums.album_id
                        ORDER BY genres.genre COLLATE NOCASE
                        LIMIT 1
                    ) AS genre
                FROM library_albums AS albums
                JOIN library_album_artists AS artists
                    ON artists.album_id = albums.album_id
                WHERE artists.artist = ? COLLATE NOCASE
                ORDER BY albums.rowid
                """,
                (artist,),
            )
        )
        artists_by_album = album_artists_by_album(
            connection,
            (str(row["album_id"]) for row in rows),
        )

    parent = artist_id(artist)
    albums: list[dict[str, object]] = []
    for row in rows:
        album_id = str(row["album_id"])
        album_artist = album_artist_display_text(artists_by_album.get(album_id, ()))
        album = album_summary_payload(
            album_id=album_id,
            name=str(row["album"]),
            artist=album_artist,
            song_count=int(row["track_count"]),
            year=int(row["year"]) if row["year"] is not None else None,
            created=row["file_created_at"],
            has_cover=row["art_track_id"] is not None,
            art_track_id=(
                int(row["art_track_id"]) if row["art_track_id"] is not None else None
            ),
        )
        album.update(
            without_none(
                {
                    "parent": parent,
                    "album": str(row["album"]),
                    "title": str(row["album"]),
                    "isDir": True,
                    "duration": int(row["duration"] or 0),
                    "genre": row["genre"],
                }
            )
        )
        albums.append(album)
    return albums


def artist_summary_payload(
    database: Path,
    artist: str,
    *,
    album_count: int,
    root_position: int | None = None,
) -> dict[str, object]:
    return without_none(
        {
            "id": artist_id(artist),
            "name": artist,
            "coverArt": artist_cover_art_id(
                database,
                artist,
                root_position=root_position,
            ),
            "albumCount": album_count,
            "roles": ["albumartist"],
        }
    )


def artist_cover_art_id(
    database: Path,
    artist: str,
    *,
    root_position: int | None = None,
) -> str | None:
    with connect_database(database, create=False) as connection:
        if root_position is None:
            row = connection.execute(
                """
                SELECT albums.album_id
                FROM library_albums AS albums
                JOIN library_album_artists AS artists
                    ON artists.album_id = albums.album_id
                WHERE artists.artist = ? COLLATE NOCASE
                    AND albums.art_track_id IS NOT NULL
                ORDER BY albums.rowid
                LIMIT 1
                """,
                (artist,),
            ).fetchone()
        else:
            row = connection.execute(
                """
                SELECT albums.album_id
                FROM library_albums AS albums
                JOIN library_album_roots AS album_roots
                    ON album_roots.album_id = albums.album_id
                JOIN library_album_artists AS artists
                    ON artists.album_id = albums.album_id
                WHERE artists.artist = ? COLLATE NOCASE
                    AND album_roots.root_position = ?
                    AND album_roots.art_track_id IS NOT NULL
                ORDER BY albums.rowid
                LIMIT 1
                """,
                (artist, root_position),
            ).fetchone()
    if row is None:
        return None
    return album_cover_art_id(str(row["album_id"]))


def artist_indexes(artists: Iterable[dict[str, object]]) -> list[dict[str, object]]:
    groups: dict[str, list[dict[str, object]]] = {}
    for artist in sorted(
        artists,
        key=lambda item: artist_sort_key(str(item.get("name", ""))),
    ):
        groups.setdefault(artist_index_name(str(artist.get("name", ""))), []).append(artist)
    return [
        {"name": name, "artist": groups[name]}
        for name in sorted(groups, key=artist_index_sort_key)
    ]


def artist_index_name(artist: str) -> str:
    key = artist_sort_key(artist)
    if not key:
        return "#"
    first = key[0].upper()
    if not first.isalpha():
        return "#"
    return first


def artist_index_sort_key(value: str) -> tuple[int, str]:
    return (1, value) if value == "#" else (0, value)


def artist_sort_key(artist: str) -> str:
    sortable = artist.strip()
    casefolded_articles = {article.casefold() for article in IGNORED_ARTICLES}
    while True:
        words = sortable.split(maxsplit=1)
        if not words or words[0].casefold() not in casefolded_articles:
            break
        sortable = words[1] if len(words) > 1 else ""
    return sortable.casefold()


def artist_name_from_id(value: str) -> str:
    if value.startswith("artist:"):
        return value[len("artist:") :]
    return value


def album_list2_payloads(database: Path, *, size: int, offset: int) -> list[dict[str, object]]:
    with connect_database(database, create=False) as connection:
        rows = list(
            connection.execute(
                """
                SELECT
                    rowid,
                    album_id,
                    album,
                    year,
                    track_count,
                    file_created_at,
                    art_track_id
                FROM library_albums
                ORDER BY rowid
                LIMIT ? OFFSET ?
                """,
                (size, offset),
            )
        )
        artists_by_album = album_artists_by_album(
            connection,
            (str(row["album_id"]) for row in rows),
        )
    return [
        album_summary_payload(
            album_id=str(row["album_id"]),
            name=str(row["album"]),
            artist=album_artist_display_text(artists_by_album.get(str(row["album_id"]), ())),
            song_count=int(row["track_count"]),
            year=int(row["year"]) if row["year"] is not None else None,
            created=row["file_created_at"],
            has_cover=row["art_track_id"] is not None,
            art_track_id=(
                int(row["art_track_id"]) if row["art_track_id"] is not None else None
            ),
        )
        for row in rows
    ]


def album_summary_payload(
    *,
    album_id: str,
    name: str,
    artist: str,
    song_count: int,
    year: int | None,
    created: object,
    has_cover: bool,
    art_track_id: int | None,
) -> dict[str, object]:
    return without_none(
        {
            "id": album_id,
            "name": name,
            "artist": artist,
            "artistId": artist_id(artist),
            "coverArt": album_cover_art_id(album_id) if has_cover or art_track_id else None,
            "songCount": song_count,
            "created": str(created) if created else None,
            "year": year,
        }
    )


def album_payload(album: Any, *, include_songs: bool = False) -> dict[str, object]:
    duration = sum(int(track.duration_seconds or 0) for track in album.tracks)
    payload = album_summary_payload(
        album_id=album.album_id,
        name=album.album,
        artist=album.artist,
        song_count=album.track_count,
        year=album.year,
        created=album.file_created_at,
        has_cover=album.has_cover,
        art_track_id=album.art_track_id,
    )
    payload.update(
        without_none(
            {
                "duration": duration,
                "genre": album.genres[0] if album.genres else None,
            }
        )
    )
    if include_songs:
        payload["song"] = [song_payload(track) for track in album.tracks]
    return payload


def song_payload(track: Any) -> dict[str, object]:
    track_id = int(track.track_id) if track.track_id is not None else None
    artist = track.artist or track.album_artist or album_artist_display_text(track.album_artists)
    album_artist = album_artist_display_text(track.album_artists)
    path = Path(track.path)
    return without_none(
        {
            "id": str(track_id) if track_id is not None else None,
            "parent": track.album_id,
            "isDir": False,
            "title": track.title or path.stem,
            "album": track.album,
            "artist": artist,
            "artistId": artist_id(artist),
            "albumArtist": album_artist,
            "albumArtistId": artist_id(album_artist),
            "albumId": track.album_id,
            "coverArt": (
                str(track_id)
                if track_id is not None and track.has_cover
                else album_cover_art_id(track.album_id)
                if track.album_id and track.has_cover
                else None
            ),
            "track": first_number(track.track_number),
            "discNumber": first_number(track.disc_number),
            "year": first_number(track.date),
            "genre": track.genres[0] if track.genres else None,
            "created": None,
            "duration": int(track.duration_seconds) if track.duration_seconds else None,
            "bitRate": bit_rate_kbps(track.bitrate),
            "size": file_size(path),
            "suffix": audio_suffix(track.file_type, path),
            "contentType": audio_mime_type(path),
            "path": track.path,
            "type": "music",
            "isVideo": False,
        }
    )


def cover_art_track_ids(database: Path, cover_id: str) -> tuple[int, ...]:
    if cover_id.startswith("album:"):
        album = LibraryQueries(database).get_album(cover_id[len("album:") :])
        return tuple(
            track_id
            for track_id in (
                album.art_track_id,
                *(track.track_id for track in album.tracks),
            )
            if track_id is not None
        )
    try:
        return (int(cover_id),)
    except ValueError as error:
        raise OpenSubsonicApiError(
            ERROR_NOT_FOUND,
            "The requested data was not found.",
        ) from error


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


def audio_file_response(path: Path) -> Response:
    if not path.is_file():
        raise FileNotFoundError(path)
    file_size = path.stat().st_size
    if file_size <= 0:
        raise FileNotFoundError(path)

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
        return Response(status=status, headers=headers, content_type=audio_mime_type(path))

    def stream_file() -> Iterable[bytes]:
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
        stream_with_context(stream_file()),
        status=status,
        headers=headers,
        content_type=audio_mime_type(path),
        direct_passthrough=True,
    )


def subsonic_success_response(payload: Mapping[str, object]) -> Response:
    return subsonic_response("ok", payload)


def subsonic_error_response(code: int, message: str) -> Response:
    return subsonic_response(
        "failed",
        {"error": {"code": code, "message": message}},
    )


def subsonic_response(status: str, payload: Mapping[str, object]) -> Response:
    if requested_response_format() == "json":
        return subsonic_json_response(status, payload)
    return subsonic_xml_response(status, payload)


def requested_response_format() -> str:
    response_format = first_param(request_parameters(), "f")
    if response_format is not None and response_format.casefold() != "xml":
        return "json"
    return "xml"


def subsonic_json_response(status: str, payload: Mapping[str, object]) -> Response:
    body = {
        "subsonic-response": {
            "status": status,
            "version": OPEN_SUBSONIC_PROTOCOL_VERSION,
            "type": OPEN_SUBSONIC_TYPE,
            "serverVersion": kukicha_server_version(),
            "openSubsonic": True,
            **payload,
        }
    }
    return Response(
        json.dumps(body, sort_keys=True),
        content_type="application/json; charset=utf-8",
    )


def subsonic_xml_response(status: str, payload: Mapping[str, object]) -> Response:
    attributes = {
        "xmlns": "http://subsonic.org/restapi",
        "status": status,
        "version": OPEN_SUBSONIC_PROTOCOL_VERSION,
        "type": OPEN_SUBSONIC_TYPE,
        "serverVersion": kukicha_server_version(),
        "openSubsonic": "true",
    }
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<subsonic-response{xml_attributes(attributes)}>"
        f"{xml_children(payload)}"
        "</subsonic-response>"
    )
    return Response(body, content_type="text/xml; charset=utf-8")


def xml_children(value: object) -> str:
    if isinstance(value, Mapping):
        return "".join(xml_element(key, item) for key, item in value.items())
    if isinstance(value, list | tuple):
        return "".join(xml_children(item) for item in value)
    if value is None:
        return ""
    return escape(str(value))


def xml_element(name: str, value: object) -> str:
    if isinstance(value, Mapping):
        attributes: dict[str, object] = {}
        children: list[tuple[str, object]] = []
        for key, item in value.items():
            if item is None:
                continue
            if isinstance(item, Mapping) or isinstance(item, list | tuple):
                children.append((key, item))
            else:
                attributes[key] = xml_attribute_value(item)
        child_xml = "".join(xml_element(key, item) for key, item in children)
        if child_xml:
            return f"<{name}{xml_attributes(attributes)}>{child_xml}</{name}>"
        return f"<{name}{xml_attributes(attributes)}/>"
    if isinstance(value, list | tuple):
        return "".join(xml_element(name, item) for item in value)
    if value is None:
        return ""
    return f"<{name}>{escape(str(value))}</{name}>"


def xml_attributes(values: Mapping[str, object]) -> str:
    if not values:
        return ""
    return "".join(
        f" {name}={quoteattr(str(value))}"
        for name, value in values.items()
        if value is not None
    )


def xml_attribute_value(value: object) -> object:
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def kukicha_server_version() -> str:
    return kukicha_version()


def music_folder_name(path: str) -> str:
    name = Path(path).name
    return name or path


def artist_id(artist: str | None) -> str | None:
    return f"artist:{artist}" if artist else None


def album_cover_art_id(album_id: str | None) -> str | None:
    return f"album:{album_id}" if album_id else None


def first_number(value: str | None) -> int | None:
    if not value:
        return None
    match = re.search(r"\d+", value)
    return int(match.group(0)) if match else None


def bit_rate_kbps(value: int | None) -> int | None:
    if value is None:
        return None
    if value >= 1000:
        return int(value / 1000)
    return value


def file_size(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


def audio_suffix(file_type: str | None, path: Path) -> str | None:
    if file_type:
        return file_type.removeprefix(".")
    suffix = path.suffix.removeprefix(".")
    return suffix or None


def content_disposition_filename(value: str) -> str:
    return value.replace("\\", "_").replace('"', "_")


def without_none(values: Mapping[str, object | None]) -> dict[str, object]:
    return {key: value for key, value in values.items() if value is not None}
