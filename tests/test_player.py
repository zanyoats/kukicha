from __future__ import annotations

from collections.abc import Iterable
import io
import json
import os
from dataclasses import replace
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch
from urllib.parse import parse_qs

from kukicha._compat import UTC, tomllib
from kukicha.album_artists import DEFAULT_ALBUM_ARTIST_SPLIT_PATTERNS
from kukicha.app_metadata import kukicha_version
from kukicha.audio_types import (
    KNOWN_AUDIO_MIME_TYPES,
    KNOWN_IMAGE_MIME_TYPES,
    audio_content_type_for_name,
    content_type_for_name,
)
from kukicha.use_case import (
    ALBUM_LIST_SORT_ALBUMS,
    ALBUM_LIST_SORT_ARTIST,
    ALBUM_LIST_SORT_FREQUENT,
    ALBUM_LIST_SORT_GENRE,
    ALBUM_LIST_SORT_RECENT,
    ALBUM_LIST_SORT_RECENTLY_ADDED,
    ALBUM_LIST_SORT_STARRED,
    AlbumDetails,
    AlbumListQuery,
    AlbumNotFoundError,
    GenreFilterGroup,
    GenreStyleFilter,
    LibraryAlbumArtistStats,
    LibraryFilterOptions,
    LibraryQueries,
    LibraryRootFilterOption,
    LibrarySearchQuery,
    MAX_RECOMMENDATION_LIMIT,
    NATIVE_PLAYBACK_SOURCE,
    PlaylistDetails,
    PlaylistItem,
    PlaylistTrack,
    home_dashboard,
    record_opensubsonic_client,
    record_playback,
)
from kukicha.cli import build_parser
from kukicha.use_case import connect_database
from kukicha.use_case.database import clear_library
from kukicha.use_case import CoverArtResolutionStats, GenreResolutionStats, save_library
from kukicha.models import MusicLibrary, PlaylistItemRecord, PlaylistRecord, TrackArtwork, TrackRecord
from kukicha.library_sources import (
    RemoteRootConfig,
    canonical_s3_path,
    clear_s3_client_cache,
    create_s3_client,
)
from kukicha.player_jobs import (
    job_payload,
)

from kukicha.player_config import (
    ACCENT_COLOR_CODES,
    DEFAULT_ACCENT_COLOR,
    DEFAULT_AUTH_COOKIE_MAX_AGE,
    DEFAULT_AUTH_COOKIE_NAME,
    DEFAULT_OPEN_SUBSONIC_SECRET_FILENAME,
    DEFAULT_PLAYER_HOST,
    DEFAULT_PLAYER_LOG_LEVEL,
    DEFAULT_PLAYER_PORT,
    DEFAULT_PREFER_MUSICBRAINZ_ENGLISH_ALIASES,
    DEFAULT_TOAST_TIMEOUT_MS,
    DEFAULT_TRUSTED_PROXY_HEADERS,
    APPEARANCE_THEMES,
    CONTROL_ACCENT_MINIMUM_CONTRAST,
    OpenSubsonicOptions,
    PlayerAuthOptions,
    PlayerServerOptions,
    build_template_environment,
    contrast_ratio,
    derived_control_accent,
    load_player_options,
    player_accent_theme,
    player_config_help_text,
    validate_player_startup,
)
from kukicha.player_auth import hash_password, signed_auth_cookie, verify_password
from kukicha.use_case import (
    append_queue as append_queue_command,
    delete_album_files,
    edit_library_album_edit,
    edit_library_album_musicbrainz,
    edit_library_album_tags,
    library_job_detail_lines,
    library_job_summary_text,
    library_scan_progress_text,
    load_queue_state_database,
    create_player_job,
    get_player_job,
    list_player_jobs,
    mark_stale_player_jobs_canceled,
    playlist_menu_options_by_track_id,
    prepare_album_cover_upload_job,
    prepare_album_delete_job,
    prepare_album_edit_job,
    prepare_album_musicbrainz_edit_job,
    prepare_album_musicbrainz_edit_request,
    prepare_album_tag_edit_job,
    rescan_library,
    remove_queue_item as remove_queue_item_command,
    sync_library_roots,
    set_track_playlist_membership_database,
    start_album_cover_upload,
    start_album_delete,
    start_album_edit,
    start_bulk_album_metadata_edit,
    update_playback as update_playback_command,
    update_player_job,
    update_queue as update_queue_command,
    upload_album_cover_files,
)
from kukicha.player_errors import PlayerConfigError, PlayerConflictError
from kukicha.player_common import format_compact_count, format_count_label
from kukicha.player_media import audio_mime_type, mpeg4_audio_codec_for_path
from kukicha.player_navigation import (
    album_artist_links,
    album_artist_url,
    album_bulk_metadata_edit_url,
    album_bulk_star_action_url,
    album_genre_links,
    album_index_url,
    album_meta_query,
    album_style_links,
    artist_cloud_links,
    player_page_heading,
    player_page_menu_items,
    playlist_index_url,
    recommendation_album_radio_url,
    recommendation_artist_radio_url,
    recommendation_daily_url,
    recommendation_track_radio_url,
    search_url,
)
from kukicha.use_case import album_list_query_from_params
from kukicha.scanner import ARTWORK_IMAGE_EXTENSIONS, SUPPORTED_EXTENSIONS
from kukicha.player_presenters import (
    PlaylistMenuOption,
    TrackView,
    album_playback_track_payloads,
    album_tag_edit_section_for_tracks,
    album_tag_edit_sections,
    album_track_sections,
    format_track_duration,
    normalized_queue_state,
    playlist_item_view,
    queue_meta_text,
    queue_status,
    reset_queue_state,
    track_playback_payload,
    track_view,
    track_views_with_artist_display_lines,
    valid_playback_ids,
)
from kukicha.player_views import (
    album_musicbrainz_edit_sections,
    base_player_context,
    build_album_edit_context,
    bulk_metadata_edit_rows_for_album,
)
from kukicha.use_case.metadata import (
    store_album_metadata_link,
    store_album_metadata_track_link,
)

from kukicha.player_runtime import (
    PlayerJobCanceled,
    PlayerJobCancelToken,
    PlayerJobRecord,
    PlayerJobResult,
    PlayerQueueState,
    PlayerRuntime,
)
from kukicha.player_web_adapter import create_player_app, serve_player, start_player_sync
from kukicha.playlist_art import playlist_cover_data_url, playlist_cover_svg
from kukicha.static_assets import (
    HTML_CACHE_CONTROL,
    STATIC_ASSET_CACHE_CONTROL,
    STATIC_COMPAT_CACHE_CONTROL,
    static_asset_url,
)


TEST_ARGON2ID_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$"
    "c29tZXNhbHR2YWx1ZQ$"
    "c29tZXBhc3N3b3JkaGFzaHZhbHVl"
)


class TtyStringIO(io.StringIO):
    def isatty(self) -> bool:
        return True


def insert_library_album(
    connection,
    album_id: str,
    artist: str,
    album: str,
    year: int | None,
    track_count: int,
) -> None:
    connection.execute(
        """
        INSERT INTO library_albums (
            album_id, album, year, track_count
        ) VALUES (?, ?, ?, ?)
        """,
        (album_id, album, year, track_count),
    )
    connection.execute(
        """
        INSERT INTO library_album_artists (album_id, position, artist)
        VALUES (?, ?, ?)
        """,
        (album_id, 0, artist),
    )


def insert_library_track(
    connection,
    track_id: int,
    *,
    path: str | None = None,
    title: str | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO library_tracks (
            track_id,
            path,
            file_type,
            artist,
            album_artist,
            album,
            title,
            duration_seconds
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            track_id,
            path or f"/music/Artist/Album/{track_id:02d}.mp3",
            "mp3",
            "Artist",
            "Artist",
            "Album",
            title or f"Track {track_id}",
            60.0,
        ),
    )


class FakeRemoteEditS3Client:
    def __init__(
        self,
        data: bytes = b"audio bytes",
        *,
        metadata: dict[str, str] | None = None,
        content_type: str = "audio/flac",
        objects: Iterable[str] | None = None,
    ) -> None:
        self.data = data
        self.metadata = metadata or {"local-created-at": "2026-05-16T12:00:00+00:00"}
        self.content_type = content_type
        self.objects = {key: b"" for key in objects or ()}
        self.gets: list[dict[str, object]] = []
        self.puts: list[dict[str, object]] = []
        self.deletes: list[dict[str, object]] = []
        self.lists: list[dict[str, object]] = []

    def get_object(self, **kwargs: object) -> dict[str, object]:
        self.gets.append(kwargs)
        return {
            "Body": io.BytesIO(self.data),
            "Metadata": self.metadata,
            "ContentType": self.content_type,
        }

    def put_object(self, **kwargs: object) -> dict[str, object]:
        body = kwargs["Body"]
        if not hasattr(body, "read"):
            raise AssertionError("Body must be readable")
        self.puts.append(
            {
                "Bucket": kwargs["Bucket"],
                "Key": kwargs["Key"],
                "Body": body.read(),
                "ContentType": kwargs["ContentType"],
                "Metadata": kwargs["Metadata"],
            }
        )
        return {}

    def delete_object(self, **kwargs: object) -> dict[str, object]:
        self.deletes.append(kwargs)
        key = str(kwargs["Key"])
        self.objects.pop(key, None)
        return {}

    def list_objects_v2(self, **kwargs: object) -> dict[str, object]:
        self.lists.append(kwargs)
        prefix = str(kwargs.get("Prefix") or "")
        return {
            "Contents": [
                {"Key": key}
                for key in sorted(self.objects)
                if key.startswith(prefix)
            ]
        }


class PlayerQueueStateTest(unittest.TestCase):
    def test_reset_queue_state_clears_queue_and_unloads_track(self) -> None:
        state = PlayerQueueState(
            track_ids=[101, 102, 103],
            position=1,
            loaded_track_id=102,
            paused=False,
            errored_track_ids=[101],
        )

        reset_queue_state(state)

        self.assertEqual(state.track_ids, [])
        self.assertEqual(state.position, 0)
        self.assertIsNone(state.loaded_track_id)
        self.assertTrue(state.paused)
        self.assertEqual(state.errored_track_ids, [])

    def test_normalized_queue_state_keeps_finished_queue_position(self) -> None:
        state = normalized_queue_state(
            [101, 102],
            position=2,
            loaded_track_id=None,
            paused=False,
        )

        self.assertEqual(state.track_ids, [101, 102])
        self.assertEqual(state.position, 2)
        self.assertIsNone(state.loaded_track_id)
        self.assertTrue(state.paused)

    def test_normalized_queue_state_falls_back_to_current_track_when_loaded_track_is_invalid(self) -> None:
        state = normalized_queue_state(
            [101, 102, 103],
            position=1,
            loaded_track_id=999,
            paused=False,
        )

        self.assertEqual(state.track_ids, [101, 102, 103])
        self.assertEqual(state.position, 1)
        self.assertEqual(state.loaded_track_id, 102)
        self.assertFalse(state.paused)

    def test_normalized_queue_state_keeps_valid_error_track_ids(self) -> None:
        state = normalized_queue_state(
            [101, 102, 103],
            position=1,
            loaded_track_id=102,
            paused=False,
            errored_track_ids=[102, "103", 999, "not-an-int", 102],
        )

        self.assertEqual(state.errored_track_ids, [102, 103])

    def test_queue_status_shows_error_for_errored_track(self) -> None:
        state = PlayerQueueState(
            track_ids=[101, 102, 103],
            position=2,
            loaded_track_id=103,
            paused=False,
            errored_track_ids=[102],
        )

        self.assertEqual(queue_status(state, 102, 1), "Error")
        self.assertEqual(queue_status(state, 103, 2), "Now")

    def test_queue_status_shows_unavailable_before_error(self) -> None:
        state = PlayerQueueState(
            track_ids=[101],
            position=0,
            loaded_track_id=None,
            paused=True,
            errored_track_ids=[101],
            unavailable_track_ids=[101],
        )

        self.assertEqual(queue_status(state, 101, 0), "Unavailable")

    def test_queue_meta_text_counts_finished_queue_as_played(self) -> None:
        text = queue_meta_text(
            PlayerQueueState(
                track_ids=[101, 102],
                position=2,
                loaded_track_id=None,
                paused=True,
            ),
            [
                make_track_view(
                    101,
                    root_position=0,
                    path="/music/a/Album/01.mp3",
                    duration_seconds=60.0,
                ),
                make_track_view(
                    102,
                    root_position=0,
                    path="/music/a/Album/02.mp3",
                    duration_seconds=120.0,
                ),
            ],
        )

        self.assertEqual(text, "2 tracks - 2 played - 3 minutes")


class PlayerRuntimeTest(unittest.TestCase):
    def test_update_queue_keeps_only_valid_playback_ids(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            connection = connect_database(database)
            try:
                insert_library_track(connection, 101, title="One")
                connection.commit()
            finally:
                connection.close()
            runtime = PlayerRuntime(database)

            payload = update_queue_command(
                runtime,
                {
                    "track_ids": [101, 999, "not-an-int"],
                    "position": 0,
                    "loaded_track_id": 101,
                    "paused": False,
                    "errored_track_ids": [101, 999],
                }
            )

        self.assertEqual(payload["track_ids"], [101])
        self.assertEqual(payload["loaded_track_id"], 101)
        self.assertFalse(payload["paused"])
        self.assertEqual(payload["errored_track_ids"], [101])

    def test_update_playback_records_valid_queue_error_ids(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            connection = connect_database(database)
            try:
                insert_library_track(connection, 101, title="One")
                insert_library_track(connection, 102, title="Two")
                connection.commit()
            finally:
                connection.close()
            runtime = PlayerRuntime(database)
            update_queue_command(
                runtime,
                {
                    "track_ids": [101, 102],
                    "position": 0,
                    "loaded_track_id": 101,
                    "paused": False,
                },
            )

            payload = update_playback_command(
                runtime,
                {
                    "position": 1,
                    "loaded_track_id": 102,
                    "paused": False,
                    "errored_track_ids": [101, 999, "102"],
                },
            )

        self.assertEqual(payload["position"], 1)
        self.assertEqual(payload["loaded_track_id"], 102)
        self.assertEqual(payload["errored_track_ids"], [101, 102])

    def test_remote_playlist_stream_errors_are_not_persisted(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            save_library(
                MusicLibrary(
                    roots=[],
                    tracks=[],
                    playlists=[
                        PlaylistRecord(
                            path="/music/streams.m3u8",
                            name="Streams",
                            items=[
                                PlaylistItemRecord(
                                    path="https://example.test/live",
                                    title="Live",
                                )
                            ],
                        )
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-05-01T00:00:00+00:00",
                ),
                database,
            )
            runtime = PlayerRuntime(database)

            payload = update_queue_command(
                runtime,
                {
                    "track_ids": [-1],
                    "position": 0,
                    "loaded_track_id": -1,
                    "paused": False,
                    "errored_track_ids": [-1],
                },
            )

        self.assertEqual(payload["track_ids"], [-1])
        self.assertEqual(payload["loaded_track_id"], -1)
        self.assertEqual(payload["errored_track_ids"], [])

    def test_queue_load_clears_stale_remote_playlist_stream_errors(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            save_library(
                MusicLibrary(
                    roots=[],
                    tracks=[],
                    playlists=[
                        PlaylistRecord(
                            path="/music/streams.m3u8",
                            name="Streams",
                            items=[
                                PlaylistItemRecord(
                                    path="https://example.test/live",
                                    title="Live",
                                )
                            ],
                        )
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-05-01T00:00:00+00:00",
                ),
                database,
            )
            runtime = PlayerRuntime(database)
            update_queue_command(
                runtime,
                {
                    "track_ids": [-1],
                    "position": 0,
                    "loaded_track_id": -1,
                    "paused": True,
                },
            )
            with connect_database(database) as connection:
                connection.execute("UPDATE player_queue_items SET errored = 1")

            state = load_queue_state_database(database)
            with connect_database(database) as connection:
                errored = int(
                    connection.execute(
                        "SELECT errored FROM player_queue_items WHERE playback_id = -1"
                    ).fetchone()["errored"]
                )

        self.assertEqual(state.track_ids, [-1])
        self.assertEqual(state.errored_track_ids, [])
        self.assertEqual(errored, 0)

    def test_queue_load_keeps_remote_playlist_stream_available_after_rescan(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"

            def stream_library() -> MusicLibrary:
                return MusicLibrary(
                    roots=[],
                    tracks=[],
                    playlists=[
                        PlaylistRecord(
                            path="/music/streams.m3u8",
                            name="Streams",
                            items=[
                                PlaylistItemRecord(
                                    path="https://example.test/live",
                                    title="Live",
                                    duration_is_indeterminate=True,
                                )
                            ],
                        )
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-05-01T00:00:00+00:00",
                )

            save_library(stream_library(), database)
            runtime = PlayerRuntime(database)
            update_queue_command(
                runtime,
                {
                    "track_ids": [-1],
                    "position": 0,
                    "loaded_track_id": -1,
                    "paused": False,
                },
            )

            save_library(
                MusicLibrary(
                    roots=[],
                    tracks=[],
                    supported_extensions=[".flac"],
                    generated_at="2026-05-02T00:00:00+00:00",
                ),
                database,
            )
            state = load_queue_state_database(database)

        self.assertEqual(state.track_ids, [-1])
        self.assertEqual(state.loaded_track_id, -1)
        self.assertEqual(state.unavailable_track_ids, [])

    def test_queue_persists_across_runtime_instances(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            connection = connect_database(database)
            try:
                insert_library_track(connection, 101, title="One")
                insert_library_track(connection, 102, title="Two")
                connection.commit()
            finally:
                connection.close()

            first_runtime = PlayerRuntime(database)
            update_queue_command(
                first_runtime,
                {
                    "track_ids": [101, 102],
                    "position": 1,
                    "loaded_track_id": 102,
                    "paused": False,
                },
            )
            second_runtime = PlayerRuntime(database)
            state = second_runtime.queue_state_copy()

        self.assertEqual(state.track_ids, [101, 102])
        self.assertEqual(state.position, 1)
        self.assertEqual(state.loaded_track_id, 102)
        self.assertFalse(state.paused)

    def test_queue_payload_includes_snapshots_for_cold_browser_load(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            connection = connect_database(database)
            try:
                insert_library_track(connection, 101, title="One")
                connection.commit()
            finally:
                connection.close()
            runtime = PlayerRuntime(database)

            payload = update_queue_command(
                runtime,
                {
                    "track_ids": [101],
                    "position": 0,
                    "loaded_track_id": 101,
                    "paused": True,
                },
            )

        self.assertEqual(payload["track_snapshots"][0]["trackId"], 101)
        self.assertEqual(payload["track_snapshots"][0]["title"], "One")
        self.assertEqual(payload["track_snapshots"][0]["albumArtist"], "Artist")

    def test_queue_load_refreshes_stale_snapshots_from_library(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            connection = connect_database(database)
            try:
                insert_library_track(connection, 500, title="Actual Title")
                connection.execute(
                    """
                    INSERT INTO player_queue_items (
                        position,
                        playback_id,
                        snapshot_json,
                        errored
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (0, 500, '{"trackId": 500, "title": "Track 500"}', 0),
                )
                connection.execute(
                    """
                    INSERT INTO player_queue_state (
                        state_id,
                        position,
                        paused,
                        updated_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (1, 0, 1, "2026-05-03T00:00:00Z"),
                )
                connection.commit()
            finally:
                connection.close()

            state = load_queue_state_database(database)

        self.assertEqual(state.loaded_track_id, 500)
        self.assertEqual(state.snapshots[0]["title"], "Actual Title")
        self.assertEqual(state.snapshots[0]["albumArtist"], "Artist")

    def test_append_queue_creates_paused_queue_when_empty(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            connection = connect_database(database)
            try:
                insert_library_track(connection, 101, title="One")
                connection.commit()
            finally:
                connection.close()
            runtime = PlayerRuntime(database)

            payload = append_queue_command(runtime, {"track_ids": [101]})

        self.assertEqual(payload["track_ids"], [101])
        self.assertEqual(payload["position"], 0)
        self.assertEqual(payload["loaded_track_id"], 101)
        self.assertTrue(payload["paused"])

    def test_append_queue_preserves_existing_current_track(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            connection = connect_database(database)
            try:
                insert_library_track(connection, 101, title="One")
                insert_library_track(connection, 102, title="Two")
                connection.commit()
            finally:
                connection.close()
            runtime = PlayerRuntime(database)
            update_queue_command(
                runtime,
                {
                    "track_ids": [101],
                    "position": 0,
                    "loaded_track_id": 101,
                    "paused": False,
                },
            )

            payload = append_queue_command(runtime, {"track_ids": [102]})

        self.assertEqual(payload["track_ids"], [101, 102])
        self.assertEqual(payload["position"], 0)
        self.assertEqual(payload["loaded_track_id"], 101)
        self.assertFalse(payload["paused"])

    def test_remove_current_queue_item_advances_to_next_when_playing(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            connection = connect_database(database)
            try:
                insert_library_track(connection, 101, title="One")
                insert_library_track(connection, 102, title="Two")
                connection.commit()
            finally:
                connection.close()
            runtime = PlayerRuntime(database)
            update_queue_command(
                runtime,
                {
                    "track_ids": [101, 102],
                    "position": 0,
                    "loaded_track_id": 101,
                    "paused": False,
                },
            )

            payload = remove_queue_item_command(runtime, {"position": 0})

        self.assertEqual(payload["queue"]["track_ids"], [102])
        self.assertEqual(payload["queue"]["position"], 0)
        self.assertEqual(payload["queue"]["loaded_track_id"], 102)
        self.assertTrue(payload["play_next"])
        self.assertFalse(payload["queue"]["paused"])

    def test_remove_non_current_queue_item_preserves_current_track(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            connection = connect_database(database)
            try:
                insert_library_track(connection, 101, title="One")
                insert_library_track(connection, 102, title="Two")
                connection.commit()
            finally:
                connection.close()
            runtime = PlayerRuntime(database)
            update_queue_command(
                runtime,
                {
                    "track_ids": [101, 102],
                    "position": 0,
                    "loaded_track_id": 101,
                    "paused": False,
                },
            )

            payload = remove_queue_item_command(runtime, {"position": 1})

        self.assertEqual(payload["queue"]["track_ids"], [101])
        self.assertEqual(payload["queue"]["loaded_track_id"], 101)
        self.assertFalse(payload["play_next"])
        self.assertFalse(payload["queue"]["paused"])

    def test_queue_keeps_missing_items_as_unavailable_snapshots(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            connection = connect_database(database)
            try:
                insert_library_track(connection, 101, title="One")
                connection.commit()
            finally:
                connection.close()
            runtime = PlayerRuntime(database)
            update_queue_command(
                runtime,
                {
                    "track_ids": [101],
                    "position": 0,
                    "loaded_track_id": 101,
                    "paused": False,
                },
            )
            connection = connect_database(database, create=False)
            try:
                clear_library(connection)
                connection.commit()
            finally:
                connection.close()

            state = load_queue_state_database(database)

        self.assertEqual(state.track_ids, [101])
        self.assertEqual(state.unavailable_track_ids, [101])
        self.assertEqual(state.position, 1)
        self.assertIsNone(state.loaded_track_id)
        self.assertTrue(state.paused)
        self.assertEqual(state.snapshots[0]["title"], "One")

    def test_jobs_are_published_to_subscribers(self) -> None:
        runtime = PlayerRuntime(Path("/tmp/kukicha-test.sqlite"))
        subscriber: Queue[dict[str, object]] = Queue()
        runtime.subscribe_jobs(subscriber)

        runtime.publish_job(
            PlayerJobRecord(
                job_id=7,
                created_at="2026-04-21T10:00:00Z",
                updated_at="2026-04-21T10:00:00Z",
                started_at=None,
                finished_at=None,
                cancel_requested_at=None,
                kind="rescan_library",
                status="queued",
                message="Rescan queued.",
                reason="",
                context={"roots_scanned": 1},
            )
        )

        payload = subscriber.get_nowait()
        self.assertEqual(payload["job_id"], 7)
        self.assertEqual(payload["kind"], "rescan_library")

    def test_library_filter_options_are_cached_until_invalidated(self) -> None:
        runtime = PlayerRuntime(Path("/tmp/kukicha-test.sqlite"))
        options = LibraryFilterOptions(
            roots=(),
            artists=("Alice",),
            genre_groups=(GenreFilterGroup("Electronic"),),
        )
        api = Mock()
        api.filter_options.return_value = options

        with patch("kukicha.use_case.LibraryQueries", return_value=api):
            first = runtime.library_filter_options()
            second = runtime.library_filter_options()
            runtime.invalidate_library_filter_options()
            third = runtime.library_filter_options()

        self.assertIs(first, options)
        self.assertIs(second, options)
        self.assertIs(third, options)
        self.assertEqual(api.filter_options.call_count, 2)

    def test_enqueued_job_runs_and_updates_status(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            connection = connect_database(database)
            connection.close()
            runtime = PlayerRuntime(database)

            with patch.object(runtime, "ensure_job_worker_locked"):
                record = runtime.enqueue_job(
                    kind="rescan_library",
                    queued_message="Rescan queued.",
                    running_message="Rescan running.",
                    canceled_message="Rescan canceled.",
                    failed_message="Rescan failed.",
                    context={"roots_scanned": 1},
                    runner=lambda _cancel_token: PlayerJobResult(
                        "Rescan completed.",
                        {"roots_scanned": 1, "tracks_scanned": 1},
                    ),
                )
                queued = runtime.job_queue.popleft()

            runtime.run_queued_job(queued)
            finished = get_player_job(database, record.job_id)

            self.assertEqual(finished.status, "succeeded")
            self.assertEqual(finished.message, "Rescan completed.")
            self.assertEqual(finished.context["tracks_scanned"], 1)

    def test_queued_job_can_be_canceled_before_running(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            connection = connect_database(database)
            connection.close()
            runtime = PlayerRuntime(database)
            runner = Mock(return_value=PlayerJobResult("Should not run."))

            with patch.object(runtime, "ensure_job_worker_locked"):
                record = runtime.enqueue_job(
                    kind="rescan_library",
                    queued_message="Rescan queued.",
                    running_message="Rescan running.",
                    canceled_message="Rescan canceled.",
                    failed_message="Rescan failed.",
                    context={"roots_scanned": 1},
                    runner=runner,
                )
                queued = runtime.job_queue.popleft()

            canceled = runtime.cancel_job(record.job_id)
            runtime.run_queued_job(queued)
            finished = get_player_job(database, record.job_id)

            self.assertEqual(canceled.status, "canceled")
            self.assertEqual(finished.status, "canceled")
            self.assertEqual(finished.reason, "Canceled by user.")
            runner.assert_not_called()

    def test_stale_queued_and_running_jobs_are_canceled_on_startup(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            connection = connect_database(database)
            connection.close()
            queued = create_player_job(
                database,
                kind="add_root",
                message="Add and scan queued.",
                context={},
            )
            running = create_player_job(
                database,
                kind="rescan_library",
                message="Rescan queued.",
                context={},
            )
            update_player_job(
                database,
                running.job_id,
                status="running",
                message="Rescan running.",
                started_at="2026-04-21T10:00:00Z",
            )

            canceled = mark_stale_player_jobs_canceled(database)
            statuses = {job.job_id: job.status for job in canceled}

            self.assertEqual(statuses, {queued.job_id: "canceled", running.job_id: "canceled"})
            self.assertEqual(get_player_job(database, queued.job_id).reason, "Canceled because the player restarted.")


class PlayerAudioMimeTypeTest(unittest.TestCase):
    def test_known_audio_mime_types_cover_supported_extensions(self) -> None:
        self.assertEqual(set(KNOWN_AUDIO_MIME_TYPES), SUPPORTED_EXTENSIONS)

    def test_known_image_mime_types_cover_artwork_extensions(self) -> None:
        self.assertEqual(set(KNOWN_IMAGE_MIME_TYPES), set(ARTWORK_IMAGE_EXTENSIONS))

    def test_supported_audio_extensions_use_known_mime_types(self) -> None:
        expected = {
            ".flac": "audio/flac",
            ".m4a": "audio/mp4",
            ".m4b": "audio/mp4",
            ".m4p": "audio/mp4",
            ".m4r": "audio/mp4",
            ".mp3": "audio/mpeg",
            ".oga": "audio/ogg",
            ".ogg": "audio/ogg",
            ".opus": "audio/ogg",
        }
        for extension, mime_type in expected.items():
            with self.subTest(extension=extension):
                self.assertEqual(audio_mime_type(Path(f"track{extension}")), mime_type)

    def test_artwork_extensions_use_known_mime_types(self) -> None:
        expected = {
            ".gif": "image/gif",
            ".jpeg": "image/jpeg",
            ".jpg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
        }
        for extension, mime_type in expected.items():
            with self.subTest(extension=extension):
                self.assertEqual(content_type_for_name(f"cover{extension}"), mime_type)

    def test_image_mime_types_do_not_depend_on_platform_mimetypes(self) -> None:
        with patch("kukicha.audio_types.mimetypes.guess_type", return_value=(None, None)):
            self.assertEqual(content_type_for_name("cover.jpg"), "image/jpeg")
            self.assertEqual(content_type_for_name("cover.webp"), "image/webp")

    def test_flac_uses_flac_audio_mime_type_without_platform_mimetype(self) -> None:
        with patch("kukicha.audio_types.mimetypes.guess_type", return_value=(None, None)):
            self.assertEqual(audio_mime_type(Path("track.flac")), "audio/flac")

    def test_known_extensions_canonicalize_declared_content_type(self) -> None:
        self.assertEqual(
            audio_content_type_for_name("track.flac", "audio/x-flac"),
            "audio/flac",
        )
        self.assertEqual(
            audio_content_type_for_name("track.m4a", "audio/mp4a-latm"),
            "audio/mp4",
        )
        self.assertEqual(
            audio_content_type_for_name("cover.jpg", "binary/octet-stream"),
            "image/jpeg",
        )

    def test_mpeg4_audio_extensions_use_mp4_audio_mime_type(self) -> None:
        for name in ("track.m4a", "track.m4b", "track.m4p", "track.m4r"):
            with self.subTest(name=name):
                self.assertEqual(audio_mime_type(Path(name)), "audio/mp4")

    def test_oga_uses_ogg_audio_mime_type(self) -> None:
        self.assertEqual(audio_mime_type(Path("track.oga")), "audio/ogg")

    def test_opus_uses_ogg_audio_mime_type(self) -> None:
        self.assertEqual(audio_mime_type(Path("track.opus")), "audio/ogg")

    def test_mpeg4_audio_extensions_use_mp4_codec_detection(self) -> None:
        audio = SimpleNamespace(info=SimpleNamespace(codec="mp4a.40.2"))
        for name in ("track.m4a", "track.m4b", "track.m4p", "track.m4r"):
            with (
                self.subTest(name=name),
                patch("mutagen.File", return_value=audio) as mutagen_file,
            ):
                self.assertEqual(mpeg4_audio_codec_for_path(Path(name)), "mp4a.40.2")
                mutagen_file.assert_called_once_with(Path(name))

    def test_non_mpeg4_audio_extension_skips_mp4_codec_detection(self) -> None:
        with patch("mutagen.File") as mutagen_file:
            self.assertEqual(mpeg4_audio_codec_for_path(Path("track.oga")), "")
            mutagen_file.assert_not_called()


class PlayerTrackDurationTest(unittest.TestCase):
    def test_track_duration_uses_whole_elapsed_seconds_without_rounding(self) -> None:
        self.assertEqual(format_track_duration(59.999), "0:59")
        self.assertEqual(format_track_duration(60.999), "1:00")
        self.assertEqual(format_track_duration(3600.999), "1:00:00")


class PlayerAlbumPlaybackTrackPayloadsTest(unittest.TestCase):
    def test_preserves_album_queue_order_and_includes_control_metadata(self) -> None:
        api = Mock()
        api.get_tracks_by_ids.return_value = (
            PlaylistTrack(
                track_id=2,
                album_id="artist::album",
                path="/music/Artist/Album/02.m4a",
                file_type="m4a",
                artist="Artist",
                album_artist="Artist",
                album_artists=("Artist One", "Artist Two"),
                album="Album",
                title="Second",
                track_number="2",
            ),
            PlaylistTrack(
                track_id=1,
                album_id="artist::album",
                path="/music/Artist/Album/01.m4a",
                file_type="m4a",
                artist="Artist",
                album_artist="Artist",
                album="Album",
                title="First",
                track_number="1",
            ),
        )
        albums = (
            AlbumDetails(
                album_id="artist::album",
                artist="Artist",
                album="Album",
                year=2000,
                track_count=2,
                track_ids=(2, 1),
            ),
        )

        payloads = album_playback_track_payloads(api, albums)

        api.get_tracks_by_ids.assert_called_once_with([2, 1])
        self.assertEqual(
            [item["trackId"] for item in payloads["artist::album"]],
            [2, 1],
        )
        self.assertEqual(payloads["artist::album"][0]["title"], "Second")
        self.assertEqual(payloads["artist::album"][0]["albumArtist"], "Artist")
        self.assertEqual(payloads["artist::album"][0]["albumArtists"], ("Artist One", "Artist Two"))
        self.assertEqual(payloads["artist::album"][0]["album"], "Album")
        self.assertEqual(payloads["artist::album"][0]["audioUrl"], "/audio/2")

    def test_external_playlist_item_uses_negative_playback_id_and_direct_url(self) -> None:
        item = PlaylistItem(
            playlist_item_id=12,
            playlist_id=3,
            position=4,
            path="https://ice6.somafm.com/deepspaceone-128-mp3",
            title="SomaFM: Deep Space One",
            duration_seconds=0.0,
            duration_is_indeterminate=True,
            genre="Ambient",
        )
        playlist = PlaylistDetails(
            playlist_id=3,
            name="Streams",
            root_position=0,
            items=(item,),
        )

        payload = track_playback_payload(playlist_item_view(item, playlist))

        self.assertEqual(payload["trackId"], -12)
        self.assertEqual(payload["audioUrl"], "https://ice6.somafm.com/deepspaceone-128-mp3")
        self.assertEqual(payload["title"], "SomaFM: Deep Space One")
        self.assertEqual(payload["album"], "Streams")
        self.assertEqual(payload["albumArtist"], "")
        self.assertEqual(payload["albumArtists"], ())
        self.assertEqual(payload["albumId"], "playlist:3")
        self.assertEqual(payload["artUrl"], playlist_cover_data_url(playlist_cover_svg("Streams")))
        self.assertIsNone(payload["durationSeconds"])
        self.assertTrue(payload["durationIsIndeterminate"])

    def test_tracked_playlist_item_uses_playlist_position_as_track_number(self) -> None:
        item = PlaylistItem(
            playlist_item_id=12,
            playlist_id=3,
            position=4,
            path="/music/Album/02.flac",
            track_id=7,
            track=PlaylistTrack(
                track_id=7,
                album_id="artist::album",
                path="/music/Album/02.flac",
                file_type="flac",
                artist="Artist",
                album_artist="Artist",
                album="Album",
                title="Second",
                track_number="2",
            ),
        )

        view = playlist_item_view(item)

        self.assertEqual(view.track_id, -12)
        self.assertEqual(view.audio_url, "/playlist-audio/12")
        self.assertEqual(view.album_id, "playlist:3")
        self.assertEqual(view.art_url, "/art/32/7")
        self.assertEqual(view.album_art_url, "/art/250/7")
        self.assertFalse(view.uses_playlist_cover)
        self.assertEqual(view.album_artist, "")
        self.assertEqual(view.display_album, "Playlist")
        self.assertEqual(view.track_number, "5")
        self.assertEqual(view.display_title, "Second")
        self.assertEqual(view.table_title, "Artist - Second")
        self.assertEqual(view.queue_title, "Artist - Second")

    def test_tracked_playlist_item_can_use_gapless_display_position(self) -> None:
        item = PlaylistItem(
            playlist_item_id=12,
            playlist_id=3,
            position=8,
            path="/music/Album/02.flac",
            track_id=7,
            track=PlaylistTrack(
                track_id=7,
                album_id="artist::album",
                path="/music/Album/02.flac",
                file_type="flac",
                artist="Artist",
                album_artist="Artist",
                album="Album",
                title="Second",
                track_number="2",
            ),
        )

        view = playlist_item_view(item, display_position=1)

        self.assertEqual(view.track_number, "2")

    def test_external_playlist_item_prefers_cover_url_thumbnail(self) -> None:
        item = PlaylistItem(
            playlist_item_id=12,
            playlist_id=3,
            position=4,
            path="https://ice6.somafm.com/deepspaceone-128-mp3",
            title="SomaFM: Deep Space One",
            cover_url="https://example.test/cover.jpg",
        )

        view = playlist_item_view(item)

        self.assertEqual(view.art_url, "https://example.test/cover.jpg")
        self.assertEqual(view.album_art_url, "https://example.test/cover.jpg")
        self.assertEqual(view.queue_title, "SomaFM: Deep Space One")
        self.assertFalse(view.uses_playlist_cover)


class PlayerPlaylistMembershipTest(unittest.TestCase):
    def test_playlist_menu_options_by_track_id_marks_memberships_per_track(self) -> None:
        with TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            database = root / "kukicha.sqlite"
            first_track_path = root / "Amon Tobin" / "Permutation" / "12 Nova.flac"
            second_track_path = root / "Amon Tobin" / "Permutation" / "01 Like Regular Chickens.flac"
            save_library(
                MusicLibrary(
                    roots=[str(root)],
                    tracks=[
                        TrackRecord(
                            path=str(first_track_path),
                            root_position=0,
                            file_type="flac",
                            artist="Amon Tobin",
                            album_artist="Amon Tobin",
                            album="Permutation",
                            title="Nova (Permutation)",
                        ),
                        TrackRecord(
                            path=str(second_track_path),
                            root_position=0,
                            file_type="flac",
                            artist="Amon Tobin",
                            album_artist="Amon Tobin",
                            album="Permutation",
                            title="Like Regular Chickens",
                        ),
                    ],
                    playlists=[
                        PlaylistRecord(
                            path=str(root / "mix.m3u8"),
                            root_position=0,
                            name="Mix",
                            items=[PlaylistItemRecord(path=str(first_track_path))],
                        ),
                        PlaylistRecord(
                            path=str(root / "empty.m3u8"),
                            root_position=0,
                            name="Empty",
                        ),
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-04-25T00:00:00+00:00",
                ),
                database,
            )

            options = playlist_menu_options_by_track_id(database, [1, 2])

        self.assertEqual([option.name for option in options[1]], ["Empty", "Mix"])
        self.assertEqual([option.checked for option in options[1]], [False, True])
        self.assertEqual([option.checked for option in options[2]], [False, False])

    def test_playlist_menu_options_exclude_file_import_playlists(self) -> None:
        with TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            database = root / "kukicha.sqlite"
            first_track_path = root / "Amon Tobin" / "Permutation" / "12 Nova.flac"
            second_track_path = root / "Amon Tobin" / "Permutation" / "01 Like Regular Chickens.flac"
            save_library(
                MusicLibrary(
                    roots=[str(root)],
                    tracks=[
                        TrackRecord(
                            path=str(first_track_path),
                            root_position=0,
                            file_type="flac",
                            artist="Amon Tobin",
                            album_artist="Amon Tobin",
                            album="Permutation",
                            title="Nova (Permutation)",
                        ),
                        TrackRecord(
                            path=str(second_track_path),
                            root_position=0,
                            file_type="flac",
                            artist="Amon Tobin",
                            album_artist="Amon Tobin",
                            album="Permutation",
                            title="Like Regular Chickens",
                        ),
                    ],
                    playlists=[
                        PlaylistRecord(
                            path=str(root / "empty.m3u8"),
                            root_position=0,
                            name="Empty",
                        ),
                        PlaylistRecord(
                            path=str(root / "mix.m3u8"),
                            root_position=0,
                            name="Mix",
                            items=[
                                PlaylistItemRecord(path=str(first_track_path)),
                            ],
                        ),
                        PlaylistRecord(
                            path=str(root / "streams.m3u8"),
                            root_position=0,
                            name="Streams",
                            kind="remote",
                            source="file_import",
                            items=[
                                PlaylistItemRecord(
                                    path="https://example.test/one.mp3",
                                    title="One",
                                ),
                                PlaylistItemRecord(
                                    path="HTTPS://example.test/two.mp3",
                                    title="Two",
                                ),
                            ],
                        ),
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-04-25T00:00:00+00:00",
                ),
                database,
            )

            options = playlist_menu_options_by_track_id(database, [1])

        self.assertEqual([option.name for option in options[1]], ["Empty", "Mix"])

    def test_set_track_playlist_membership_updates_database_only(self) -> None:
        with TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            database = root / "kukicha.sqlite"
            track_path = root / "Amon Tobin" / "Permutation" / "12 Nova.flac"
            save_library(
                MusicLibrary(
                    roots=[str(root)],
                    tracks=[
                        TrackRecord(
                            path=str(track_path),
                            root_position=0,
                            file_type="flac",
                            artist="Amon Tobin",
                            album_artist="Amon Tobin",
                            album="Permutation",
                            title="Nova (Permutation)",
                            duration_seconds=283.0,
                        )
                    ],
                    playlists=[
                        PlaylistRecord(
                            name="Mix",
                            created_at="2026-04-25T00:00:00+00:00",
                            updated_at="2026-04-25T00:00:00+00:00",
                        )
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-04-25T00:00:00+00:00",
                ),
                database,
            )

            initial_options = playlist_menu_options_by_track_id(database, [1])[1]
            with patch(
                "kukicha.use_case.commands.playlists.utc_now_iso",
                return_value="2026-04-25T01:00:00+00:00",
            ):
                added = set_track_playlist_membership_database(database, 1, 1, True)
            with connect_database(database, create=False) as connection:
                rows_after_add_db = list(connection.execute("SELECT * FROM library_playlist_items"))
                playlist_after_add = connection.execute(
                    """
                    SELECT created_at, updated_at
                    FROM library_playlists
                    WHERE playlist_id = 1
                    """
                ).fetchone()
            with patch(
                "kukicha.use_case.commands.playlists.utc_now_iso",
                return_value="2026-04-25T02:00:00+00:00",
            ):
                removed = set_track_playlist_membership_database(database, 1, 1, False)
            with connect_database(database, create=False) as connection:
                rows = list(connection.execute("SELECT * FROM library_playlist_items"))
                playlist_after_remove = connection.execute(
                    """
                    SELECT created_at, updated_at
                    FROM library_playlists
                    WHERE playlist_id = 1
                    """
                ).fetchone()

        self.assertFalse(initial_options[0].checked)
        self.assertTrue(added["checked"])
        self.assertEqual(len(rows_after_add_db), 1)
        self.assertEqual(str(rows_after_add_db[0]["path"]), str(track_path))
        self.assertEqual(int(rows_after_add_db[0]["track_id"]), 1)
        self.assertEqual(str(playlist_after_add["created_at"]), "2026-04-25T00:00:00+00:00")
        self.assertEqual(str(playlist_after_add["updated_at"]), "2026-04-25T01:00:00+00:00")
        self.assertFalse(removed["checked"])
        self.assertEqual(rows, [])
        self.assertEqual(str(playlist_after_remove["created_at"]), "2026-04-25T00:00:00+00:00")
        self.assertEqual(str(playlist_after_remove["updated_at"]), "2026-04-25T02:00:00+00:00")

    def test_set_track_playlist_membership_rejects_file_import_playlist(self) -> None:
        with TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            database = root / "kukicha.sqlite"
            track_path = root / "Amon Tobin" / "Permutation" / "12 Nova.flac"
            save_library(
                MusicLibrary(
                    roots=[str(root)],
                    tracks=[
                        TrackRecord(
                            path=str(track_path),
                            root_position=0,
                            file_type="flac",
                            artist="Amon Tobin",
                            album_artist="Amon Tobin",
                            album="Permutation",
                            title="Nova (Permutation)",
                            duration_seconds=283.0,
                        )
                    ],
                    playlists=[
                        PlaylistRecord(
                            path=str(root / "streams.m3u8"),
                            root_position=0,
                            name="Streams",
                            kind="remote",
                            source="file_import",
                            items=[
                                PlaylistItemRecord(
                                    path="https://example.test/one.mp3",
                                    title="One",
                                )
                            ],
                        )
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-04-25T00:00:00+00:00",
                ),
                database,
            )

            with self.assertRaisesRegex(ValueError, "file-import playlists are read-only"):
                set_track_playlist_membership_database(database, 1, 1, True)

            with connect_database(database, create=False) as connection:
                tracked_rows = list(
                    connection.execute(
                        """
                        SELECT *
                        FROM library_playlist_items
                        WHERE track_id IS NOT NULL
                        """
                    )
                )

        self.assertEqual(tracked_rows, [])

    def test_track_table_uses_playlist_cover_only_without_real_thumbnail(self) -> None:
        environment = build_template_environment()
        template = environment.get_template("player/_track_table.html")
        queue_state = PlayerQueueState(track_ids=[])
        tracked_item = playlist_item_view(
            PlaylistItem(
                playlist_item_id=12,
                playlist_id=3,
                position=0,
                path="/music/Album/02.flac",
                track_id=7,
                track=PlaylistTrack(
                    track_id=7,
                    album_id="artist::album",
                    path="/music/Album/02.flac",
                    file_type="flac",
                    artist="Artist",
                    album_artist="Artist",
                    album="Album",
                    title="Second",
                ),
            )
        )
        external_item = playlist_item_view(
            PlaylistItem(
                playlist_item_id=13,
                playlist_id=3,
                position=1,
                path="https://example.test/stream",
                title="Stream",
                duration_is_indeterminate=True,
            )
        )

        tracked_html = template.render(
            table_rows=[{"track": tracked_item, "group_label": ""}],
            is_queue=False,
            queue_state=queue_state,
        )
        external_html = template.render(
            table_rows=[{"track": external_item, "group_label": ""}],
            is_queue=False,
            queue_state=queue_state,
        )

        self.assertIn('src="/art/32/7"', tracked_html)
        self.assertNotIn("playlist-cover-image", tracked_html)
        self.assertIn("playlist-cover-image", external_html)
        self.assertIn("data:image/svg+xml", external_html)
        self.assertIn('data-duration-is-indeterminate="1"', external_html)
        self.assertIn("duration-infinity-icon", external_html)

    def test_track_table_fills_playlist_icon_for_playlist_membership(self) -> None:
        with TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            database = root / "kukicha.sqlite"
            track_path = root / "Amon Tobin" / "Permutation" / "12 Nova.flac"
            save_library(
                MusicLibrary(
                    roots=[str(root)],
                    tracks=[
                        TrackRecord(
                            path=str(track_path),
                            root_position=0,
                            file_type="flac",
                            artist="Amon Tobin",
                            album_artist="Amon Tobin",
                            album="Permutation",
                            title="Nova (Permutation)",
                        )
                    ],
                    playlists=[
                        PlaylistRecord(
                            path=str(root / "mix.m3u8"),
                            root_position=0,
                            name="Mix",
                            items=[PlaylistItemRecord(path=str(track_path))],
                        )
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-04-25T00:00:00+00:00",
                ),
                database,
            )
            view = track_view(LibraryQueries(database).get_track(1))

        html = build_template_environment().get_template("player/_track_table.html").render(
            table_rows=[{"track": view, "group_label": ""}],
            is_queue=False,
            queue_state=PlayerQueueState(track_ids=[]),
        )

        self.assertTrue(view.has_playlist_membership)
        self.assertIn("has-playlist-membership", html)
        self.assertIn("playlist-icon-filled", html)

    def test_track_table_renders_preloaded_playlist_options(self) -> None:
        view = make_track_view(
            7,
            root_position=0,
            path="/music/Album/07.mp3",
            library_track_id=7,
            playlist_options=(
                PlaylistMenuOption(
                    playlist_id=3,
                    name="Morning",
                    checked=True,
                ),
                PlaylistMenuOption(
                    playlist_id=4,
                    name="Night",
                    checked=False,
                ),
            ),
        )

        html = build_template_environment().get_template("player/_track_table.html").render(
            table_rows=[{"track": view, "group_label": ""}],
            is_queue=False,
            queue_state=PlayerQueueState(track_ids=[]),
        )

        self.assertIn('data-track-id="7"', html)
        self.assertIn('data-playlist-id="3" checked', html)
        self.assertIn('data-playlist-id="4" ', html)
        self.assertIn("<span>Morning</span>", html)
        self.assertIn('data-playlist-create-for-track data-track-id="7"', html)
        self.assertIn('placeholder="New playlist"', html)
        self.assertNotIn("Loading playlists...", html)

    def test_track_table_renders_empty_preloaded_playlist_options(self) -> None:
        view = make_track_view(
            7,
            root_position=0,
            path="/music/Album/07.mp3",
            library_track_id=7,
            playlist_options=(),
        )

        html = build_template_environment().get_template("player/_track_table.html").render(
            table_rows=[{"track": view, "group_label": ""}],
            is_queue=False,
            queue_state=PlayerQueueState(track_ids=[]),
        )

        self.assertIn("No playlists found.", html)
        self.assertIn('data-playlist-create-for-track data-track-id="7"', html)
        self.assertNotIn("Loading playlists...", html)

    def test_album_page_tracks_use_action_menu_with_bookmark_submenu(self) -> None:
        album = AlbumDetails(
            album_id="aphex-twin::selected-ambient-works-volume-ii",
            artist="Aphex Twin",
            album_artists=("Aphex Twin",),
            album="Selected Ambient Works Volume II",
            year=None,
            track_count=1,
        )
        view = replace(
            make_track_view(
                7,
                root_position=0,
                path="/music/Album/07.mp3",
                library_track_id=7,
                playlist_options=(
                    PlaylistMenuOption(
                        playlist_id=3,
                        name="Morning",
                        checked=True,
                    ),
                ),
            ),
            has_playlist_membership=True,
        )

        html = build_template_environment().get_template("player/album.html").render(
            album=album,
            album_back_url="/albums",
            album_edit_page_url="/albums/aphex-twin::selected-ambient-works-volume-ii/edit",
            album_artist_links=album_artist_links(album, AlbumListQuery()),
            album_genre_links=(),
            album_year_text="",
            album_style_links=(),
            track_sections=(
                {
                    "label": "",
                    "table_rows": ({"track": view, "group_label": ""},),
                    "meta": (),
                },
            ),
        )

        self.assertIn("album-action-menu-button", html)
        self.assertIn('data-queue-album data-queue-append', html)
        self.assertIn(
            'href="/recommendations/radio/album/aphex-twin::selected-ambient-works-volume-ii" data-nav>Album Radio</a>',
            html,
        )
        self.assertIn('class="filter-menu track-action-menu"', html)
        self.assertIn('data-queue-track data-queue-append', html)
        self.assertIn(
            'href="/recommendations/radio/track/7" data-nav>Track Radio</a>',
            html,
        )
        self.assertEqual(html.count("Add to Queue"), 2)
        self.assertIn("track-action-submenu has-playlist-membership", html)
        self.assertIn("Bookmark", html)
        self.assertIn('data-playlist-id="3" checked', html)
        self.assertIn("<span>Morning</span>", html)
        self.assertNotIn("queue-add-icon", html)

    def test_playlist_page_hides_playlist_bookmark_control(self) -> None:
        view = make_track_view(
            7,
            root_position=0,
            path="/music/Album/07.mp3",
            library_track_id=7,
            playlist_options=(
                PlaylistMenuOption(
                    playlist_id=3,
                    name="Morning",
                    checked=True,
                ),
            ),
        )

        html = build_template_environment().get_template("player/playlist.html").render(
            playlist=PlaylistDetails(
                playlist_id=3,
                name="Morning",
                root_position=0,
                items=(PlaylistItem(playlist_item_id=1, playlist_id=3, position=0, path=view.path),),
            ),
            playlist_back_url="/",
            playlist_index_url="/?is_playlist=1",
            playlist_cover_url="data:image/svg+xml,cover",
            playlist_edit_page_url="/playlists/3/edit",
            table_rows=[{"track": view, "group_label": ""}],
            playlist_track_meta=(),
            queue_state=PlayerQueueState(track_ids=[]),
        )

        self.assertIn("data-queue-track", html)
        self.assertNotIn("Album Radio", html)
        self.assertNotIn("data-playlist-menu", html)
        self.assertNotIn("data-playlist-toggle", html)
        self.assertNotIn("Add to Playlists", html)

    def test_track_table_can_show_track_artist_after_cover(self) -> None:
        environment = build_template_environment()
        template = environment.get_template("player/_track_table.html")
        html = template.render(
            table_rows=[
                {
                    "track": make_track_view(
                        7,
                        root_position=0,
                        path="/music/Album/07.mp3",
                        album_artist="Album Artist",
                        artist="Track Artist",
                    ),
                    "group_label": "",
                }
            ],
            is_queue=False,
            queue_state=PlayerQueueState(track_ids=[]),
            show_track_artist=True,
        )

        self.assertLess(html.index("<th>Cover</th>"), html.index("<th>Artist</th>"))
        self.assertLess(html.index('<td class="cover-cell">'), html.index("Track Artist"))
        self.assertIn('<td class="track-artist">Track Artist</td>', html)

    def test_track_table_can_show_track_artist_display_lines(self) -> None:
        view = replace(
            make_track_view(
                7,
                root_position=0,
                path="/music/Album/07.mp3",
                album_artist="Album Artist",
                artist="Spiritualized;J. Spaceman;Sean Cook",
            ),
            track_artist_display_lines=("Spiritualized", "J. Spaceman", "Sean Cook"),
        )

        html = build_template_environment().get_template("player/_track_table.html").render(
            table_rows=[{"track": view, "group_label": ""}],
            is_queue=False,
            queue_state=PlayerQueueState(track_ids=[]),
            show_track_artist=True,
        )

        self.assertIn('<td class="track-artist track-artist-lines">', html)
        self.assertIn('<span class="track-artist-line">Spiritualized</span>', html)
        self.assertIn("<br>", html)
        self.assertIn('<span class="track-artist-line">J. Spaceman</span>', html)
        self.assertIn('<span class="track-artist-line">Sean Cook</span>', html)

    def test_track_artist_display_lines_follow_default_display_rules(self) -> None:
        views = track_views_with_artist_display_lines(
            [
                make_track_view(
                    7,
                    root_position=0,
                    path="/music/Album/07.mp3",
                    artist="Spiritualized;J. Spaceman;Sean Cook",
                ),
                make_track_view(
                    8,
                    root_position=0,
                    path="/music/Album/08.mp3",
                    artist="Brian Eno - Jon Hopkins",
                ),
            ],
            split_patterns=DEFAULT_ALBUM_ARTIST_SPLIT_PATTERNS,
        )

        self.assertEqual(
            views[0].track_artist_display_lines,
            ("Spiritualized", "J. Spaceman", "Sean Cook"),
        )
        self.assertEqual(
            views[1].track_artist_display_lines,
            ("Brian Eno - Jon Hopkins",),
        )
        self.assertEqual(views[0].artist, "Spiritualized;J. Spaceman;Sean Cook")

    def test_track_table_renders_queue_error_status(self) -> None:
        view = make_track_view(
            7,
            root_position=0,
            path="/music/Album/07.mp3",
        )

        html = build_template_environment().get_template("player/_track_table.html").render(
            table_rows=[
                {
                    "track": view,
                    "group_label": "",
                    "queue_position": 0,
                    "queue_status": "Error",
                }
            ],
            is_queue=True,
            queue_state=PlayerQueueState(track_ids=[7], errored_track_ids=[7]),
        )

        self.assertIn('<th class="queue-status-head">Queue</th>', html)
        self.assertIn('<span class="queue-status-label">Error</span>', html)

    def test_track_table_renders_queue_delete_control_and_unavailable_marker(self) -> None:
        view = make_track_view(
            7,
            root_position=0,
            path="/music/Album/07.mp3",
        )

        html = build_template_environment().get_template("player/_track_table.html").render(
            table_rows=[
                {
                    "track": view,
                    "group_label": "",
                    "queue_position": 0,
                    "queue_status": "Unavailable",
                    "queue_unavailable": True,
                }
            ],
            is_queue=True,
            queue_state=PlayerQueueState(track_ids=[7], unavailable_track_ids=[7]),
        )

        self.assertIn('data-delete-queue-track', html)
        self.assertIn('data-unavailable="1"', html)
        self.assertIn('<span class="queue-status-label">Unavailable</span>', html)

    def test_queue_template_hides_grouping_rows(self) -> None:
        view = make_track_view(
            7,
            root_position=0,
            path="/music/Album/07.mp3",
        )

        html = build_template_environment().get_template("player/queue.html").render(
            queue_rows=[{"track": view}],
            table_rows=[
                {
                    "track": view,
                    "group_label": "Work Header",
                    "queue_position": 0,
                    "queue_status": "Next",
                    "queue_unavailable": False,
                }
            ],
            queue_meta="1 track - 0 played",
            queue_duration_text="",
            queue_back_url="/",
        )

        self.assertNotIn("grouping-row", html)
        self.assertNotIn("Work Header", html)
        self.assertIn('data-track-id="7"', html)
        self.assertIn('<span class="queue-status-label">Next</span>', html)

    def test_valid_playback_ids_accepts_tracks_and_external_playlist_items(self) -> None:
        api = Mock()
        api.get_tracks_by_ids.return_value = (
            PlaylistTrack(track_id=7, path="/music/07.flac"),
        )
        api.get_playlist_items_by_ids.return_value = (
            PlaylistItem(
                playlist_item_id=12,
                playlist_id=3,
                position=0,
                path="https://example.test/stream",
            ),
        )

        self.assertEqual(valid_playback_ids(api, [7, -12, 999, -404]), [7, -12])


class PlayerConfigTest(unittest.TestCase):
    def write_password_hash(self, path: Path, *, mode: int = 0o600, text: str = TEST_ARGON2ID_HASH) -> None:
        path.write_text(f"{text}\n", encoding="utf-8")
        path.chmod(mode)

    def write_open_subsonic_secret(
        self,
        path: Path,
        *,
        mode: int = 0o600,
        text: str = "os-pass",
    ) -> None:
        path.write_text(f"{text}\n", encoding="utf-8")
        path.chmod(mode)

    def write_config(self, config_path: Path, text: str) -> Path:
        password_hash_file = config_path.parent / "password.hash"
        self.write_password_hash(password_hash_file)
        config_body = text.rstrip()
        auth_section = "\n".join(
            (
                "[auth]",
                "username = 'listener'",
                "password_hash_file = 'password.hash'",
            )
        )
        output = f"{config_body}\n\n{auth_section}\n" if config_body else f"{auth_section}\n"
        config_path.write_text(output, encoding="utf-8")
        return password_hash_file

    def test_default_toast_timeout_is_five_seconds(self) -> None:
        self.assertEqual(DEFAULT_TOAST_TIMEOUT_MS, 5000)

    def test_default_player_log_level_is_info(self) -> None:
        self.assertEqual(DEFAULT_PLAYER_LOG_LEVEL, "INFO")

    def test_kukicha_version_uses_package_metadata_without_fallback(self) -> None:
        with patch("kukicha.app_metadata.version", side_effect=PackageNotFoundError):
            with self.assertRaises(PackageNotFoundError):
                kukicha_version()

    def test_control_accent_preserves_readable_accents_and_lifts_low_contrast_dim_accents(self) -> None:
        dim = APPEARANCE_THEMES["dim"]
        cyan = player_accent_theme("cyan").accent
        brown = player_accent_theme("brown").accent

        self.assertEqual(derived_control_accent(cyan, dim), cyan)
        resolved_brown = derived_control_accent(brown, dim)

        self.assertNotEqual(resolved_brown, brown)
        self.assertGreaterEqual(
            contrast_ratio(resolved_brown, dim.surface),
            CONTROL_ACCENT_MINIMUM_CONTRAST,
        )

    def test_load_player_options_reads_toml_config(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            config_path = temp_path / "kukicha.toml"
            open_subsonic_secret = temp_path / "os.secret"
            self.write_open_subsonic_secret(open_subsonic_secret)
            self.write_config(
                config_path,
                "\n".join(
                    (
                        "log_level = 'info'",
                        "database_path = 'library.sqlite'",
                        "roots = ['music-a', 'music-b']",
                        "remote_workers = 6",
                        "ffmpeg_path = 'bin/ffmpeg'",
                        "youtube_download_root = 'music-a'",
                        "prefer_musicbrainz_english_aliases = false",
                        "host = '0.0.0.0'",
                        "port = 43210",
                        "trusted_proxy_headers = true",
                        "accent_color = 'Dark-Sky-Blue'",
                        "appearance = 'DaRk'",
                        "toast_timeout_ms = 12000",
                        "album_artist_split_patterns = ['&', '/']",
                        "[opensubsonic]",
                        "mount_prefix = '/sonic/'",
                        "secret_file = 'os.secret'",
                    )
                ),
            )

            options = load_player_options(config_path)

            self.assertEqual(options.config_path, config_path.resolve())
            self.assertEqual(options.database, (temp_path / "library.sqlite").resolve())
            self.assertEqual(
                options.roots,
                (
                    (temp_path / "music-a").resolve(),
                    (temp_path / "music-b").resolve(),
                ),
            )
            self.assertEqual(options.remote_workers, 6)
            self.assertEqual(options.ffmpeg_path, (temp_path / "bin" / "ffmpeg").resolve())
            self.assertEqual(options.youtube_download_root, "music-a")
            self.assertFalse(options.prefer_musicbrainz_english_aliases)
            self.assertEqual(options.host, "0.0.0.0")
            self.assertEqual(options.port, 43210)
            self.assertTrue(options.trusted_proxy_headers)
            self.assertEqual(options.log_level, "INFO")
            self.assertEqual(options.accent_color, "dark-sky-blue")
            self.assertEqual(options.appearance, "dark")
            self.assertEqual(options.toast_timeout_ms, 12000)
            self.assertEqual(options.album_artist_split_patterns, ("&", "/"))
            self.assertIsNotNone(options.auth)
            assert options.auth is not None
            self.assertEqual(options.auth.username, "listener")
            self.assertEqual(options.auth.password_hash_file, (temp_path / "password.hash").resolve())
            self.assertEqual(options.auth.cookie_max_age, DEFAULT_AUTH_COOKIE_MAX_AGE)
            self.assertEqual(options.auth.cookie_name, DEFAULT_AUTH_COOKIE_NAME)
            self.assertIsNotNone(options.opensubsonic)
            assert options.opensubsonic is not None
            self.assertEqual(options.opensubsonic.mount_prefix, "/sonic")
            self.assertEqual(options.opensubsonic.secret_file, open_subsonic_secret.resolve())

    def test_load_player_options_reads_auth_cookie_config(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            config_path = temp_path / "kukicha.toml"
            password_hash_file = temp_path / "auth" / "password.hash"
            password_hash_file.parent.mkdir()
            self.write_password_hash(password_hash_file)
            config_path.write_text(
                "\n".join(
                    (
                        "[auth]",
                        "username = 'listener'",
                        "password_hash_file = 'auth/password.hash'",
                        "cookie_max_age = '365d'",
                        "cookie_name = 'kukicha_session'",
                    )
                ),
                encoding="utf-8",
            )

            options = load_player_options(config_path)

            self.assertIsNotNone(options.auth)
            assert options.auth is not None
            self.assertEqual(options.auth.username, "listener")
            self.assertEqual(options.auth.password_hash_file, password_hash_file.resolve())
            self.assertEqual(options.auth.cookie_max_age, "365d")
            self.assertEqual(options.auth.cookie_max_age_seconds, 365 * 24 * 60 * 60)
            self.assertEqual(options.auth.cookie_name, "kukicha_session")

    def test_load_player_options_rejects_missing_auth_section(self) -> None:
        with TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "kukicha.toml"
            config_path.write_text("log_level = 'INFO'\n", encoding="utf-8")

            with self.assertRaisesRegex(PlayerConfigError, r"\[auth\] section is required"):
                load_player_options(config_path)

    def test_load_player_options_rejects_invalid_auth_config(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            config_path = temp_path / "kukicha.toml"
            password_hash_file = temp_path / "password.hash"
            self.write_password_hash(password_hash_file)
            cases = (
                (
                    "[auth]\npassword_hash_file = 'password.hash'\n",
                    "missing required key",
                ),
                (
                    "[auth]\nusername = 'listener'\npassword_hash_file = 'missing.hash'\n",
                    "does not exist",
                ),
                (
                    "[auth]\nusername = 'listener'\npassword_hash_file = 'password.hash'\n"
                    "cookie_max_age = '0d'\n",
                    "positive day duration",
                ),
                (
                    "[auth]\nusername = 'listener'\npassword_hash_file = 'password.hash'\n"
                    "cookie_name = 'bad cookie'\n",
                    "valid HTTP cookie name",
                ),
                (
                    "[auth]\nusername = 'listener'\npassword_hash_file = 'password.hash'\n"
                    "bogus = true\n",
                    "unsupported auth key",
                ),
            )
            for text, message in cases:
                with self.subTest(message=message):
                    config_path.write_text(text, encoding="utf-8")
                    with self.assertRaisesRegex(PlayerConfigError, message):
                        load_player_options(config_path)

    def test_load_player_options_rejects_unsafe_password_hash_file(self) -> None:
        if os.name == "nt":
            self.skipTest("POSIX file mode checks do not apply on Windows")
        with TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "kukicha.toml"
            password_hash_file = Path(tempdir) / "password.hash"
            self.write_password_hash(password_hash_file, mode=0o644)
            config_path.write_text(
                "[auth]\nusername = 'listener'\npassword_hash_file = 'password.hash'\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(PlayerConfigError, "permissions must be 600"):
                load_player_options(config_path)

    def test_load_player_options_rejects_invalid_opensubsonic_config(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            config_path = temp_path / "kukicha.toml"
            secret_file = temp_path / "opensubsonic.secret"
            self.write_open_subsonic_secret(secret_file)
            cases = (
                (
                    "opensubsonic = 'bad'\n",
                    "opensubsonic must be a table",
                ),
                (
                    "[opensubsonic]\nsecret_file = 'opensubsonic.secret'\n",
                    "missing required key",
                ),
                (
                    "[opensubsonic]\nmount_prefix = 'sonic'\nsecret_file = 'opensubsonic.secret'\n",
                    "mount_prefix must start with /",
                ),
                (
                    "[opensubsonic]\nmount_prefix = '/'\nsecret_file = 'missing.secret'\n",
                    "does not exist",
                ),
                (
                    "[opensubsonic]\nmount_prefix = '/'\nsecret_file = 'opensubsonic.secret'\nBogus = true\n",
                    "unsupported opensubsonic key",
                ),
            )
            for text, message in cases:
                with self.subTest(message=message):
                    self.write_config(config_path, text)
                    with self.assertRaisesRegex(PlayerConfigError, message):
                        load_player_options(config_path)

    def test_load_player_options_rejects_unsafe_opensubsonic_secret_file(self) -> None:
        if os.name == "nt":
            self.skipTest("POSIX file mode checks do not apply on Windows")
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            config_path = temp_path / "kukicha.toml"
            secret_file = temp_path / "opensubsonic.secret"
            self.write_open_subsonic_secret(secret_file, mode=0o644)
            self.write_config(
                config_path,
                "[opensubsonic]\nmount_prefix = '/'\nsecret_file = 'opensubsonic.secret'\n",
            )

            with self.assertRaisesRegex(PlayerConfigError, "permissions must be 600"):
                load_player_options(config_path)

    def test_load_player_options_reads_remote_roots(self) -> None:
        with TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "kukicha.toml"
            self.write_config(
                config_path,
                "\n".join(
                    (
                        "[[remote_roots]]",
                        "name = 'wasabi-music'",
                        "endpoint_url = 'https://s3.us-east-1.wasabisys.com/'",
                        "bucket = 'com.cconroy.music'",
                        "prefix = '/tracks'",
                        "profile = 'wasabi-music'",
                        "region = 'us-east-1'",
                        "addressing_style = 'path'",
                    )
                ),
            )

            options = load_player_options(config_path)

        self.assertEqual(len(options.remote_roots), 1)
        remote = options.remote_roots[0]
        self.assertEqual(remote.name, "wasabi-music")
        self.assertEqual(remote.endpoint_url, "https://s3.us-east-1.wasabisys.com")
        self.assertEqual(remote.bucket, "com.cconroy.music")
        self.assertEqual(remote.prefix, "tracks/")
        self.assertEqual(remote.profile, "wasabi-music")
        self.assertEqual(remote.region, "us-east-1")
        self.assertEqual(remote.addressing_style, "path")

    def test_load_player_options_rejects_invalid_remote_roots(self) -> None:
        with TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "kukicha.toml"
            cases = (
                (
                    "[remote_roots]\nname = 'music'\n",
                    "remote_roots must be an array of tables",
                ),
                (
                    "[[remote_roots]]\n"
                    "name = 'music'\n"
                    "endpoint_url = 'https://s3.example.test'\n"
                    "bucket = 'bucket'\n"
                    "secret_access_key = 'secret'\n",
                    "must not contain inline credentials",
                ),
                (
                    "[[remote_roots]]\n"
                    "name = 'all'\n"
                    "endpoint_url = 'https://s3.example.test'\n"
                    "bucket = 'bucket'\n"
                    "prefix = 'music/'\n"
                    "[[remote_roots]]\n"
                    "name = 'nested'\n"
                    "endpoint_url = 'https://s3.example.test'\n"
                    "bucket = 'bucket'\n"
                    "prefix = 'music/live/'\n",
                    "must not contain nested prefixes",
                ),
                (
                    "[[remote_roots]]\n"
                    "name = 'music'\n"
                    "endpoint_url = 'https://s3.example.test'\n"
                    "bucket = 'bucket'\n"
                    "addressing_style = 'dns'\n",
                    "addressing_style must be one of",
                ),
                (
                    "[[remote_roots]]\n"
                    "name = 'music'\n"
                    "endpoint_url = 'https://s3.example.test'\n"
                    "bucket = 'bucket'\n"
                    "archive_profile = 'writer'\n",
                    "unsupported remote_roots key",
                ),
            )
            for text, message in cases:
                with self.subTest(message=message):
                    self.write_config(config_path, text)
                    with self.assertRaisesRegex(PlayerConfigError, message):
                        load_player_options(config_path)

    def test_load_player_options_rejects_missing_default_config(self) -> None:
        with TemporaryDirectory() as tempdir:
            config_home = Path(tempdir)
            with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(config_home)}, clear=False):
                with self.assertRaisesRegex(PlayerConfigError, "config file does not exist"):
                    load_player_options()

    def test_load_player_options_uses_default_paths_when_default_config_exists(self) -> None:
        with TemporaryDirectory() as tempdir:
            config_home = Path(tempdir)
            config_path = config_home / "kukicha" / "kukicha.toml"
            config_path.parent.mkdir(parents=True)
            self.write_config(config_path, "")
            with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(config_home)}, clear=False):
                options = load_player_options()

            self.assertEqual(options.config_path, config_path.resolve())
            self.assertEqual(options.database, (config_home / "kukicha" / "kukicha.sqlite").resolve())
            self.assertIsNone(options.ffmpeg_path)
            self.assertIsNone(options.youtube_download_root)
            self.assertEqual(
                options.prefer_musicbrainz_english_aliases,
                DEFAULT_PREFER_MUSICBRAINZ_ENGLISH_ALIASES,
            )
            self.assertEqual(options.roots, ())
            self.assertEqual(options.host, DEFAULT_PLAYER_HOST)
            self.assertEqual(options.port, DEFAULT_PLAYER_PORT)
            self.assertEqual(options.trusted_proxy_headers, DEFAULT_TRUSTED_PROXY_HEADERS)
            self.assertIsNone(options.opensubsonic)
            self.assertEqual(options.log_level, DEFAULT_PLAYER_LOG_LEVEL)
            self.assertEqual(options.accent_color, DEFAULT_ACCENT_COLOR)
            self.assertEqual(options.appearance, "system")
            self.assertEqual(options.toast_timeout_ms, DEFAULT_TOAST_TIMEOUT_MS)
            self.assertEqual(
                options.album_artist_split_patterns,
                DEFAULT_ALBUM_ARTIST_SPLIT_PATTERNS,
            )

    def test_load_player_options_accepts_empty_album_artist_split_patterns(self) -> None:
        with TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "kukicha.toml"
            self.write_config(config_path, "album_artist_split_patterns = []\n")

            options = load_player_options(config_path)

        self.assertEqual(options.album_artist_split_patterns, ())

    def test_load_player_options_rejects_non_string_album_artist_split_patterns(self) -> None:
        with TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "kukicha.toml"
            self.write_config(config_path, "album_artist_split_patterns = ['&', 1]\n")

            with self.assertRaisesRegex(
                PlayerConfigError,
                "album_artist_split_patterns must be an array of strings",
            ):
                load_player_options(config_path)

    def test_load_player_options_rejects_invalid_roots(self) -> None:
        with TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "kukicha.toml"
            for text, message in (
                ("roots = 'music'\n", "roots must be an array of strings"),
                ("roots = ['music', 1]\n", "roots must be an array of non-empty strings"),
                ("roots = ['music', '  ']\n", "roots must be an array of non-empty strings"),
                ("roots = ['music', './music']\n", "roots must not contain duplicate paths"),
                ("roots = ['music', 'music/live']\n", "roots must not contain nested paths"),
                ("roots = ['music/live', 'music']\n", "roots must not contain nested paths"),
            ):
                with self.subTest(text=text):
                    self.write_config(config_path, text)
                    with self.assertRaisesRegex(PlayerConfigError, message):
                        load_player_options(config_path)

    def test_load_player_options_rejects_roots_nested_through_symlink(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            library = temp_path / "library"
            album = library / "album"
            album.mkdir(parents=True)
            linked_album = temp_path / "linked-album"
            linked_album_alias = temp_path / "linked-album-alias"
            try:
                linked_album.symlink_to(album, target_is_directory=True)
                linked_album_alias.symlink_to(linked_album, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"symlink unavailable: {error}")
            config_path = temp_path / "kukicha.toml"
            self.write_config(config_path, "roots = ['library', 'linked-album-alias']\n")

            with self.assertRaisesRegex(
                PlayerConfigError,
                "roots must not contain nested paths",
            ) as caught:
                load_player_options(config_path)

            message = str(caught.exception)
            self.assertIn("after resolving symbolic links", message)
            self.assertIn(str(linked_album_alias), message)
            self.assertIn(f"resolves to {album.resolve(strict=False)}", message)
            self.assertIn(str(library.resolve(strict=False)), message)

    def test_load_player_options_rejects_invalid_toast_timeouts(self) -> None:
        with TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "kukicha.toml"
            self.write_config(config_path, "toast_timeout_ms = 0\n")

            with self.assertRaisesRegex(PlayerConfigError, "toast_timeout_ms must be greater than 0"):
                load_player_options(config_path)

    def test_load_player_options_rejects_invalid_remote_workers(self) -> None:
        with TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "kukicha.toml"
            for text, message in (
                ("remote_workers = 0\n", "remote_workers must be greater than 0"),
                ("remote_workers = 'auto'\n", "remote_workers must be an integer"),
            ):
                with self.subTest(text=text):
                    self.write_config(config_path, text)

                    with self.assertRaisesRegex(PlayerConfigError, message):
                        load_player_options(config_path)

    def test_load_player_options_rejects_invalid_musicbrainz_alias_preference(self) -> None:
        with TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "kukicha.toml"
            self.write_config(config_path, "prefer_musicbrainz_english_aliases = 'yes'\n")

            with self.assertRaisesRegex(
                PlayerConfigError,
                "prefer_musicbrainz_english_aliases must be true or false",
            ):
                load_player_options(config_path)

    def test_load_player_options_rejects_invalid_trusted_proxy_headers(self) -> None:
        with TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "kukicha.toml"
            self.write_config(config_path, "trusted_proxy_headers = 'yes'\n")

            with self.assertRaisesRegex(
                PlayerConfigError,
                "trusted_proxy_headers must be true or false",
            ):
                load_player_options(config_path)

    def test_load_player_options_rejects_invalid_accent_color(self) -> None:
        with TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "kukicha.toml"
            self.write_config(config_path, "accent_color = 'plaid'\n")

            with self.assertRaisesRegex(PlayerConfigError, "accent_color must be a supported palette color"):
                load_player_options(config_path)

            self.write_config(config_path, "accent_color = 123\n")

            with self.assertRaisesRegex(PlayerConfigError, "accent_color must be a non-empty string"):
                load_player_options(config_path)

    def test_load_player_options_rejects_neutral_accent_colors(self) -> None:
        with TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "kukicha.toml"
            for value in (
                "slate",
                "#334155",
                "dark-slate",
                "#1e293b",
                "soft-white",
                "#fafafa",
                "light-border",
            ):
                with self.subTest(value=value):
                    self.write_config(config_path, f"accent_color = '{value}'\n")

                    with self.assertRaisesRegex(
                        PlayerConfigError,
                        "accent_color must be a supported palette color",
                    ):
                        load_player_options(config_path)

    def test_load_player_options_rejects_invalid_appearance(self) -> None:
        with TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "kukicha.toml"
            self.write_config(config_path, "appearance = 'sepia'\n")

            with self.assertRaisesRegex(PlayerConfigError, "appearance must be one of"):
                load_player_options(config_path)

            self.write_config(config_path, "appearance = 123\n")

            with self.assertRaisesRegex(PlayerConfigError, "appearance must be a non-empty string"):
                load_player_options(config_path)

    def test_load_player_options_accepts_system_appearance(self) -> None:
        with TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "kukicha.toml"
            self.write_config(config_path, "appearance = 'SYSTEM'\n")

            options = load_player_options(config_path)

        self.assertEqual(options.appearance, "system")

    def test_load_player_options_accepts_palette_accent_color_code(self) -> None:
        with TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "kukicha.toml"
            self.write_config(config_path, "accent_color = '#06B6D4'\n")

            options = load_player_options(config_path)

        self.assertEqual(options.accent_color, "cyan")

    def test_player_accent_theme_derives_contrast_safe_values(self) -> None:
        gray_theme = player_accent_theme("gray")
        cyan_theme = player_accent_theme("cyan")

        self.assertEqual(gray_theme.accent, "#71717a")
        self.assertEqual(gray_theme.accent_strong, "#4f4f55")
        self.assertEqual(gray_theme.accent_soft, "#eeeeef")
        self.assertEqual(gray_theme.accent_foreground, "#ffffff")
        self.assertEqual(cyan_theme.accent, "#06b6d4")
        self.assertEqual(cyan_theme.accent_strong, "#04768a")
        self.assertEqual(cyan_theme.accent_soft, "#e1f6fa")
        self.assertEqual(cyan_theme.accent_foreground, "#111827")

    def test_load_player_options_rejects_unknown_config_keys(self) -> None:
        with TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "kukicha.toml"
            for text in (
                "Bogus = 'value'\n",
                "youtube_download_path = 'youtube'\n",
                "linked_toast_timeout_ms = 25000\n",
                "LogLevel = 'INFO'\n",
                "OpenSubsonicUsername = 'guest'\n",
                "OpenSubsonicPassword = 'guest'\n",
                "OpenSubsonicHost = '127.0.0.1'\n",
                "OpenSubsonicPort = 4533\n",
            ):
                with self.subTest(text=text):
                    config_path.write_text(text, encoding="utf-8")

                    with self.assertRaisesRegex(PlayerConfigError, "unsupported config key"):
                        load_player_options(config_path)

    def test_player_config_help_text_shows_defaults_when_config_is_missing(self) -> None:
        with TemporaryDirectory() as tempdir:
            config_home = Path(tempdir)
            with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(config_home)}, clear=False):
                help_text = player_config_help_text()

            self.assertIn("status: missing (startup would fail)", help_text)
            self.assertIn(f"path: {(config_home / 'kukicha' / 'kukicha.toml').resolve()}", help_text)
            self.assertIn("error: config file does not exist", help_text)
            self.assertNotIn("Current values:", help_text)
            self.assertIn(
                "Supported keys:\n  log_level\n  database_path\n  roots\n  remote_roots\n  remote_workers\n  ffmpeg_path\n"
                "  youtube_download_root\n  prefer_musicbrainz_english_aliases\n  host\n  port\n"
                "  trusted_proxy_headers\n  appearance\n  accent_color\n  toast_timeout_ms\n"
                "  album_artist_split_patterns\n  auth.username\n  auth.password_hash_file\n"
                "  auth.cookie_max_age\n  auth.cookie_name\n"
                "  opensubsonic.mount_prefix\n  opensubsonic.secret_file",
                help_text,
            )
            self.assertNotIn("linked_toast_timeout_ms", help_text)
            self.assertIn(
                "appearance accepts these values:\n  light\n  dark\n  dim\n  system",
                help_text,
            )
            self.assertIn(
                "accent_color accepts these palette names or matching hex codes:\n  "
                + " ".join(ACCENT_COLOR_CODES),
                help_text,
            )
            self.assertLess(
                help_text.index("appearance accepts these values:"),
                help_text.index("accent_color accepts these palette names or matching hex codes:"),
            )
            self.assertNotIn(f"{DEFAULT_ACCENT_COLOR} ({ACCENT_COLOR_CODES[DEFAULT_ACCENT_COLOR]})", help_text)


class CliPlayerCommandTest(unittest.TestCase):
    def test_root_command_accepts_config_flag(self) -> None:
        parser = build_parser()

        args = parser.parse_args(["-c", "/tmp/kukicha.toml"])

        self.assertEqual(args.config, Path("/tmp/kukicha.toml"))

    def test_root_command_accepts_version_flag(self) -> None:
        with patch("kukicha.cli.kukicha_version", return_value="9.8.7"):
            parser = build_parser()

        for version_flag in ("-v", "--version"):
            with self.subTest(version_flag=version_flag):
                with (
                    patch("sys.stdout", new=io.StringIO()) as stdout,
                    self.assertRaises(SystemExit) as raised,
                ):
                    parser.parse_args([version_flag])

                self.assertEqual(raised.exception.code, 0)
                self.assertEqual(stdout.getvalue(), "kukicha 9.8.7\n")

    def test_player_subcommand_is_not_available(self) -> None:
        parser = build_parser()

        with (
            patch("sys.stderr", new=io.StringIO()),
            self.assertRaises(SystemExit),
        ):
            parser.parse_args(["player", "-c", "/tmp/kukicha.toml"])

    def test_opensubsonic_requires_management_subcommand(self) -> None:
        parser = build_parser()

        with (
            patch("sys.stderr", new=io.StringIO()),
            self.assertRaises(SystemExit),
        ):
            parser.parse_args(["opensubsonic"])

    def test_opensubsonic_init_subcommand_is_available(self) -> None:
        parser = build_parser()

        args = parser.parse_args(["opensubsonic", "init"])

        self.assertEqual(args.command, "opensubsonic")
        self.assertEqual(args.opensubsonic_command, "init")
        self.assertTrue(callable(args.func))

    def test_opensubsonic_password_subcommand_is_available(self) -> None:
        parser = build_parser()

        args = parser.parse_args(["opensubsonic", "password"])

        self.assertEqual(args.command, "opensubsonic")
        self.assertEqual(args.opensubsonic_command, "password")
        self.assertTrue(callable(args.func))

    def test_auth_password_subcommand_is_available(self) -> None:
        parser = build_parser()

        args = parser.parse_args(["auth", "password"])

        self.assertEqual(args.command, "auth")
        self.assertEqual(args.auth_command, "password")
        self.assertTrue(callable(args.func))

    def test_rescan_subcommand_is_not_available(self) -> None:
        parser = build_parser()

        with (
            patch("sys.stderr", new=io.StringIO()),
            self.assertRaises(SystemExit),
        ):
            parser.parse_args(["rescan"])

    def test_scan_subcommand_is_not_available(self) -> None:
        parser = build_parser()

        with (
            patch("sys.stderr", new=io.StringIO()),
            self.assertRaises(SystemExit),
        ):
            parser.parse_args(["scan"])

    def test_doctor_subcommand_is_not_available(self) -> None:
        parser = build_parser()

        with (
            patch("sys.stderr", new=io.StringIO()),
            self.assertRaises(SystemExit),
        ):
            parser.parse_args(["doctor", "id"])

    def test_root_help_uses_config_values_from_explicit_config_path(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            config_path = temp_path / "custom.toml"
            password_hash_file = temp_path / "password.hash"
            password_hash_file.write_text(f"{TEST_ARGON2ID_HASH}\n", encoding="utf-8")
            password_hash_file.chmod(0o600)
            config_path.write_text(
                "\n".join(
                    (
                        "log_level = 'info'",
                        "database_path = 'custom.sqlite'",
                        "host = '0.0.0.0'",
                        "port = 43210",
                        "[auth]",
                        "username = 'listener'",
                        "password_hash_file = 'password.hash'",
                    )
                ),
                encoding="utf-8",
            )
            parser = build_parser(["--config", str(config_path), "--help"])

            with (
                patch("sys.stdout", new=io.StringIO()) as stdout,
                self.assertRaises(SystemExit),
            ):
                parser.parse_args(["--config", str(config_path), "--help"])

            help_text = stdout.getvalue()
            self.assertIn(f"path: {config_path.resolve()}", help_text)
            self.assertIn("status: found", help_text)
            self.assertIn("log_level: INFO (configured)", help_text)
            self.assertIn(f"database_path: {(temp_path / 'custom.sqlite').resolve()} (configured)", help_text)
            self.assertIn("host: 0.0.0.0 (configured)", help_text)
            self.assertIn("port: 43210 (configured)", help_text)
            self.assertIn("ffmpeg_path: <unset> (default)", help_text)


class InitCommandTest(unittest.TestCase):
    def run_init(
        self,
        config_path: Path,
        *,
        stdin: str = "",
        username: str = "listener",
        password: str = "secret",
    ) -> int:
        args = build_parser().parse_args(["--config", str(config_path), "init"])
        env = {
            "KUKICHA_USERNAME": username,
            "KUKICHA_PASSWORD": password,
        }
        with (
            patch.dict(os.environ, env, clear=False),
            patch("sys.stdin", io.StringIO(stdin)),
            patch("sys.stdout", new=io.StringIO()),
            patch("sys.stderr", new=io.StringIO()),
        ):
            return args.func(args)

    def run_open_subsonic_init(
        self,
        config_path: Path,
        *,
        password: str = "sonic-secret",
        mount_prefix: str = "/",
    ) -> int:
        args = build_parser().parse_args(["--config", str(config_path), "opensubsonic", "init"])
        env = {
            "OPENSUBSONIC_PASSWORD": password,
            "OPENSUBSONIC_MOUNT": mount_prefix,
        }
        with (
            patch.dict(os.environ, env, clear=False),
            patch("sys.stdout", new=io.StringIO()),
            patch("sys.stderr", new=io.StringIO()),
        ):
            return args.func(args)

    def run_open_subsonic_password(
        self,
        config_path: Path,
        *,
        password: str = "new-sonic-secret",
    ) -> int:
        args = build_parser().parse_args(
            ["--config", str(config_path), "opensubsonic", "password"]
        )
        with (
            patch.dict(os.environ, {"OPENSUBSONIC_PASSWORD": password}, clear=False),
            patch("sys.stdout", new=io.StringIO()),
            patch("sys.stderr", new=io.StringIO()),
        ):
            return args.func(args)

    def run_auth_password(
        self,
        config_path: Path,
        *,
        password: str = "new-secret",
    ) -> int:
        args = build_parser().parse_args(["--config", str(config_path), "auth", "password"])
        with (
            patch.dict(os.environ, {"KUKICHA_PASSWORD": password}, clear=False),
            patch("sys.stdout", new=io.StringIO()),
            patch("sys.stderr", new=io.StringIO()),
        ):
            return args.func(args)

    def test_init_uses_env_credentials_and_stdin_config(self) -> None:
        with TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "kukicha.toml"
            exit_code = self.run_init(
                config_path,
                stdin="\n".join(
                    (
                        "host = '0.0.0.0'",
                        "port = 4533",
                        "roots = ['/music']",
                        "youtube_download_root = '/music'",
                        "appearance = 'dim'",
                        "",
                    )
                ),
            )

            self.assertEqual(exit_code, 0)
            config = tomllib.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(config["host"], "0.0.0.0")
            self.assertEqual(config["youtube_download_root"], "/music")
            self.assertEqual(config["auth"]["username"], "listener")
            self.assertEqual(
                config["auth"]["password_hash_file"],
                str((Path(tempdir) / "password.hash").resolve()),
            )
            password_hash_file = Path(tempdir) / "password.hash"
            self.assertEqual(password_hash_file.stat().st_mode & 0o777, 0o600)
            options = load_player_options(config_path)
            assert options.auth is not None
            self.assertTrue(verify_password(options.auth, "secret"))

    def test_init_prompts_interactively_without_env_credentials(self) -> None:
        with TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "kukicha.toml"
            args = build_parser().parse_args(["--config", str(config_path), "init"])
            with (
                patch.dict(os.environ, {}, clear=True),
                patch("sys.stdin", TtyStringIO()),
                patch("builtins.input", return_value="listener"),
                patch("getpass.getpass", side_effect=["secret", "secret"]),
                patch("sys.stdout", new=io.StringIO()),
                patch("sys.stderr", new=io.StringIO()),
            ):
                exit_code = args.func(args)

            self.assertEqual(exit_code, 0)
            options = load_player_options(config_path)
            assert options.auth is not None
            self.assertEqual(options.auth.username, "listener")
            self.assertTrue(verify_password(options.auth, "secret"))

    def test_init_rejects_stdin_auth_section(self) -> None:
        with TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "kukicha.toml"
            exit_code = self.run_init(config_path, stdin="[auth]\nusername = 'bad'\n")

            self.assertEqual(exit_code, 1)
            self.assertFalse(config_path.exists())

    def test_init_rejects_old_youtube_download_path_config(self) -> None:
        with TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "kukicha.toml"
            exit_code = self.run_init(
                config_path,
                stdin="youtube_download_path = 'youtube'\n",
            )

            self.assertEqual(exit_code, 1)
            self.assertFalse(config_path.exists())

    def test_init_adds_auth_to_existing_config_and_rejects_existing_auth(self) -> None:
        with TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "kukicha.toml"
            config_path.write_text("log_level = 'INFO'\n", encoding="utf-8")

            first_exit_code = self.run_init(config_path)
            second_exit_code = self.run_init(config_path)

            self.assertEqual(first_exit_code, 0)
            self.assertEqual(second_exit_code, 1)
            config = tomllib.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(config["log_level"], "INFO")
            self.assertEqual(config["auth"]["username"], "listener")

    def test_init_rejects_existing_config_with_stdin_config(self) -> None:
        with TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "kukicha.toml"
            config_path.write_text("log_level = 'INFO'\n", encoding="utf-8")

            exit_code = self.run_init(config_path, stdin="host = '0.0.0.0'\n")

            self.assertEqual(exit_code, 1)
            self.assertNotIn("[auth]", config_path.read_text(encoding="utf-8"))

    def test_opensubsonic_init_uses_env_and_rejects_existing_section(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            config_path = temp_path / "kukicha.toml"
            self.run_init(config_path)

            first_exit_code = self.run_open_subsonic_init(
                config_path,
                password="sonic-secret",
                mount_prefix="/sonic/",
            )
            second_exit_code = self.run_open_subsonic_init(config_path)

            self.assertEqual(first_exit_code, 0)
            self.assertEqual(second_exit_code, 1)
            config = tomllib.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(config["opensubsonic"]["mount_prefix"], "/sonic")
            self.assertEqual(
                config["opensubsonic"]["secret_file"],
                str((temp_path / DEFAULT_OPEN_SUBSONIC_SECRET_FILENAME).resolve()),
            )
            secret_file = temp_path / DEFAULT_OPEN_SUBSONIC_SECRET_FILENAME
            self.assertEqual(secret_file.read_text(encoding="utf-8"), "sonic-secret\n")
            self.assertEqual(secret_file.stat().st_mode & 0o777, 0o600)
            options = load_player_options(config_path)
            self.assertIsNotNone(options.opensubsonic)
            assert options.opensubsonic is not None
            self.assertEqual(options.opensubsonic.mount_prefix, "/sonic")
            self.assertEqual(options.opensubsonic.secret_file, secret_file.resolve())

    def test_opensubsonic_init_prompts_interactively_without_env(self) -> None:
        with TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "kukicha.toml"
            self.run_init(config_path)
            args = build_parser().parse_args(
                ["--config", str(config_path), "opensubsonic", "init"]
            )
            with (
                patch.dict(os.environ, {}, clear=True),
                patch("builtins.input", return_value="/sonic"),
                patch("getpass.getpass", side_effect=["sonic-secret", "sonic-secret"]),
                patch("sys.stdout", new=io.StringIO()),
                patch("sys.stderr", new=io.StringIO()),
            ):
                exit_code = args.func(args)

            self.assertEqual(exit_code, 0)
            options = load_player_options(config_path)
            self.assertIsNotNone(options.opensubsonic)
            assert options.opensubsonic is not None
            self.assertEqual(options.opensubsonic.mount_prefix, "/sonic")
            self.assertEqual(
                options.opensubsonic.secret_file.read_text(encoding="utf-8"),
                "sonic-secret\n",
            )

    def test_opensubsonic_init_rejects_config_without_auth(self) -> None:
        with TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "kukicha.toml"
            config_path.write_text("log_level = 'INFO'\n", encoding="utf-8")

            exit_code = self.run_open_subsonic_init(config_path)

            self.assertEqual(exit_code, 1)
            self.assertNotIn("[opensubsonic]", config_path.read_text(encoding="utf-8"))

    def test_opensubsonic_password_updates_secret_file_and_preserves_config(self) -> None:
        with TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "kukicha.toml"
            self.run_init(config_path)
            self.run_open_subsonic_init(config_path, password="old-secret")
            before_config = config_path.read_text(encoding="utf-8")
            options = load_player_options(config_path)
            assert options.opensubsonic is not None

            exit_code = self.run_open_subsonic_password(
                config_path,
                password="new-secret",
            )

            self.assertEqual(exit_code, 0)
            self.assertEqual(config_path.read_text(encoding="utf-8"), before_config)
            self.assertEqual(
                options.opensubsonic.secret_file.read_text(encoding="utf-8"),
                "new-secret\n",
            )
            self.assertEqual(options.opensubsonic.secret_file.stat().st_mode & 0o777, 0o600)

    def test_password_commands_create_missing_files_from_declared_config(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            config_path = temp_path / "kukicha.toml"
            config_path.write_text(
                "\n".join(
                    (
                        "log_level = 'INFO'",
                        "",
                        "[auth]",
                        "username = 'listener'",
                        "password_hash_file = 'secrets/password.hash'",
                        "cookie_max_age = '180d'",
                        "cookie_name = 'kukicha_cookie'",
                        "",
                        "[opensubsonic]",
                        "mount_prefix = '/'",
                        "secret_file = 'secrets/opensubsonic.secret'",
                        "",
                    )
                ),
                encoding="utf-8",
            )
            before_config = config_path.read_text(encoding="utf-8")
            password_hash_file = temp_path / "secrets" / "password.hash"
            secret_file = temp_path / "secrets" / "opensubsonic.secret"

            auth_exit_code = self.run_auth_password(config_path, password="browser-secret")
            open_subsonic_exit_code = self.run_open_subsonic_password(
                config_path,
                password="sonic-secret",
            )

            self.assertEqual(auth_exit_code, 0)
            self.assertEqual(open_subsonic_exit_code, 0)
            self.assertEqual(config_path.read_text(encoding="utf-8"), before_config)
            self.assertEqual(password_hash_file.stat().st_mode & 0o777, 0o600)
            self.assertEqual(secret_file.stat().st_mode & 0o777, 0o600)
            self.assertEqual(secret_file.read_text(encoding="utf-8"), "sonic-secret\n")
            options = load_player_options(config_path)
            assert options.auth is not None
            self.assertTrue(verify_password(options.auth, "browser-secret"))

    def test_opensubsonic_password_rejects_config_without_opensubsonic(self) -> None:
        with TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "kukicha.toml"
            self.run_init(config_path)

            exit_code = self.run_open_subsonic_password(config_path)

            self.assertEqual(exit_code, 1)

    def test_auth_password_updates_hash_file_from_env_and_preserves_config(self) -> None:
        with TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "kukicha.toml"
            self.run_init(config_path, password="old-secret")
            before_config = config_path.read_text(encoding="utf-8")
            options = load_player_options(config_path)
            assert options.auth is not None
            before_hash = options.auth.password_hash_file.read_text(encoding="utf-8")
            args = build_parser().parse_args(["--config", str(config_path), "auth", "password"])

            with (
                patch.dict(os.environ, {"KUKICHA_PASSWORD": "new-secret"}, clear=False),
                patch("sys.stdout", new=io.StringIO()) as stdout,
                patch("sys.stderr", new=io.StringIO()),
            ):
                exit_code = args.func(args)

            after_hash = options.auth.password_hash_file.read_text(encoding="utf-8")
            self.assertEqual(exit_code, 0)
            self.assertEqual(config_path.read_text(encoding="utf-8"), before_config)
            self.assertNotEqual(after_hash, before_hash)
            self.assertEqual(options.auth.password_hash_file.stat().st_mode & 0o777, 0o600)
            self.assertIn("invalidated", stdout.getvalue())
            self.assertFalse(verify_password(options.auth, "old-secret"))
            self.assertTrue(verify_password(options.auth, "new-secret"))

    def test_auth_password_prompts_interactively_without_env_password(self) -> None:
        with TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "kukicha.toml"
            self.run_init(config_path, password="old-secret")
            options = load_player_options(config_path)
            assert options.auth is not None
            args = build_parser().parse_args(["--config", str(config_path), "auth", "password"])

            with (
                patch.dict(os.environ, {}, clear=True),
                patch("getpass.getpass", side_effect=["new-secret", "new-secret"]),
                patch("sys.stdout", new=io.StringIO()),
                patch("sys.stderr", new=io.StringIO()),
            ):
                exit_code = args.func(args)

            self.assertEqual(exit_code, 0)
            self.assertTrue(verify_password(options.auth, "new-secret"))

    def test_auth_password_rejects_config_without_auth(self) -> None:
        with TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "kukicha.toml"
            config_path.write_text("log_level = 'INFO'\n", encoding="utf-8")
            args = build_parser().parse_args(["--config", str(config_path), "auth", "password"])

            with (
                patch.dict(os.environ, {"KUKICHA_PASSWORD": "new-secret"}, clear=False),
                patch("sys.stdout", new=io.StringIO()),
                patch("sys.stderr", new=io.StringIO()) as stderr,
            ):
                exit_code = args.func(args)

            self.assertEqual(exit_code, 1)
            self.assertIn("[auth] section is required", stderr.getvalue())


class PlayerStartupTest(unittest.TestCase):
    def test_validate_player_startup_creates_missing_database(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            options = PlayerServerOptions(
                config_path=temp_path / "kukicha.toml",
                database=database,
                ffmpeg_path=None,
            )

            validate_player_startup(options)

            self.assertTrue(database.exists())
            connection = connect_database(database, create=False)
            try:
                self.assertIsNotNone(
                    connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'library_roots'"
                    ).fetchone()
                )
            finally:
                connection.close()

    def test_validate_player_startup_rejects_nested_roots_before_database_setup(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            options = PlayerServerOptions(
                config_path=temp_path / "kukicha.toml",
                database=database,
                ffmpeg_path=None,
                roots=(temp_path / "music", temp_path / "music" / "live"),
            )

            with self.assertRaisesRegex(
                PlayerConfigError,
                "roots must not contain nested paths",
            ):
                validate_player_startup(options)

            self.assertFalse(database.exists())


class PlayerPageMenuTest(unittest.TestCase):
    def test_player_page_menu_items_include_all_pages_and_mark_current(self) -> None:
        items = player_page_menu_items("jobs")

        self.assertEqual(
            [(item.kind, item.title, item.url) for item in items],
            [
                ("heading", "LIBRARY", ""),
                ("link", "Home", "/"),
                ("link", "Albums", "/albums"),
                ("link", "Artists", "/artists"),
                ("link", "Playlists", "/playlists"),
                ("divider", "", ""),
                ("heading", "SETTINGS", ""),
                ("link", "Roots", "/roots"),
                ("link", "Artists Split Rules", "/artist-split-rules"),
                ("link", "Metadata Overrides", "/metadata-overrides"),
                ("link", "Listening Data", "/listening-data"),
                ("link", "Cache", "/cache"),
                ("divider", "", ""),
                ("link", "Jobs", "/jobs"),
                ("action", "Keyboard Shortcuts", ""),
                ("link", "Help", "/help"),
            ],
        )
        self.assertEqual(
            [item.title for item in items if item.current],
            ["Jobs"],
        )
        self.assertNotIn("Search", [item.title for item in items])

    def test_player_page_menu_template_groups_library_and_settings_links(self) -> None:
        template = build_template_environment().get_template("player/_page_title.html")

        html = template.render(
            page_heading="Roots",
            page_menu_items=player_page_menu_items("roots"),
            count_text="",
        )

        self.assertIn('class="page-menu-heading">LIBRARY</div>', html)
        self.assertIn('class="page-menu-heading">SETTINGS</div>', html)
        self.assertEqual(html.count('class="page-menu-divider"'), 2)
        self.assertIn('class="page-menu-divider"', html)
        self.assertLess(html.index("LIBRARY"), html.index("Albums"))
        self.assertLess(html.index("Artists"), html.index('class="page-menu-divider"'))
        self.assertLess(html.index("Artists"), html.index("Playlists"))
        self.assertLess(html.index("Playlists"), html.index('class="page-menu-divider"'))
        self.assertLess(html.index("SETTINGS"), html.index("Roots"))
        self.assertLess(html.index("Roots"), html.index("Artists Split Rules"))
        self.assertLess(html.index("Artists Split Rules"), html.index("Metadata Overrides"))
        self.assertLess(html.index("Metadata Overrides"), html.index("Listening Data"))
        self.assertLess(html.index("Listening Data"), html.index("Cache"))
        self.assertIn("data-open-keyboard-shortcuts", html)
        self.assertNotIn('href="/search"', html)
        self.assertLess(html.index("Jobs"), html.index("Keyboard Shortcuts"))
        self.assertLess(html.index("Keyboard Shortcuts"), html.index("Help"))

    def test_player_page_heading_rejects_unknown_page(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown player page"):
            player_page_heading("missing")


class PlayerCompactCountTest(unittest.TestCase):
    def test_format_compact_count_uses_three_significant_digits_and_suffixes(self) -> None:
        examples = {
            999: "999",
            1_000: "1k",
            1_200: "1.2k",
            12_300: "12.3k",
            123_000: "123k",
            1_200_000: "1.2M",
            12_300_000: "12.3M",
            12_380_000: "12.4M",
            123_000_000: "123M",
            999_500: "1M",
            999_000_000_000_000: "999T",
            999_000_000_000_001: "infinity",
        }

        self.assertEqual(
            {value: format_compact_count(value) for value in examples},
            examples,
        )

    def test_format_count_label_keeps_raw_count_for_pluralization(self) -> None:
        self.assertEqual(format_count_label(1_200, "play", "plays"), "1.2k plays")
        self.assertEqual(format_count_label(1, "play", "plays"), "1 play")


class PlayerArtistCloudLinksTest(unittest.TestCase):
    def test_artist_cloud_links_skip_blank_artists_and_link_to_album_filter(self) -> None:
        links = artist_cloud_links(
            (
                LibraryAlbumArtistStats(
                    album_artist="",
                    tracks_scanned=10,
                    albums_scanned=2,
                ),
                LibraryAlbumArtistStats(
                    album_artist="Ahmad Jamal",
                    tracks_scanned=8,
                    albums_scanned=1,
                ),
            )
        )

        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].label, "Ahmad Jamal")
        self.assertEqual(links[0].url, "/albums?artist=Ahmad+Jamal")
        self.assertEqual(links[0].title, "1 album - 8 tracks")

    def test_artist_cloud_size_weights_albums_more_than_tracks(self) -> None:
        links = artist_cloud_links(
            (
                LibraryAlbumArtistStats(
                    album_artist="Track Heavy",
                    tracks_scanned=40,
                    albums_scanned=1,
                ),
                LibraryAlbumArtistStats(
                    album_artist="Album Heavy",
                    tracks_scanned=0,
                    albums_scanned=5,
                ),
            )
        )
        sizes = {link.label: link.font_size_rem for link in links}

        self.assertGreater(sizes["Album Heavy"], sizes["Track Heavy"])

    def test_artist_cloud_uses_midpoint_size_when_scores_match(self) -> None:
        links = artist_cloud_links(
            (
                LibraryAlbumArtistStats(
                    album_artist="Alice",
                    tracks_scanned=8,
                    albums_scanned=2,
                ),
                LibraryAlbumArtistStats(
                    album_artist="Bob",
                    tracks_scanned=8,
                    albums_scanned=2,
                ),
            )
        )

        self.assertEqual([link.font_size_rem for link in links], [1.58, 1.58])


class PlayerGenreFilterQueryParamsTest(unittest.TestCase):
    def test_parses_grouped_genre_filter_params(self) -> None:
        query = album_list_query_from_params(
            parse_qs(
                "genre[0][p]=Electronic&genre[0][c][]=Ambient"
                "&genre[0][c][]=Techno&genre[1][p]=Jazz"
            )
        )

        self.assertEqual(
            query.genre_filters,
            (
                GenreStyleFilter(genre="Electronic", styles=("Ambient", "Techno")),
                GenreStyleFilter(genre="Jazz"),
            ),
        )

    def test_ignores_flat_genre_and_style_params(self) -> None:
        query = album_list_query_from_params(
            parse_qs("genre=Electronic&style=Ambient")
        )

        self.assertEqual(query.genre_filters, ())
        self.assertEqual(query.genres, ())
        self.assertEqual(query.styles, ())

    def test_ignores_removed_root_and_property_filter_params(self) -> None:
        query = album_list_query_from_params(
            parse_qs("root=1&has_cover=1&compilation=0&work=1")
        )

        self.assertEqual(query.root_positions, ())
        self.assertEqual(album_index_url(AlbumListQuery(root_positions=(1,))), "/albums")

    def test_album_index_url_uses_grouped_genre_filter_params(self) -> None:
        url = album_index_url(
            AlbumListQuery(
                genre_filters=(
                    GenreStyleFilter(genre="Electronic", styles=("Ambient", "Techno")),
                    GenreStyleFilter(genre="Jazz", styles=("Bebop",)),
                )
            )
        )

        self.assertEqual(
            url,
            "/albums?genre[0][p]=Electronic&genre[0][c][]=Ambient"
            "&genre[0][c][]=Techno&genre[1][p]=Jazz&genre[1][c][]=Bebop",
        )

    def test_album_index_url_allows_parent_only_genre_filter_params(self) -> None:
        url = album_index_url(
            AlbumListQuery(
                genre_filters=(
                    GenreStyleFilter(genre="Electronic"),
                )
            )
        )

        self.assertEqual(url, "/albums?genre[0][p]=Electronic")

    def test_album_bulk_metadata_edit_url_keeps_filters_and_drops_paging(self) -> None:
        url = album_bulk_metadata_edit_url(
            AlbumListQuery(
                artists=("Brian Eno",),
                search="ambient",
                genre_filters=(GenreStyleFilter(genre="Electronic"),),
                sort=ALBUM_LIST_SORT_RECENT,
                size=50,
                offset=200,
            )
        )

        self.assertEqual(
            url,
            "/albums/metadata-urls/edit?artist=Brian+Eno&search=ambient"
            "&genre[0][p]=Electronic&sort=recent",
        )

    def test_album_bulk_star_action_url_keeps_filters_and_drops_paging(self) -> None:
        url = album_bulk_star_action_url(
            AlbumListQuery(
                artists=("Brian Eno",),
                search="ambient",
                genre_filters=(GenreStyleFilter(genre="Electronic"),),
                sort=ALBUM_LIST_SORT_RECENT,
                size=50,
                offset=200,
            )
        )

        self.assertEqual(
            url,
            "/api/albums/star?artist=Brian+Eno&search=ambient"
            "&genre[0][p]=Electronic&sort=recent",
        )

    def test_recommendation_urls_encode_seed_values_and_modes(self) -> None:
        album = AlbumDetails(
            album_id="brian-eno::ambient 1",
            artist="Brian Eno",
            album_artists=("Brian Eno",),
            album="Ambient 1",
            year=1978,
            track_count=4,
        )

        self.assertEqual(
            recommendation_track_radio_url(
                make_track_view(7, root_position=0, path="/music/07.flac"),
                mode="discovery",
                limit=10,
            ),
            "/recommendations/radio/track/7?mode=discovery&limit=10",
        )
        self.assertEqual(
            recommendation_album_radio_url(album, limit=25),
            "/recommendations/radio/album/brian-eno::ambient%201?limit=25",
        )
        self.assertEqual(
            recommendation_album_radio_url(replace(album, is_playlist=True)),
            "",
        )
        self.assertEqual(
            recommendation_artist_radio_url("Seed Artist/Guest"),
            "/recommendations/radio/artist/Seed%20Artist%2FGuest",
        )
        self.assertEqual(
            recommendation_daily_url(limit=30, date="2026-06-07"),
            "/recommendations/daily?limit=30&date=2026-06-07",
        )

    def test_parses_sort_param_and_defaults_to_artist(self) -> None:
        default_query = album_list_query_from_params(parse_qs(""))
        artist_query = album_list_query_from_params(parse_qs("sort=artist"))
        albums_query = album_list_query_from_params(parse_qs("sort=albums"))
        genre_query = album_list_query_from_params(parse_qs("sort=genre"))
        recent_query = album_list_query_from_params(parse_qs("sort=recent"))
        frequent_query = album_list_query_from_params(parse_qs("sort=frequent"))
        starred_query = album_list_query_from_params(parse_qs("sort=starred"))
        invalid_query = album_list_query_from_params(parse_qs("sort=unknown"))

        self.assertEqual(default_query.sort, ALBUM_LIST_SORT_ARTIST)
        self.assertEqual(artist_query.sort, ALBUM_LIST_SORT_ARTIST)
        self.assertEqual(albums_query.sort, ALBUM_LIST_SORT_ALBUMS)
        self.assertEqual(genre_query.sort, ALBUM_LIST_SORT_GENRE)
        self.assertEqual(recent_query.sort, ALBUM_LIST_SORT_RECENT)
        self.assertEqual(frequent_query.sort, ALBUM_LIST_SORT_FREQUENT)
        self.assertEqual(starred_query.sort, ALBUM_LIST_SORT_STARRED)
        self.assertEqual(invalid_query.sort, ALBUM_LIST_SORT_ARTIST)

    def test_album_index_url_includes_only_non_default_sort_param(self) -> None:
        self.assertEqual(
            album_index_url(AlbumListQuery(sort=ALBUM_LIST_SORT_RECENTLY_ADDED)),
            "/albums?sort=recently_added",
        )
        self.assertEqual(
            album_index_url(AlbumListQuery(sort=ALBUM_LIST_SORT_ARTIST)),
            "/albums",
        )
        self.assertEqual(
            album_index_url(AlbumListQuery(sort=ALBUM_LIST_SORT_ALBUMS)),
            "/albums?sort=albums",
        )
        self.assertEqual(
            album_index_url(AlbumListQuery(sort=ALBUM_LIST_SORT_RECENT)),
            "/albums?sort=recent",
        )
        self.assertEqual(
            album_index_url(AlbumListQuery(sort=ALBUM_LIST_SORT_FREQUENT)),
            "/albums?sort=frequent",
        )
        self.assertEqual(
            album_index_url(AlbumListQuery(sort=ALBUM_LIST_SORT_GENRE)),
            "/albums?sort=genre",
        )
        self.assertEqual(
            album_index_url(AlbumListQuery(sort=ALBUM_LIST_SORT_STARRED)),
            "/albums?sort=starred",
        )

    def test_album_query_params_do_not_include_playlist_filter(self) -> None:
        query = album_list_query_from_params(parse_qs("playlist=1&search=Road"))

        self.assertIsNone(query.is_playlist)
        self.assertEqual(album_index_url(AlbumListQuery(is_playlist=True, search="Road")), "/albums?search=Road")

    def test_album_size_offset_query_params_round_trip_and_playlist_urls_ignore_album_query_state(self) -> None:
        query = album_list_query_from_params(parse_qs("size=80&offset=160"))

        self.assertEqual(query.size, 80)
        self.assertEqual(query.offset, 160)
        self.assertEqual(
            album_index_url(AlbumListQuery(size=80, offset=160)),
            "/albums?size=80&offset=160",
        )
        self.assertEqual(
            album_index_url(
                AlbumListQuery(size=80, offset=160),
                offset=240,
            ),
            "/albums?size=80&offset=240",
        )
        self.assertEqual(
            playlist_index_url(
                AlbumListQuery(search="Road", size=80, offset=160),
                offset=240,
            ),
            "/playlists",
        )

    def test_search_url_preserves_unmodified_offsets(self) -> None:
        query = LibrarySearchQuery(
            query="",
            artist_count=20,
            artist_offset=0,
            album_count=20,
            album_offset=20,
            song_count=20,
            song_offset=0,
        )

        self.assertEqual(
            search_url(query, album_offset=40),
            "/search?query=&artistCount=20&artistOffset=0"
            "&albumCount=20&albumOffset=40&songCount=20&songOffset=0",
        )
        self.assertEqual(
            search_url(query, song_offset=20),
            "/search?query=&artistCount=20&artistOffset=0"
            "&albumCount=20&albumOffset=20&songCount=20&songOffset=20",
        )
        self.assertNotIn("object", search_url(query, artist_offset=20))
        self.assertNotIn("object", search_url(query, album_offset=40))
        self.assertNotIn("object", search_url(query, song_offset=20))


class PlayerAlbumDetailLinksTest(unittest.TestCase):
    def test_album_meta_query_updates_requested_filters_and_preserves_current_params(self) -> None:
        query = AlbumListQuery(
            artists=("Current Artist",),
            album="Selected Ambient Works Volume II",
            root_positions=(0, 2),
            genre_filters=(GenreStyleFilter(genre="Ambient", styles=("IDM",)),),
            size=80,
            offset=240,
            search="aphex",
            sort=ALBUM_LIST_SORT_ARTIST,
        )

        linked = album_meta_query(
            query,
            artists=("Aphex Twin",),
            genre_filters=(GenreStyleFilter(genre="Electronic", styles=("Techno",)),),
        )

        self.assertEqual(linked.artists, ("Aphex Twin",))
        self.assertEqual(
            linked.genre_filters,
            (GenreStyleFilter(genre="Electronic", styles=("Techno",)),),
        )
        self.assertEqual(linked.root_positions, ())
        self.assertEqual(linked.offset, 0)
        self.assertEqual(linked.size, 80)
        self.assertEqual(linked.sort, ALBUM_LIST_SORT_ARTIST)
        self.assertEqual(linked.album, "Selected Ambient Works Volume II")
        self.assertEqual(linked.search, "aphex")

    def test_album_detail_links_build_filtered_library_urls(self) -> None:
        album = AlbumDetails(
            album_id="aphex-twin::selected-ambient-works-volume-ii",
            artist="Aphex Twin",
            album_artists=("Aphex Twin",),
            album="Selected Ambient Works Volume II",
            year=1994,
            track_count=2,
            genres=("Electronic", "Jazz", "Field Recording"),
            styles=("IDM", "Bebop"),
            tracks=(
                PlaylistTrack(path="/music/b/Aphex Twin/SAW II/02.flac", root_position=2),
                PlaylistTrack(path="/music/a/Aphex Twin/SAW II/01.flac", root_position=1),
                PlaylistTrack(path="/music/a/Aphex Twin/SAW II/03.flac", root_position=1),
            ),
        )
        query = AlbumListQuery(
            root_positions=(1,),
            size=80,
            offset=160,
            search="saw",
        )
        filters = LibraryFilterOptions(
            genre_groups=(
                GenreFilterGroup(genre="Electronic", styles=("IDM", "Ambient")),
                GenreFilterGroup(genre="Jazz", styles=("Bebop",)),
                GenreFilterGroup(genre="Field Recording"),
            )
        )
        artist_links = album_artist_links(album, query)
        genre_links = album_genre_links(album, query, filters)
        style_links = album_style_links(album, query, filters)

        self.assertEqual(
            [(item.label, item.url) for item in artist_links],
            [("Aphex Twin", "/albums?artist=Aphex+Twin&search=saw&size=80")],
        )
        self.assertEqual(
            [(item.label, item.url) for item in genre_links],
            [
                (
                    "Electronic",
                    "/albums?search=saw&genre[0][p]=Electronic&size=80",
                ),
                (
                    "Jazz",
                    "/albums?search=saw&genre[0][p]=Jazz&size=80",
                ),
                (
                    "Field Recording",
                    "/albums?search=saw&genre[0][p]=Field+Recording&size=80",
                ),
            ],
        )
        self.assertEqual(
            [(item.label, item.url) for item in style_links],
            [
                (
                    "IDM",
                    "/albums?search=saw&genre[0][p]=Electronic&genre[0][c][]=IDM&size=80",
                ),
                (
                    "Bebop",
                    "/albums?search=saw&genre[0][p]=Jazz&genre[0][c][]=Bebop&size=80",
                ),
            ],
        )

    def test_album_artist_url_builds_filtered_library_url_for_album_cards(self) -> None:
        album = AlbumDetails(
            album_id="brian-eno-robert-fripp::no-pussyfooting",
            artist="Brian Eno, Robert Fripp",
            album_artists=("Brian Eno", "Robert Fripp"),
            album="No Pussyfooting",
            year=1973,
            track_count=2,
        )
        query = AlbumListQuery(
            artists=("Current Artist",),
            genre_filters=(GenreStyleFilter(genre="Ambient"),),
            size=80,
            offset=160,
            search="ignored",
            sort=ALBUM_LIST_SORT_ARTIST,
        )

        self.assertEqual(
            album_artist_url(album, query),
            "/albums?artist=Brian+Eno&artist=Robert+Fripp&search=ignored"
            "&genre[0][p]=Ambient&size=80",
        )
        self.assertEqual(album_artist_url(replace(album, is_playlist=True), query), "")
        self.assertEqual(album_artist_url(replace(album, album_artists=()), query), "")


    def test_album_index_template_renders_individual_album_artist_links(self) -> None:
        album = AlbumDetails(
            album_id="brian-eno-robert-fripp::no-pussyfooting",
            artist="Brian Eno, Robert Fripp",
            album_artists=("Brian Eno", "Robert Fripp"),
            album="No Pussyfooting",
            year=1973,
            track_count=2,
        )
        query = AlbumListQuery(
            artists=("Current Artist",),
            genre_filters=(GenreStyleFilter(genre="Ambient"),),
            size=80,
            offset=160,
            search="ignored",
            sort=ALBUM_LIST_SORT_ARTIST,
        )
        template = build_template_environment().get_template("player/index.html")

        html = template.render(
            page_key="library",
            albums=(album,),
            query=query,
            show_filter_form=False,
            show_pagination_controls=False,
            empty_message="No albums matched these filters.",
            pagination_label="Album pages",
        )

        self.assertIn(
            'href="/albums?artist=Brian+Eno&amp;search=ignored&amp;genre[0][p]=Ambient&amp;size=80" data-nav>Brian Eno</a>,',
            html,
        )
        self.assertIn(
            'href="/albums?artist=Robert+Fripp&amp;search=ignored&amp;genre[0][p]=Ambient&amp;size=80" data-nav>Robert Fripp</a>',
            html,
        )
        self.assertNotIn("artist=Brian+Eno&amp;artist=Robert+Fripp", html)

    def test_album_index_template_renders_bulk_metadata_action(self) -> None:
        template = build_template_environment().get_template("player/index.html")

        html = template.render(
            page_key="library",
            albums=(),
            query=AlbumListQuery(search="ambient"),
            show_filter_form=True,
            show_filter_controls=False,
            show_sort_controls=False,
            show_pagination_controls=False,
            empty_message="No albums matched these filters.",
            pagination_label="Album pages",
            search_placeholder="Search albums and artists",
            clear_url="/albums",
            filter_action_url="/albums",
            default_size=200,
            sort_options=((ALBUM_LIST_SORT_ARTIST, "Artist"),),
            bulk_metadata_edit_page_url="/albums/metadata-urls/edit?search=ambient",
            bulk_album_star_action_url="/api/albums/star?search=ambient",
        )

        self.assertIn("vertical-ellipsis-icon", html)
        self.assertIn('href="/albums/metadata-urls/edit?search=ambient"', html)
        self.assertIn("data-bulk-metadata-edit-link data-nav>Edit Metadata URLs</a>", html)
        self.assertIn('data-bulk-album-star data-starred="true"', html)
        self.assertIn('data-bulk-album-star data-starred="false"', html)
        self.assertIn('data-action-url="/api/albums/star?search=ambient"', html)
        self.assertIn(">Star all filtered</button>", html)
        self.assertIn(">Unstar all filtered</button>", html)

    def test_album_index_template_renders_artist_radio_bulk_action(self) -> None:
        template = build_template_environment().get_template("player/index.html")

        html = template.render(
            page_key="library",
            albums=(),
            query=AlbumListQuery(artists=("Seed Artist",)),
            show_filter_form=True,
            show_filter_controls=False,
            show_sort_controls=False,
            show_pagination_controls=False,
            empty_message="No albums matched these filters.",
            pagination_label="Album pages",
            search_placeholder="Search albums and artists",
            clear_url="/albums",
            filter_action_url="/albums",
            default_size=200,
            sort_options=((ALBUM_LIST_SORT_ARTIST, "Artist"),),
            artist_radio_url="/recommendations/radio/artist/Seed%20Artist",
            bulk_metadata_edit_page_url="",
            bulk_album_star_action_url="",
        )

        self.assertIn('class="filter-menu bulk-actions-menu"', html)
        self.assertIn(
            'href="/recommendations/radio/artist/Seed%20Artist" data-nav>Artist Radio</a>',
            html,
        )

    def test_home_template_renders_daily_recommendations_entry_point(self) -> None:
        template = build_template_environment().get_template("player/home.html")

        html = template.render(
            page_key="home",
            dashboard=SimpleNamespace(
                recent_albums=(),
                recently_added_albums=(),
                recently_added_since="",
                recently_starred_albums=(),
                recent_artists=(),
                recent_tracks=(),
                recent_playlists=(),
            ),
            continue_listening=None,
            show_history_empty=False,
            recently_added_heading="Added in the Last Month",
            played_label=lambda value: value,
            added_label=lambda value: value,
            favorited_label=lambda value: value,
        )

        self.assertIn(
            'href="/recommendations/daily" data-nav>Daily Recommendations</a>',
            html,
        )

    def test_album_template_renders_individual_album_artist_labels_with_commas(self) -> None:
        album = AlbumDetails(
            album_id="brian-eno-robert-fripp::no-pussyfooting",
            artist="Brian Eno, Robert Fripp",
            album_artists=("Brian Eno", "Robert Fripp"),
            album="No Pussyfooting",
            year=1973,
            track_count=0,
        )
        template = build_template_environment().get_template("player/album.html")

        html = template.render(
            album=album,
            album_back_url="/",
            album_edit_page_url="/albums/brian-eno-robert-fripp::no-pussyfooting/edit",
            album_root_links=(),
            album_artist_links=album_artist_links(album, AlbumListQuery()),
            album_genre_links=(),
            album_year_text="",
            album_style_links=(),
            track_sections=(),
        )

        self.assertIn('href="/albums?artist=Brian+Eno" data-nav>Brian Eno</a>,', html)
        self.assertIn(
            'href="/albums?artist=Robert+Fripp" data-nav>Robert Fripp</a>',
            html,
        )

    def test_album_template_renders_year_adjacent_to_album_title(self) -> None:
        album = AlbumDetails(
            album_id="brian-eno::ambient-1",
            artist="Brian Eno",
            album_artists=("Brian Eno",),
            album="Ambient 1",
            year=1978,
            track_count=0,
        )
        template = build_template_environment().get_template("player/album.html")

        html = template.render(
            album=album,
            album_back_url="/",
            album_edit_page_url="/albums/brian-eno::ambient-1/edit",
            album_root_links=(),
            album_artist_links=album_artist_links(album, AlbumListQuery()),
            album_genre_links=({"label": "Ambient", "url": "/?genre=Ambient"},),
            album_year_text="1978",
            album_style_links=(),
            track_sections=(),
        )

        title_year = '<span class="album-title-year-meta">1978</span>'
        title_text = '<span class="album-detail-title-text" title="Ambient 1">Ambient 1</span>'
        self.assertIn(f"{title_text}{title_year}", html)
        self.assertIn('<ul class="meta-list album-artist-meta">', html)
        self.assertNotIn('<li class="album-year-meta">1978</li>', html)
        self.assertLess(
            html.index("Ambient 1"),
            html.index(title_year),
        )
        self.assertLess(
            html.index(title_year),
            html.index("album-artist-meta"),
        )
        self.assertLess(
            html.index("album-artist-meta"),
            html.index("album-genre-meta"),
        )

    def test_album_template_renders_genres_after_artist_and_styles_below(self) -> None:
        album = AlbumDetails(
            album_id="autechre::tri-repetae",
            artist="Autechre",
            album_artists=("Autechre",),
            album="Tri Repetae",
            year=1995,
            track_count=0,
        )
        template = build_template_environment().get_template("player/album.html")

        html = template.render(
            album=album,
            album_back_url="/",
            album_edit_page_url="/albums/autechre::tri-repetae/edit",
            album_root_links=(),
            album_artist_links=album_artist_links(album, AlbumListQuery()),
            album_genre_links=(
                {"label": "Electronic", "url": "/?genre=Electronic"},
                {"label": "Experimental", "url": "/?genre=Experimental"},
            ),
            album_year_text="",
            album_style_links=(
                {"label": "IDM", "url": "/?style=IDM"},
                {"label": "Ambient Techno", "url": "/?style=Ambient+Techno"},
            ),
            track_sections=(),
        )
        collapsed_html = " ".join(html.split())

        artist_list_start = html.index('<ul class="meta-list album-artist-meta">')
        artist_list_end = html.index("</ul>", artist_list_start)
        genre_item = html.index('<li class="album-genre-meta">')
        style_list_start = html.index('<ul class="meta-list album-style-meta">')
        style_list_end = html.index("</ul>", style_list_start)

        self.assertLess(artist_list_start, html.index("Autechre"))
        self.assertLess(html.index("Autechre"), genre_item)
        self.assertLess(genre_item, html.index("Electronic"))
        self.assertLess(html.index("Electronic"), artist_list_end)
        self.assertLess(artist_list_end, style_list_start)
        self.assertLess(style_list_start, html.index("IDM"))
        self.assertLess(html.index("IDM"), style_list_end)
        self.assertNotIn('<ul class="meta-list album-genre-meta">', html)
        self.assertIn("Electronic</a>,&nbsp;<a", collapsed_html)
        self.assertIn("IDM</a>,&nbsp;<a", collapsed_html)

    def test_album_edit_template_matches_album_identity_meta_grouping(self) -> None:
        album = AlbumDetails(
            album_id="autechre::tri-repetae",
            artist="Autechre",
            album_artists=("Autechre",),
            album="Tri Repetae",
            year=1995,
            track_count=0,
        )
        template = build_template_environment().get_template("player/album_edit.html")

        html = template.render(
            album=album,
            album_back_url="/albums/autechre::tri-repetae",
            album_root_links=(),
            album_artist_parts=("Autechre",),
            album_year_text="1995",
            album_genre_parts=("Electronic", "Experimental"),
            album_style_parts=("IDM", "Ambient Techno"),
            album_edit_action_url="/api/albums/autechre::tri-repetae/edit",
            album_tag_edit_sections=(),
            album_musicbrainz_release_mbid="",
            album_musicbrainz_release_group_mbid="",
        )

        title_year = '<span class="album-title-year-meta">1995</span>'
        title_text = '<span class="album-detail-title-text" title="Tri Repetae">Tri Repetae</span>'
        artist_list_start = html.index('<ul class="meta-list album-artist-meta">')
        artist_list_end = html.index("</ul>", artist_list_start)
        genre_item = html.index('<li class="album-genre-meta">')
        style_list_start = html.index('<ul class="meta-list album-style-meta">')
        style_list_end = html.index("</ul>", style_list_start)

        self.assertIn(f"{title_text}{title_year}", html)
        self.assertLess(html.index("Tri Repetae"), html.index(title_year))
        self.assertLess(html.index(title_year), artist_list_start)
        self.assertLess(artist_list_start, html.index("Autechre"))
        self.assertLess(html.index("Autechre"), genre_item)
        self.assertNotIn('<li class="album-year-meta">1995</li>', html)
        self.assertLess(genre_item, html.index("Electronic"))
        self.assertLess(html.index("Electronic"), artist_list_end)
        self.assertLess(artist_list_end, style_list_start)
        self.assertLess(style_list_start, html.index("IDM"))
        self.assertLess(html.index("IDM"), style_list_end)
        self.assertNotIn('<ul class="meta-list album-genre-meta">', html)
        self.assertIn("Electronic,&nbsp;Experimental", html)
        self.assertIn("IDM,&nbsp;Ambient Techno", html)

    def test_album_edit_template_renders_multiple_musicbrainz_groups_only(self) -> None:
        album = AlbumDetails(
            album_id="unknown::unknown",
            artist="__Unknown",
            album_artists=("__Unknown",),
            album="__Unknown",
            year=None,
            track_count=2,
        )
        roots = (
            LibraryRootFilterOption(position=0, path="/music/downloads", label=".../downloads"),
        )
        tracks = [
            make_track_view(
                1,
                root_position=0,
                path="/music/downloads/Unknown/01.mp3",
                album_id="unknown::unknown",
                album_artist="__Unknown",
                album="__Unknown",
                genres=("__Unknown",),
            ),
            make_track_view(
                2,
                root_position=0,
                path="/music/downloads/Artist/Album/01.mp3",
                album_id="unknown::unknown",
                album_artist="Artist",
                album="Album",
                genres=("Electronic",),
                styles=("Ambient",),
            ),
        ]
        musicbrainz_sections = album_tag_edit_sections(
            tracks,
            roots,
        )
        tag_section = album_tag_edit_section_for_tracks(tracks)
        template = build_template_environment().get_template("player/album_edit.html")

        html = template.render(
            album=album,
            album_back_url="/albums/unknown::unknown",
            album_root_links=(),
            album_artist_parts=("__Unknown",),
            album_year_text="",
            album_genre_parts=("__Unknown",),
            album_style_parts=(),
            album_edit_action_url="/api/albums/unknown::unknown/edit",
            album_delete_action_url="/api/albums/unknown::unknown/delete",
            album_cover_upload_enabled=False,
            album_musicbrainz_sections=musicbrainz_sections,
            album_tag_edit_section=tag_section,
            album_musicbrainz_release_mbid="",
            album_musicbrainz_release_group_mbid="",
        )
        normalized_html = " ".join(html.split())

        self.assertEqual(html.count("data-album-edit-form"), 1)
        self.assertEqual(html.count("data-album-delete-form"), 1)
        self.assertNotIn("data-album-cover-form", html)
        self.assertEqual(html.count("data-apply-album-edit"), 2)
        self.assertEqual(html.count("data-album-edit-status"), 1)
        self.assertEqual(html.count("data-album-delete-status"), 1)
        self.assertNotIn("data-album-cover-status", html)
        self.assertNotIn("data-album-musicbrainz-status", html)
        self.assertIn("album-edit-notice-icon", html)
        self.assertIn('fill="currentColor"', html)
        self.assertIn("Tag / Metadata Edits", html)
        delete_index = html.index("data-album-delete-form")
        tags_index = html.index("Tag / Metadata Edits")
        notice_index = html.index("album-edit-notice-icon")
        form_index = html.index("data-album-edit-form")
        self.assertLess(delete_index, tags_index)
        self.assertLess(tags_index, notice_index)
        self.assertLess(notice_index, form_index)
        self.assertIn(
            "These actions queue jobs that edit the metadata stored in the audio files",
            html,
        )
        self.assertIn("On rescan", html)
        self.assertIn("extract the updated metadata into Kukicha's library database", html)
        self.assertIn("Saves a MusicBrainz release or release-group URL", normalized_html)
        self.assertIn("album artist, album, and genres", normalized_html)
        self.assertIn("resolves genres against Kukicha's taxonomy", normalized_html)
        self.assertIn("writes provider-derived album artist", normalized_html)
        self.assertIn("Track titles", normalized_html)
        self.assertIn("track numbers are not changed", normalized_html)
        self.assertIn("Clearing the URL removes the saved metadata override without", html)
        self.assertNotIn(
            "Writes the album, album artist, genre, track artist, track number, and title fields",
            html,
        )
        self.assertEqual(html.count("data-metadata-group"), 2)
        self.assertIn('action="/api/albums/unknown::unknown/edit"', html)
        self.assertEqual(html.count("data-metadata-url-input"), 2)
        self.assertNotIn("data-musicbrainz-release-mbid-input", html)
        self.assertNotIn("data-musicbrainz-release-group-mbid-input", html)
        self.assertEqual(html.count('data-server-value=""'), 2)
        self.assertEqual(
            html.count('class="album-edit-panel settings-panel album-edit-section'),
            2,
        )
        self.assertEqual(html.count("data-metadata-track-id"), 2)
        self.assertIn(".../downloads/Unknown/", html)
        self.assertIn(".../downloads/Artist/Album/", html)
        self.assertNotIn("data-track-id=", html)
        self.assertNotIn("data-album-input", html)
        self.assertNotIn("data-album-artist-input", html)
        self.assertNotIn("data-album-genre-input", html)
        self.assertNotIn("data-track-artist-input", html)
        self.assertNotIn("data-track-number-input", html)
        self.assertNotIn("data-track-title-input", html)
        first_section_start = html.index(".../downloads/Unknown/")
        first_musicbrainz_input = html.index("data-metadata-url-input", first_section_start)
        second_section_start = html.index(".../downloads/Artist/Album/")
        second_musicbrainz_input = html.index("data-metadata-url-input", second_section_start)
        self.assertLess(first_section_start, first_musicbrainz_input)
        self.assertLess(first_musicbrainz_input, second_section_start)
        self.assertLess(second_section_start, second_musicbrainz_input)
        self.assertNotIn("Update Audio Tags", html)

    def test_album_edit_template_renders_single_group_combined_form_without_musicbrainz_url(self) -> None:
        album = AlbumDetails(
            album_id="brian-eno::ambient-1",
            artist="Brian Eno",
            album_artists=("Brian Eno",),
            album="Ambient 1",
            year=1978,
            track_count=2,
        )
        roots = (
            LibraryRootFilterOption(position=0, path="/music/downloads", label=".../downloads"),
        )
        tracks = [
            make_track_view(
                1,
                root_position=0,
                path="/music/downloads/Brian Eno/Ambient 1/01.flac",
                album_id=album.album_id,
                album_artist="Brian Eno",
                album="Ambient 1",
                genres=("Ambient",),
            ),
            make_track_view(
                2,
                root_position=0,
                path="/music/downloads/Brian Eno/Ambient 1/02.flac",
                album_id=album.album_id,
                album_artist="Brian Eno",
                album="Ambient 1",
                genres=("Ambient",),
            ),
        ]
        musicbrainz_sections = album_tag_edit_sections(tracks, roots)
        template = build_template_environment().get_template("player/album_edit.html")

        html = template.render(
            album=album,
            album_back_url="/albums/brian-eno::ambient-1",
            album_root_links=(),
            album_artist_parts=("Brian Eno",),
            album_year_text="1978",
            album_genre_parts=("Ambient",),
            album_style_parts=(),
            album_edit_action_url="/api/albums/brian-eno::ambient-1/edit",
            album_cover_upload_action_url="/api/albums/brian-eno::ambient-1/cover",
            album_cover_upload_enabled=True,
            album_delete_action_url="/api/albums/brian-eno::ambient-1/delete",
            album_musicbrainz_sections=musicbrainz_sections,
            album_tag_edit_section=album_tag_edit_section_for_tracks(tracks),
            album_musicbrainz_release_mbid="",
            album_musicbrainz_release_group_mbid="",
        )

        self.assertEqual(html.count("data-album-edit-form"), 1)
        self.assertEqual(html.count("data-album-delete-form"), 1)
        self.assertEqual(html.count("data-album-cover-form"), 1)
        self.assertEqual(html.count("data-apply-album-edit"), 2)
        self.assertEqual(html.count("data-album-edit-status"), 1)
        self.assertEqual(html.count("data-album-delete-status"), 1)
        self.assertEqual(html.count("data-album-cover-status"), 1)
        self.assertIn('action="/api/albums/brian-eno::ambient-1/edit"', html)
        self.assertIn('data-delete-url="/api/albums/brian-eno::ambient-1/delete"', html)
        self.assertIn('data-upload-url="/api/albums/brian-eno::ambient-1/cover"', html)
        self.assertEqual(html.count("data-metadata-group"), 1)
        self.assertIn("Update Audio Tags", html)
        notice_index = html.index("album-edit-notice-icon")
        delete_index = html.index("data-album-delete-form")
        cover_index = html.index("data-album-cover-form")
        tags_index = html.index("Tag / Metadata Edits")
        apply_index = html.index("data-apply-album-edit")
        self.assertLess(delete_index, cover_index)
        self.assertLess(cover_index, tags_index)
        self.assertLess(tags_index, notice_index)
        self.assertLess(notice_index, apply_index)
        self.assertIn(
            "These actions queue jobs that edit the metadata stored in the audio files. On rescan, Kukicha will extract the updated metadata into Kukicha's library database.",
            html,
        )
        self.assertIn("data-album-input", html)
        self.assertIn("data-album-artist-input", html)
        self.assertIn("data-album-genre-input", html)
        self.assertIn("data-track-artist-input", html)
        self.assertIn("data-track-number-input", html)
        self.assertIn("data-track-title-input", html)
        self.assertNotIn("data-album-input disabled", html)
        self.assertNotIn("data-album-artist-input disabled", html)
        self.assertNotIn("data-album-genre-input disabled", html)
        note_start = html.index("data-album-level-metadata-note")
        note_end = html.index(">", note_start)
        self.assertIn("hidden", html[note_start:note_end])

    def test_album_edit_template_disables_single_group_album_fields_with_musicbrainz_url(self) -> None:
        album = AlbumDetails(
            album_id="brian-eno::ambient-1",
            artist="Brian Eno",
            album_artists=("Brian Eno",),
            album="Ambient 1",
            year=1978,
            track_count=1,
        )
        roots = (
            LibraryRootFilterOption(position=0, path="/music/downloads", label=".../downloads"),
        )
        tracks = [
            make_track_view(
                1,
                root_position=0,
                path="/music/downloads/Brian Eno/Ambient 1/01.flac",
                album_id=album.album_id,
                album_artist="Brian Eno",
                album="Ambient 1",
                genres=("Ambient",),
            ),
        ]
        sections = album_tag_edit_sections(tracks, roots)
        musicbrainz_sections = [
            replace(
                sections[0],
                musicbrainz_url=(
                    "https://musicbrainz.org/release/"
                    "11111111-1111-1111-1111-111111111111"
                ),
            )
        ]
        template = build_template_environment().get_template("player/album_edit.html")

        html = template.render(
            album=album,
            album_back_url="/albums/brian-eno::ambient-1",
            album_root_links=(),
            album_artist_parts=("Brian Eno",),
            album_year_text="1978",
            album_genre_parts=("Ambient",),
            album_style_parts=(),
            album_edit_action_url="/api/albums/brian-eno::ambient-1/edit",
            album_musicbrainz_sections=musicbrainz_sections,
            album_tag_edit_section=album_tag_edit_section_for_tracks(tracks),
            album_musicbrainz_release_mbid="",
            album_musicbrainz_release_group_mbid="",
        )

        self.assertEqual(html.count("data-album-edit-form"), 1)
        self.assertIn("data-album-input disabled", html)
        self.assertIn("data-album-artist-input disabled", html)
        self.assertIn("data-album-genre-input disabled", html)
        self.assertEqual(html.count(" disabled>"), 3)
        self.assertIn("data-track-artist-input", html)
        self.assertIn("data-track-number-input", html)
        self.assertIn("data-track-title-input", html)
        note_start = html.index("data-album-level-metadata-note")
        note_end = html.index(">", note_start)
        self.assertNotIn("hidden", html[note_start:note_end])
        normalized_html = " ".join(html.split())
        self.assertIn(
            "Album, album artist, and genre are locked while a metadata URL is set",
            normalized_html,
        )
        self.assertIn("Clear the URL to edit them manually.", normalized_html)
        self.assertIn("album-level-musicbrainz-note-icon", html)
        self.assertLess(
            html.index("album-level-musicbrainz-note-icon"),
            html.index("Album, album artist, and genre are locked"),
        )

    def test_album_edit_template_prefills_group_musicbrainz_urls(self) -> None:
        album = AlbumDetails(
            album_id="aphex-twin::selected-ambient-works-volume-ii::09f",
            artist="Aphex Twin",
            album_artists=("Aphex Twin",),
            album="Selected Ambient Works, Volume II",
            year=1994,
            track_count=2,
        )
        roots = (
            LibraryRootFilterOption(position=0, path="/music/downloaded", label=".../downloaded"),
            LibraryRootFilterOption(position=1, path="/music/amazon", label=".../amazon"),
        )
        tracks = [
            make_track_view(
                1,
                root_position=0,
                path="/music/downloaded/Aphex Twin/Selected Ambient Works, Volume II/01.flac",
                album_id=album.album_id,
                album="Selected Ambient Works, Volume II",
            ),
            make_track_view(
                2,
                root_position=1,
                path="/music/amazon/Aphex Twin/Selected Ambient Works Volume II/01.mp3",
                album_id=album.album_id,
                album="Selected Ambient Works, Volume II",
            ),
        ]
        sections = album_tag_edit_sections(tracks, roots)
        musicbrainz_sections = [
            replace(
                sections[0],
                musicbrainz_url=(
                    "https://musicbrainz.org/release/"
                    "6d0aaf02-f571-4c03-8677-23018ff628ee"
                ),
            ),
            replace(
                sections[1],
                musicbrainz_url=(
                    "https://musicbrainz.org/release/"
                    "6439fcbe-b404-4cf4-ac58-4816c43cf2e3"
                ),
            ),
        ]
        template = build_template_environment().get_template("player/album_edit.html")

        html = template.render(
            album=album,
            album_back_url="/albums/aphex-twin::selected-ambient-works-volume-ii::09f",
            album_root_links=(),
            album_artist_parts=("Aphex Twin",),
            album_year_text="1994",
            album_genre_parts=("Electronic",),
            album_style_parts=("Ambient",),
            album_edit_action_url=(
                "/api/albums/aphex-twin::selected-ambient-works-volume-ii::09f/edit"
            ),
            album_musicbrainz_sections=musicbrainz_sections,
            album_tag_edit_section=album_tag_edit_section_for_tracks(tracks),
            album_musicbrainz_release_mbid="",
            album_musicbrainz_release_group_mbid="",
        )

        self.assertIn(
            'value="https://musicbrainz.org/release/6d0aaf02-f571-4c03-8677-23018ff628ee"',
            html,
        )
        self.assertIn(
            'data-server-value="https://musicbrainz.org/release/6439fcbe-b404-4cf4-ac58-4816c43cf2e3"',
            html,
        )

    def test_album_edit_template_shows_cover_upload_when_enabled(self) -> None:
        album = AlbumDetails(
            album_id="aphex-twin::selected-ambient-works-volume-ii::09f",
            artist="Aphex Twin",
            album_artists=("Aphex Twin",),
            album="Selected Ambient Works, Volume II",
            year=1994,
            track_count=1,
        )
        roots = (
            LibraryRootFilterOption(position=0, path="/music/downloaded", label=".../downloaded"),
        )
        tracks = [
            make_track_view(
                1,
                root_position=0,
                path="/music/downloaded/Aphex Twin/Selected Ambient Works, Volume II/01.flac",
                album_id=album.album_id,
                album="Selected Ambient Works, Volume II",
            ),
        ]
        musicbrainz_sections = album_tag_edit_sections(tracks, roots)
        template = build_template_environment().get_template("player/album_edit.html")

        html = template.render(
            album=album,
            album_back_url="/albums/aphex-twin::selected-ambient-works-volume-ii::09f",
            album_root_links=(),
            album_artist_parts=("Aphex Twin",),
            album_year_text="1994",
            album_genre_parts=("Electronic",),
            album_style_parts=(),
            album_edit_action_url=(
                "/api/albums/aphex-twin::selected-ambient-works-volume-ii::09f/edit"
            ),
            album_cover_upload_action_url=(
                "/api/albums/aphex-twin::selected-ambient-works-volume-ii::09f/cover"
            ),
            album_cover_upload_enabled=True,
            album_delete_action_url=(
                "/api/albums/aphex-twin::selected-ambient-works-volume-ii::09f/delete"
            ),
            album_musicbrainz_sections=musicbrainz_sections,
            album_tag_edit_section=album_tag_edit_section_for_tracks(tracks),
            album_musicbrainz_release_mbid="",
            album_musicbrainz_release_group_mbid="",
        )

        delete_index = html.index("Delete Album")
        cover_index = html.index("Upload Cover")
        apply_index = html.index("data-apply-album-edit")
        self.assertLess(delete_index, cover_index)
        self.assertLess(cover_index, apply_index)
        self.assertIn("data-album-cover-input", html)
        self.assertIn("data-upload-album-cover", html)
        self.assertIn(
            'data-upload-url="/api/albums/aphex-twin::selected-ambient-works-volume-ii::09f/cover"',
            html,
        )
        self.assertIn("Existing cover files with the same extension will be overwritten", html)

    def test_album_musicbrainz_edit_sections_prefill_urls_from_track_links(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            connection = connect_database(database)
            try:
                connection.executemany(
                    """
                    INSERT INTO album_musicbrainz_track_links (
                        path, file_album_id, release_mbid, release_group_mbid
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        (
                            "/music/downloaded/Aphex Twin/Selected Ambient Works, Volume II/01.flac",
                            "aphex-twin::selected-ambient-works-volume-ii",
                            "6d0aaf02-f571-4c03-8677-23018ff628ee",
                            "0e7a233f-81f8-3e63-ad07-6cdfe2faecc3",
                        ),
                        (
                            "/music/amazon/Aphex Twin/Selected Ambient Works Volume II/01.mp3",
                            "aphex-twin::selected-ambient-works-volume-ii",
                            "6439fcbe-b404-4cf4-ac58-4816c43cf2e3",
                            "0e7a233f-81f8-3e63-ad07-6cdfe2faecc3",
                        ),
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            roots = (
                LibraryRootFilterOption(position=0, path="/music/downloaded", label=".../downloaded"),
                LibraryRootFilterOption(position=1, path="/music/amazon", label=".../amazon"),
            )
            tracks = [
                make_track_view(
                    1,
                    root_position=0,
                    path="/music/downloaded/Aphex Twin/Selected Ambient Works, Volume II/01.flac",
                    album_id="aphex-twin::selected-ambient-works-volume-ii::09f",
                    album="Selected Ambient Works, Volume II",
                ),
                make_track_view(
                    2,
                    root_position=1,
                    path="/music/amazon/Aphex Twin/Selected Ambient Works Volume II/01.mp3",
                    album_id="aphex-twin::selected-ambient-works-volume-ii::09f",
                    album="Selected Ambient Works, Volume II",
                ),
            ]

            sections = album_musicbrainz_edit_sections(
                database,
                "aphex-twin::selected-ambient-works-volume-ii::09f",
                tracks,
                roots,
            )

            self.assertEqual(
                [section.musicbrainz_url for section in sections],
                [
                    (
                        "https://musicbrainz.org/release/"
                        "6d0aaf02-f571-4c03-8677-23018ff628ee"
                    ),
                    (
                        "https://musicbrainz.org/release/"
                        "6439fcbe-b404-4cf4-ac58-4816c43cf2e3"
                    ),
                ],
            )

    def test_album_templates_render_year_without_root_title_links(self) -> None:
        album = AlbumDetails(
            album_id="brian-eno-robert-fripp::no-pussyfooting",
            artist="Brian Eno, Robert Fripp",
            album_artists=("Brian Eno", "Robert Fripp"),
            album="No Pussyfooting",
            year=1973,
            track_count=2,
            tracks=(
                PlaylistTrack(path="/music/a/Brian Eno/No Pussyfooting/01.flac", root_position=0),
                PlaylistTrack(path="/music/b/Brian Eno/No Pussyfooting/02.flac", root_position=2),
            ),
        )
        album_template = build_template_environment().get_template("player/album.html")
        edit_template = build_template_environment().get_template("player/album_edit.html")

        album_html = album_template.render(
            album=album,
            album_back_url="/",
            album_edit_page_url="/albums/brian-eno-robert-fripp::no-pussyfooting/edit",
            album_artist_links=album_artist_links(album, AlbumListQuery()),
            album_genre_links=({"label": "Ambient", "url": "/?genre=Ambient"},),
            album_year_text="1973",
            album_style_links=({"label": "Frippertronics", "url": "/?style=Frippertronics"},),
            track_sections=(),
        )
        edit_html = edit_template.render(
            album=album,
            album_back_url="/albums/brian-eno-robert-fripp::no-pussyfooting",
            album_artist_parts=("Brian Eno", "Robert Fripp"),
            album_year_text="1973",
            album_genre_parts=("Ambient",),
            album_style_parts=("Frippertronics",),
            album_edit_action_url="/api/albums/brian-eno-robert-fripp::no-pussyfooting/edit",
            album_tag_edit_sections=(),
            album_musicbrainz_release_mbid="",
            album_musicbrainz_release_group_mbid="",
        )

        self.assertNotIn("album-title-root-links", album_html)
        self.assertNotIn("album-title-root-links", edit_html)
        self.assertNotIn('href="/?root=0"', album_html)
        self.assertNotIn('href="/?root=2"', album_html)
        self.assertIn("Brian Eno</a>,&nbsp;<a", album_html)
        self.assertIn('href="/albums?artist=Brian+Eno"', album_html)
        self.assertIn('href="/albums?artist=Robert+Fripp"', album_html)
        self.assertLess(
            album_html.index('<span class="album-title-year-meta">1973</span>'),
            album_html.index("album-artist-meta"),
        )
        self.assertLess(
            edit_html.index('<span class="album-title-year-meta">1973</span>'),
            edit_html.index("album-artist-meta"),
        )
        self.assertNotIn('href="/?root=0"', edit_html)


class PlayerWebAdapterTest(unittest.TestCase):
    def write_config(self, config_path: Path, text: str, *, password: str = "secret") -> None:
        password_hash_file = config_path.parent / "password.hash"
        password_hash_file.write_text(f"{hash_password(password)}\n", encoding="utf-8")
        password_hash_file.chmod(0o600)
        config_body = text.rstrip()
        auth_section = "\n".join(
            (
                "[auth]",
                "username = 'listener'",
                "password_hash_file = 'password.hash'",
            )
        )
        output = f"{config_body}\n\n{auth_section}\n" if config_body else f"{auth_section}\n"
        config_path.write_text(output, encoding="utf-8")

    def logged_in_client(self, app):
        client = app.test_client()
        response = client.post(
            "/login",
            data={"username": "listener", "password": "secret"},
        )
        self.assertEqual(response.status_code, 302)
        return client

    def make_options(self, temp_path: Path) -> PlayerServerOptions:
        return PlayerServerOptions(
            config_path=temp_path / "kukicha.toml",
            database=temp_path / "kukicha.sqlite",
            ffmpeg_path=None,
        )

    def make_auth_options(
        self,
        temp_path: Path,
        *,
        cookie_max_age_seconds: int = 180 * 24 * 60 * 60,
    ) -> PlayerServerOptions:
        password_hash_file = temp_path / "password.hash"
        password_hash_file.write_text(f"{hash_password('secret')}\n", encoding="utf-8")
        password_hash_file.chmod(0o600)
        return replace(
            self.make_options(temp_path),
            auth=PlayerAuthOptions(
                username="listener",
                password_hash_file=password_hash_file,
                cookie_max_age="180d",
                cookie_max_age_seconds=cookie_max_age_seconds,
                cookie_name="kukicha_cookie",
            ),
        )

    def seed_recommendation_database(self, database: Path) -> None:
        with connect_database(database) as connection:
            connection.executemany(
                """
                INSERT INTO library_albums (album_id, album, year, track_count)
                VALUES (?, ?, ?, ?)
                """,
                (
                    ("album-seed", "Seed Album", 1992, 1),
                    ("album-match", "Match Album", 1992, 1),
                    ("album-same-artist", "Same Artist Album", 2001, 1),
                    ("album-jazz", "Jazz Album", 1984, 1),
                ),
            )
            connection.executemany(
                """
                INSERT INTO library_tracks (
                    track_id,
                    album_id,
                    path,
                    file_type,
                    scan_error,
                    artist,
                    album_artist,
                    album,
                    title,
                    date
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        1,
                        "album-seed",
                        "/music/seed/01.flac",
                        "flac",
                        None,
                        "Seed Artist",
                        "Seed Artist",
                        "Seed Album",
                        "Seed Song",
                        "1992",
                    ),
                    (
                        2,
                        "album-match",
                        "/music/match/01.flac",
                        "flac",
                        None,
                        "Other Artist",
                        "Other Artist",
                        "Match Album",
                        "Closest Song",
                        "1992",
                    ),
                    (
                        3,
                        "album-same-artist",
                        "/music/same-artist/01.flac",
                        "flac",
                        None,
                        "Seed Artist",
                        "Seed Artist",
                        "Same Artist Album",
                        "Same Artist Song",
                        "2001",
                    ),
                    (
                        4,
                        "album-jazz",
                        "/music/jazz/01.flac",
                        "flac",
                        None,
                        "Jazz Artist",
                        "Jazz Artist",
                        "Jazz Album",
                        "Jazz Song",
                        "1984",
                    ),
                ),
            )
            connection.executemany(
                """
                INSERT INTO library_track_genres (track_id, position, genre)
                VALUES (?, ?, ?)
                """,
                (
                    (1, 0, "Rock"),
                    (2, 0, "Rock"),
                    (3, 0, "Electronic"),
                    (4, 0, "Jazz"),
                ),
            )
            connection.executemany(
                """
                INSERT INTO library_track_styles (track_id, position, style)
                VALUES (?, ?, ?)
                """,
                (
                    (1, 0, "Dream Pop"),
                    (2, 0, "Dream Pop"),
                    (3, 0, "Minimalism"),
                    (4, 0, "Hard Bop"),
                ),
            )
            connection.execute(
                """
                INSERT INTO track_user_state (track_path, starred_at)
                VALUES (?, ?)
                """,
                ("/music/seed/01.flac", "2026-06-01T10:00:00+00:00"),
            )
            connection.execute(
                """
                INSERT INTO play_track_stats (
                    track_path,
                    play_count,
                    last_played_at,
                    track_id,
                    album_id,
                    path,
                    title,
                    artist,
                    album
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "/music/match/01.flac",
                    7,
                    "2026-05-20T12:00:00+00:00",
                    2,
                    "album-match",
                    "/music/match/01.flac",
                    "Closest Song",
                    "Other Artist",
                    "Match Album",
                ),
            )

    def make_runtime(self, database: Path) -> Mock:
        runtime = Mock()
        runtime.database = database
        runtime.queue_state_copy.return_value = PlayerQueueState(track_ids=[])
        runtime.active_job_payloads.return_value = []
        runtime.library_filter_options.side_effect = (
            lambda: LibraryQueries(database).filter_options()
        )
        return runtime

    def test_healthz_returns_no_content(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            runtime = self.make_runtime(temp_path / "kukicha.sqlite")
            with patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime):
                app = create_player_app(self.make_options(temp_path))

            response = app.test_client().get("/healthz")

            self.assertEqual(response.status_code, 204)

    def test_opensubsonic_routes_return_not_found_when_not_configured(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            runtime = self.make_runtime(temp_path / "kukicha.sqlite")
            with patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime):
                app = create_player_app(self.make_auth_options(temp_path))

            response = app.test_client().get(
                "/rest/ping",
                query_string={
                    "u": "listener",
                    "p": "sonic-secret",
                    "v": "1.16.1",
                    "c": "kukicha-test",
                    "f": "json",
                },
            )

            self.assertEqual(response.status_code, 404)

    def test_player_auth_redirects_pages_and_rejects_api_and_media(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            runtime = self.make_runtime(temp_path / "kukicha.sqlite")
            with patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime):
                app = create_player_app(self.make_auth_options(temp_path))
            client = app.test_client()

            page_response = client.get("/help")
            api_response = client.get("/api/jobs/events")
            recommendation_response = client.get("/recommendations/daily")
            audio_response = client.get("/audio/1")
            static_response = client.get("/static/player.css")
            health_response = client.get("/healthz")

            self.assertEqual(page_response.status_code, 302)
            self.assertEqual(page_response.headers["Location"], "/login?next=%2Fhelp")
            self.assertEqual(api_response.status_code, 401)
            self.assertEqual(api_response.get_json(), {"error": "authentication required"})
            self.assertEqual(recommendation_response.status_code, 401)
            self.assertEqual(
                recommendation_response.get_json(),
                {"error": "authentication required"},
            )
            self.assertEqual(audio_response.status_code, 401)
            self.assertEqual(static_response.status_code, 200)
            self.assertEqual(health_response.status_code, 204)

    def test_login_page_uses_fingerprinted_static_assets(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            runtime = self.make_runtime(temp_path / "kukicha.sqlite")
            with patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime):
                app = create_player_app(self.make_auth_options(temp_path))

            response = app.test_client().get("/login")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["Cache-Control"], HTML_CACHE_CONTROL)
            html = response.data.decode()
            self.assertIn(f'href="{static_asset_url("favicon.svg")}"', html)
            self.assertIn(f'href="{static_asset_url("player.css")}"', html)
            self.assertNotIn('href="/static/favicon.svg"', html)
            self.assertNotIn('href="/static/player.css"', html)

    def test_trusted_proxy_headers_use_forwarded_client_ip(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            runtime = self.make_runtime(temp_path / "kukicha.sqlite")
            options = replace(
                self.make_auth_options(temp_path),
                trusted_proxy_headers=True,
            )
            with patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime):
                app = create_player_app(options)
            client = self.logged_in_client(app)

            response = client.get(
                "/help",
                environ_base={"REMOTE_ADDR": "127.0.0.1"},
                headers={"X-Forwarded-For": "198.51.100.25"},
            )

            self.assertEqual(response.status_code, 200)
            self.assertIn(b"<dt>Client IP</dt>", response.data)
            self.assertIn(b"<dd><code>198.51.100.25</code></dd>", response.data)
            self.assertNotIn(b"<dd><code>127.0.0.1</code></dd>", response.data)

    def test_player_login_sets_hardened_cookie_and_allows_access(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            runtime = self.make_runtime(temp_path / "kukicha.sqlite")
            with patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime):
                app = create_player_app(self.make_auth_options(temp_path))
            client = app.test_client()

            login_response = client.post(
                "/login?next=/help",
                data={"username": "listener", "password": "secret"},
            )
            help_response = client.get("/help")

            self.assertEqual(login_response.status_code, 302)
            self.assertEqual(login_response.headers["Location"], "/help")
            cookie = login_response.headers["Set-Cookie"]
            self.assertIn("kukicha_cookie=", cookie)
            self.assertIn("Max-Age=15552000", cookie)
            self.assertIn("HttpOnly", cookie)
            self.assertIn("SameSite=Strict", cookie)
            self.assertNotIn("Secure", cookie)
            self.assertEqual(help_response.status_code, 200)

    def test_player_login_rejects_bad_credentials(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            runtime = self.make_runtime(temp_path / "kukicha.sqlite")
            with patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime):
                app = create_player_app(self.make_auth_options(temp_path))

            response = app.test_client().post(
                "/login",
                data={"username": "listener", "password": "wrong"},
            )

            self.assertEqual(response.status_code, 401)
            self.assertNotIn("Set-Cookie", response.headers)
            self.assertIn(b"Invalid username or password", response.data)

    def test_player_auth_rejects_tampered_and_expired_cookies(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            runtime = self.make_runtime(temp_path / "kukicha.sqlite")
            auth_options = self.make_auth_options(temp_path, cookie_max_age_seconds=-1)
            assert auth_options.auth is not None
            expired_cookie = signed_auth_cookie(auth_options.auth)
            with patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime):
                app = create_player_app(auth_options)

            tampered_client = app.test_client()
            tampered_client.set_cookie("kukicha_cookie", "not-a-real-cookie")
            expired_client = app.test_client()
            expired_client.set_cookie("kukicha_cookie", expired_cookie)

            tampered_response = tampered_client.get("/help")
            expired_response = expired_client.get("/help")

            self.assertEqual(tampered_response.status_code, 302)
            self.assertEqual(expired_response.status_code, 302)

    def test_album_star_api_toggles_album_and_templates_render_state(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            save_library(
                MusicLibrary(
                    roots=[],
                    tracks=[
                        TrackRecord(
                            path="/music/Artist/Album/01.flac",
                            file_type="flac",
                            artist="Artist",
                            album_artist="Artist",
                            album="Album",
                            title="Track",
                        )
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-05-01T00:00:00+00:00",
                ),
                database,
            )
            runtime = self.make_runtime(database)
            with patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime):
                app = create_player_app(self.make_options(temp_path))
                client = app.test_client()
                initial_response = client.get("/albums")
                star_response = client.post(
                    "/api/albums/artist::album/star",
                    json={"starred": True},
                )
                starred_grid_response = client.get("/albums")
                starred_detail_response = client.get("/albums/artist::album")
                with connect_database(database, create=False) as connection:
                    starred_state = connection.execute(
                        """
                        SELECT starred_at
                        FROM album_user_state
                        WHERE album_id = ?
                        """,
                        ("artist::album",),
                    ).fetchone()
                unstar_response = client.post(
                    "/api/albums/artist::album/star",
                    json={"starred": False},
                )
                with connect_database(database, create=False) as connection:
                    unstarred_state = connection.execute(
                        """
                        SELECT starred_at
                        FROM album_user_state
                        WHERE album_id = ?
                        """,
                        ("artist::album",),
                    ).fetchone()
                missing_response = client.post(
                    "/api/albums/missing::album/star",
                    json={"starred": True},
                )

        initial_html = initial_response.data.decode()
        starred_grid_html = starred_grid_response.data.decode()
        starred_detail_html = starred_detail_response.data.decode()
        self.assertEqual(star_response.status_code, 200)
        self.assertTrue(star_response.json["starred"])
        self.assertIsNotNone(starred_state)
        self.assertEqual(starred_state["starred_at"], star_response.json["starred_at"])
        self.assertEqual(unstar_response.status_code, 200)
        self.assertFalse(unstar_response.json["starred"])
        self.assertIsNone(unstar_response.json["starred_at"])
        self.assertIsNone(unstarred_state)
        self.assertEqual(missing_response.status_code, 404)
        self.assertIn('data-album-star-toggle data-album-id="artist::album"', initial_html)
        self.assertIn('aria-pressed="false"', initial_html)
        self.assertIn('class="album-star-toggle starred"', starred_grid_html)
        self.assertIn('aria-pressed="true"', starred_grid_html)
        self.assertIn('class="album-detail-title-line"', starred_detail_html)
        self.assertIn('class="album-star-toggle starred"', starred_detail_html)

    def test_filtered_album_star_api_updates_matches_without_retimestamping(self) -> None:
        old_starred_at = "2026-05-01T00:00:00Z"
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            save_library(
                MusicLibrary(
                    roots=[],
                    tracks=[
                        TrackRecord(
                            path="/music/Brian Eno/Ambient One/01.flac",
                            file_type="flac",
                            artist="Brian Eno",
                            album_artist="Brian Eno",
                            album="Ambient One",
                            title="Track",
                        ),
                        TrackRecord(
                            path="/music/Brian Eno/Already Starred/01.flac",
                            file_type="flac",
                            artist="Brian Eno",
                            album_artist="Brian Eno",
                            album="Already Starred",
                            title="Track",
                        ),
                        TrackRecord(
                            path="/music/Other Artist/Outside/01.flac",
                            file_type="flac",
                            artist="Other Artist",
                            album_artist="Other Artist",
                            album="Outside",
                            title="Track",
                        ),
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-05-01T00:00:00+00:00",
                ),
                database,
            )
            with connect_database(database, create=False) as connection:
                album_ids = {
                    str(row["album"]): str(row["album_id"])
                    for row in connection.execute(
                        "SELECT album_id, album FROM library_albums"
                    )
                }
                already_starred_id = album_ids["Already Starred"]
                connection.execute(
                    """
                    INSERT INTO album_user_state (album_id, starred_at)
                    VALUES (?, ?)
                    """,
                    (already_starred_id, old_starred_at),
                )
                connection.execute(
                    """
                    UPDATE library_albums
                    SET starred_at = ?
                    WHERE album_id = ?
                    """,
                    (old_starred_at, already_starred_id),
                )

            runtime = self.make_runtime(database)
            with patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime):
                app = create_player_app(self.make_options(temp_path))
                client = app.test_client()
                star_response = client.post(
                    "/api/albums/star?artist=Brian+Eno",
                    json={"starred": True},
                )
                with connect_database(database, create=False) as connection:
                    starred_rows = {
                        str(row["album"]): row["starred_at"]
                        for row in connection.execute(
                            """
                            SELECT album, starred_at
                            FROM library_albums
                            ORDER BY album
                            """
                        )
                    }
                    already_state = connection.execute(
                        """
                        SELECT starred_at
                        FROM album_user_state
                        WHERE album_id = ?
                        """,
                        (already_starred_id,),
                    ).fetchone()
                unstar_response = client.post(
                    "/api/albums/star?artist=Brian+Eno",
                    json={"starred": False},
                )
                with connect_database(database, create=False) as connection:
                    unstarred_rows = {
                        str(row["album"]): row["starred_at"]
                        for row in connection.execute(
                            """
                            SELECT album, starred_at
                            FROM library_albums
                            ORDER BY album
                            """
                        )
                    }
                    remaining_state_count = int(
                        connection.execute(
                            "SELECT COUNT(*) AS count FROM album_user_state"
                        ).fetchone()["count"]
                    )

        self.assertEqual(star_response.status_code, 200)
        self.assertEqual(star_response.json["matched_count"], 2)
        self.assertEqual(star_response.json["changed_count"], 1)
        self.assertEqual(star_response.json["message"], "Starred 1 filtered album.")
        self.assertIsNotNone(starred_rows["Ambient One"])
        self.assertEqual(starred_rows["Already Starred"], old_starred_at)
        self.assertIsNone(starred_rows["Outside"])
        self.assertIsNotNone(already_state)
        self.assertEqual(already_state["starred_at"], old_starred_at)
        self.assertEqual(unstar_response.status_code, 200)
        self.assertEqual(unstar_response.json["matched_count"], 2)
        self.assertEqual(unstar_response.json["changed_count"], 2)
        self.assertEqual(unstar_response.json["message"], "Unstarred 2 filtered albums.")
        self.assertIsNone(unstarred_rows["Ambient One"])
        self.assertIsNone(unstarred_rows["Already Starred"])
        self.assertIsNone(unstarred_rows["Outside"])
        self.assertEqual(remaining_state_count, 0)

    def test_home_empty_state_links_to_library_pages_and_query_redirects_to_albums(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            save_library(
                MusicLibrary(
                    roots=[],
                    tracks=[],
                    supported_extensions=[],
                    generated_at="2026-05-01T00:00:00+00:00",
                ),
                database,
            )
            runtime = self.make_runtime(database)
            with patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime):
                app = create_player_app(self.make_options(temp_path))
                client = app.test_client()
                home_response = client.get("/")
                redirect_response = client.get("/?artist=Artist+A&search=Road")

        self.assertEqual(home_response.status_code, 200)
        self.assertIn(b"<h1>Home</h1>", home_response.data)
        home_header = home_response.data.split(b'<section class="home-empty"', 1)[0]
        self.assertIn(b'class="filter-menu page-menu"', home_header)
        self.assertIn(b'aria-current="page">Home</a>', home_header)
        self.assertNotIn(b'<a class="button-link" href="/albums" data-nav>Albums</a>', home_header)
        self.assertIn(b"No listening history yet", home_response.data)
        self.assertIn(b'href="/albums" data-nav>Albums</a>', home_response.data)
        self.assertIn(b'href="/artists" data-nav>Artists</a>', home_response.data)
        self.assertIn(b'href="/playlists" data-nav>Playlists</a>', home_response.data)
        self.assertEqual(redirect_response.status_code, 302)
        self.assertEqual(
            redirect_response.headers["Location"],
            "/albums?artist=Artist+A&search=Road",
        )

    def test_home_shows_recently_added_albums_from_file_created_at(self) -> None:
        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):  # type: ignore[override]
                return datetime(2026, 5, 10, tzinfo=tz or UTC)

        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            save_library(
                MusicLibrary(
                    roots=["/music"],
                    tracks=[
                        TrackRecord(
                            path=f"/music/New Artist {index:02d}/New Album {index:02d}/01.flac",
                            root_position=0,
                            file_created_at=f"2026-05-09T{index:02d}:00:00+00:00",
                            file_type="flac",
                            artist=f"New Artist {index:02d}",
                            album_artist=f"New Artist {index:02d}",
                            album=f"New Album {index:02d}",
                            title=f"New Track {index:02d}",
                        )
                        for index in range(22)
                    ]
                    + [
                        TrackRecord(
                            path="/music/Old Artist/Old Album/01.flac",
                            root_position=0,
                            file_created_at="2026-03-01T12:00:00+00:00",
                            file_type="flac",
                            artist="Old Artist",
                            album_artist="Old Artist",
                            album="Old Album",
                            title="Old Track",
                        ),
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-05-10T00:00:00+00:00",
                ),
                database,
            )
            with connect_database(database) as connection:
                connection.execute(
                    "UPDATE library_albums SET added_at = file_created_at"
                )
            runtime = self.make_runtime(database)
            with (
                patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime),
                patch("kukicha.use_case.listening.datetime", FixedDateTime),
            ):
                dashboard = home_dashboard(database)
                app = create_player_app(self.make_options(temp_path))
                response = app.test_client().get("/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(dashboard.recently_added_albums), 14)
        self.assertIn(b"Added in the Last Month", response.data)
        self.assertIn(b"New Album 21", response.data)
        self.assertIn(b"New Album 08", response.data)
        self.assertNotIn(b"New Album 07", response.data)
        self.assertNotIn(b"New Album 00", response.data)
        self.assertNotIn(b"Old Album", response.data)

    def test_home_falls_back_to_latest_added_albums_when_month_is_empty(self) -> None:
        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):  # type: ignore[override]
                return datetime(2026, 5, 10, tzinfo=tz or UTC)

        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            save_library(
                MusicLibrary(
                    roots=["/music"],
                    tracks=[
                        TrackRecord(
                            path=f"/music/Archive Artist {index:02d}/Archive Album {index:02d}/01.flac",
                            root_position=0,
                            file_created_at=f"2026-03-{index + 1:02d}T12:00:00+00:00",
                            file_type="flac",
                            artist=f"Archive Artist {index:02d}",
                            album_artist=f"Archive Artist {index:02d}",
                            album=f"Archive Album {index:02d}",
                            title=f"Archive Track {index:02d}",
                        )
                        for index in range(22)
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-05-10T00:00:00+00:00",
                ),
                database,
            )
            with connect_database(database) as connection:
                connection.execute(
                    "UPDATE library_albums SET added_at = file_created_at"
                )
            runtime = self.make_runtime(database)
            with (
                patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime),
                patch("kukicha.use_case.listening.datetime", FixedDateTime),
            ):
                dashboard = home_dashboard(database)
                app = create_player_app(self.make_options(temp_path))
                response = app.test_client().get("/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(dashboard.recently_added_albums), 14)
        self.assertNotIn(b"Added in the Last Month", response.data)
        self.assertIn(b"Most Recently Added Since", response.data)
        self.assertIn(
            b'<time data-local-date datetime="2026-03-09T12:00:00+00:00">2026-03-09</time>',
            response.data,
        )
        self.assertIn(b"Archive Album 21", response.data)
        self.assertIn(b"Archive Album 08", response.data)
        self.assertNotIn(b"Archive Album 07", response.data)
        self.assertNotIn(b"Archive Album 00", response.data)

    def test_search_page_renders_entity_sections_and_home_search_form(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            save_library(
                MusicLibrary(
                    roots=["/music"],
                    tracks=[
                        TrackRecord(
                            path="/music/Jim Hall/Undercurrent/01.flac",
                            root_position=0,
                            file_type="flac",
                            artist="Jim Hall",
                            album_artist="Jim Hall & Bill Evans",
                            album="Undercurrent",
                            title="My Funny Valentine",
                        ),
                        TrackRecord(
                            path="/music/Alice Coltrane/Journey/01.flac",
                            root_position=0,
                            file_type="flac",
                            artist="Alice Coltrane",
                            album_artist="Alice Coltrane",
                            album="Journey in Satchidananda",
                            title="Journey in Satchidananda",
                        ),
                    ],
                    supported_extensions=[".flac"],
                    generated_at="test",
                ),
                database,
            )
            with connect_database(database, create=False) as connection:
                album_id = str(
                    connection.execute(
                        "SELECT album_id FROM library_albums WHERE album = ?",
                        ("Undercurrent",),
                    ).fetchone()["album_id"]
                )
            app = create_player_app(self.make_options(temp_path))
            client = app.test_client()

            home_response = client.get("/")
            search_response = client.get(
                "/search?query=Bill&artistCount=10&albumCount=10&songCount=10"
            )
            album_search_response = client.get(
                "/search?query=Undercurrent&artistCount=0&albumCount=10&songCount=0"
            )
            track_search_response = client.get(
                "/search?query=Funny&artistCount=0&albumCount=0&songCount=10"
            )
            selected_album_response = client.get(
                f"/albums/{album_id}?selectedTrackId=1"
            )
            paginated_search_response = client.get(
                "/search?query=&artistCount=1&albumCount=1&songCount=1"
            )
            album_paginated_search_response = client.get(
                "/search?query=&artistCount=1&albumCount=1&albumOffset=1&songCount=1"
            )

        self.assertIn(b'action="/search"', home_response.data)
        self.assertIn(b"data-search-form", home_response.data)
        self.assertIn(b'name="query"', home_response.data)
        self.assertIn(b"data-search-form", search_response.data)
        self.assertIn(
            b'<a class="back-link" href="/" data-history-back data-nav>&larr; back</a>',
            search_response.data,
        )
        self.assertNotIn(b'filter-menu page-menu', search_response.data)
        self.assertIn(b"<h2", search_response.data)
        self.assertIn(b"Artists", search_response.data)
        self.assertIn(b"Albums", search_response.data)
        self.assertIn(b"Tracks", search_response.data)
        self.assertIn(b"Bill Evans", search_response.data)
        self.assertIn(b"No albums found.", search_response.data)
        self.assertIn(b"No tracks found.", search_response.data)
        self.assertIn(b"search-album-row", album_search_response.data)
        self.assertIn(b"search-result-row", album_search_response.data)
        self.assertIn(b"search-result-cover-wrap", album_search_response.data)
        self.assertIn(b"data-album-playback-source", album_search_response.data)
        self.assertNotIn(b"home-card-grid", album_search_response.data)
        self.assertIn(b"?selectedTrackId=1", track_search_response.data)
        self.assertIn(
            b'<span class="home-track-cover album-cover-placeholder"',
            track_search_response.data,
        )
        self.assertNotIn(
            b'<img class="home-track-cover" src="/art/250/1"',
            track_search_response.data,
        )
        self.assertIn(b'class="selected" data-selected-track', selected_album_response.data)
        self.assertIn(b'data-track-id="1"', selected_album_response.data)
        self.assertIn(b"data-preserve-scroll", paginated_search_response.data)
        self.assertIn(
            b"href=\"/search?query=&amp;artistCount=1&amp;artistOffset=0"
            b"&amp;albumCount=1&amp;albumOffset=1"
            b"&amp;songCount=1&amp;songOffset=1\"",
            album_paginated_search_response.data,
        )

    def test_home_shows_recently_favorited_albums_below_recently_added(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            save_library(
                MusicLibrary(
                    roots=["/music"],
                    tracks=[
                        TrackRecord(
                            path=f"/music/Artist {index:02d}/Album {index:02d}/01.flac",
                            root_position=0,
                            file_created_at=f"2026-05-01T{index % 24:02d}:00:00+00:00",
                            file_type="flac",
                            artist=f"Artist {index:02d}",
                            album_artist=f"Artist {index:02d}",
                            album=f"Album {index:02d}",
                            title=f"Track {index:02d}",
                        )
                        for index in range(22)
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-05-10T00:00:00+00:00",
                ),
                database,
            )
            with connect_database(database) as connection:
                for index in range(22):
                    connection.execute(
                        """
                        UPDATE library_albums
                        SET starred_at = ?
                        WHERE album = ?
                        """,
                        (
                            f"2026-05-{index + 1:02d}T12:00:00Z",
                            f"Album {index:02d}",
                        ),
                    )
            dashboard = home_dashboard(database)
            runtime = self.make_runtime(database)
            with patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime):
                app = create_player_app(self.make_options(temp_path))
                response = app.test_client().get("/")

        html = response.data.decode()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(dashboard.recently_starred_albums), 14)
        self.assertLess(html.index("Added in the Last Month"), html.index("Recently Favorited"))
        self.assertIn('href="/albums?sort=starred" data-nav>Starred</a>', html)
        starred_section = html.split("Recently Favorited", 1)[1].split("Recent Artists", 1)[0]
        self.assertIn("Album 21", starred_section)
        self.assertIn("Album 08", starred_section)
        self.assertIn("Favorited 2026-05-22", starred_section)
        self.assertNotIn("Album 07", starred_section)
        self.assertNotIn("Album 00", starred_section)

    def test_home_continue_listening_uses_now_playing_scrobble_when_queue_loaded(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            track = TrackRecord(
                path="/music/Bill Evans/Undercurrent/01.flac",
                root_position=0,
                file_type="flac",
                artist="Bill Evans",
                album_artist="Bill Evans and Jim Hall",
                album="Undercurrent",
                title="My Funny Valentine",
                track_number="1",
            )
            save_library(
                MusicLibrary(
                    roots=["/music"],
                    tracks=[track],
                    supported_extensions=[".flac"],
                    generated_at="2026-05-11T00:00:00+00:00",
                ),
                database,
            )
            record_playback(
                database,
                track.track_id or 1,
                submission=False,
                played_at=datetime(2026, 5, 11, 13, 30, tzinfo=UTC),
                source=NATIVE_PLAYBACK_SOURCE,
            )
            runtime = self.make_runtime(database)
            runtime.queue_state_copy.return_value = PlayerQueueState(
                track_ids=[track.track_id or 1],
                position=0,
                loaded_track_id=track.track_id or 1,
                paused=False,
            )
            with patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime):
                app = create_player_app(self.make_options(temp_path))
                response = app.test_client().get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Continue Listening", response.data)
        self.assertIn(b"My Funny Valentine", response.data)
        self.assertIn(b"Undercurrent", response.data)
        self.assertIn(b"Bill Evans and Jim Hall", response.data)
        self.assertIn(b"data-continue-play-toggle", response.data)
        self.assertIn(b"home-feature-pause-icon", response.data)
        self.assertIn(b"data-pause-icon hidden", response.data)
        self.assertIn(b'href="/queue" data-nav>Queue</a>', response.data)
        self.assertNotIn(b"No listening history yet", response.data)

    def test_home_omits_continue_listening_when_queue_is_empty(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            track = TrackRecord(
                path="/music/Artist/Album/01.flac",
                root_position=0,
                file_type="flac",
                artist="Artist",
                album_artist="Artist",
                album="Album",
                title="Track",
            )
            save_library(
                MusicLibrary(
                    roots=["/music"],
                    tracks=[track],
                    supported_extensions=[".flac"],
                    generated_at="2026-05-11T00:00:00+00:00",
                ),
                database,
            )
            record_playback(
                database,
                track.track_id or 1,
                submission=False,
                played_at=datetime(2026, 5, 11, 13, 30, tzinfo=UTC),
                source=NATIVE_PLAYBACK_SOURCE,
            )
            runtime = self.make_runtime(database)
            with patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime):
                app = create_player_app(self.make_options(temp_path))
                response = app.test_client().get("/")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"Continue Listening", response.data)
        self.assertIn(b"No listening history yet", response.data)

    def test_home_continue_listening_ignores_newer_external_now_playing(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            native_track = TrackRecord(
                path="/music/Native/Native Album/01.flac",
                root_position=0,
                file_type="flac",
                artist="Native Artist",
                album_artist="Native Artist",
                album="Native Album",
                title="Native Song",
                track_number="1",
            )
            external_track = TrackRecord(
                path="/music/External/External Album/01.flac",
                root_position=0,
                file_type="flac",
                artist="External Artist",
                album_artist="External Artist",
                album="External Album",
                title="External Song",
                track_number="1",
            )
            save_library(
                MusicLibrary(
                    roots=["/music"],
                    tracks=[native_track, external_track],
                    supported_extensions=[".flac"],
                    generated_at="2026-05-11T00:00:00+00:00",
                ),
                database,
            )
            record_playback(
                database,
                native_track.track_id or 1,
                submission=False,
                played_at=datetime(2026, 5, 11, 13, 30, tzinfo=UTC),
                source=NATIVE_PLAYBACK_SOURCE,
            )
            record_playback(
                database,
                external_track.track_id or 2,
                submission=False,
                played_at=datetime(2026, 5, 11, 13, 31, tzinfo=UTC),
                source="some-client",
            )
            runtime = self.make_runtime(database)
            runtime.queue_state_copy.return_value = PlayerQueueState(
                track_ids=[native_track.track_id or 1],
                position=0,
                loaded_track_id=native_track.track_id or 1,
                paused=False,
            )
            with patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime):
                app = create_player_app(self.make_options(temp_path))
                response = app.test_client().get("/")

        self.assertEqual(response.status_code, 200)
        html = response.data.decode()
        continue_section = html.split("Continue Listening", 1)[1].split(
            "Recently Added",
            1,
        )[0]
        self.assertIn("Native Song", continue_section)
        self.assertIn("Native Album", continue_section)
        self.assertNotIn("External Song", continue_section)
        self.assertNotIn("External Album", continue_section)

    def test_player_scrobble_endpoint_records_now_playing_and_submitted_plays(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            track = TrackRecord(
                path="/music/Brian Eno/Small Craft/01.flac",
                root_position=0,
                file_type="flac",
                artist="Brian Eno",
                album_artist="Brian Eno With Jon Hopkins & Leo Abrahams",
                album="Small Craft on a Milk Sea",
                title="Emerald and Lime",
                track_number="1/15",
                disc_number="1/1",
                genres=["Ambient", "Electronic"],
                styles=["Downtempo", "Minimal"],
            )
            save_library(
                MusicLibrary(
                    roots=["/music"],
                    tracks=[track],
                    supported_extensions=[".flac"],
                    generated_at="2026-05-01T00:00:00+00:00",
                ),
                database,
            )
            runtime = self.make_runtime(database)
            with patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime):
                app = create_player_app(self.make_options(temp_path))
                client = app.test_client()
                now_response = client.post(
                    "/api/scrobble",
                    json={
                        "playback_id": track.track_id,
                        "submission": "false",
                        "time": 1770000000000,
                    },
                )
                with connect_database(database, create=False) as connection:
                    now_row = connection.execute(
                        "SELECT playback_id, updated_at, source FROM play_now_playing"
                    ).fetchone()
                    submitted_count = int(
                        connection.execute(
                            "SELECT COUNT(*) AS count FROM play_track_stats"
                        ).fetchone()["count"]
                    )
                submit_response = client.post(
                    "/api/scrobble",
                    json={
                        "playback_id": track.track_id,
                        "submission": True,
                        "time": 1770000600000,
                    },
                )
                bad_response = client.post(
                    "/api/scrobble",
                    json={"playback_id": track.track_id, "submission": "maybe"},
                )

            expected_now = datetime.fromtimestamp(1770000000000 / 1000, tz=UTC).isoformat()
            expected_played = datetime.fromtimestamp(1770000600000 / 1000, tz=UTC).isoformat()
            with connect_database(database, create=False) as connection:
                track_stats = connection.execute(
                    """
                    SELECT play_count, last_played_at, title, album
                    FROM play_track_stats
                    WHERE track_id = ?
                    """,
                    (track.track_id,),
                ).fetchone()
                event_row = connection.execute(
                    "SELECT source, snapshot_json FROM play_events"
                ).fetchone()
                event_snapshot = json.loads(str(event_row["snapshot_json"]))
                album_stats = connection.execute(
                    "SELECT play_count, last_played_at FROM play_album_stats"
                ).fetchone()
                artist_stats = [
                    (str(row["artist"]), int(row["play_count"]))
                    for row in connection.execute(
                        """
                        SELECT artist, play_count
                        FROM play_artist_stats
                        ORDER BY artist
                        """
                    )
                ]
                genre_stats = [
                    (str(row["genre"]), int(row["play_count"]))
                    for row in connection.execute(
                        """
                        SELECT genre, play_count
                        FROM play_genre_stats
                        ORDER BY genre
                        """
                    )
                ]

        self.assertEqual(now_response.status_code, 200)
        self.assertEqual(int(now_row["playback_id"]), track.track_id)
        self.assertEqual(now_row["updated_at"], expected_now)
        self.assertEqual(now_row["source"], NATIVE_PLAYBACK_SOURCE)
        self.assertEqual(submitted_count, 0)
        self.assertEqual(submit_response.status_code, 200)
        self.assertEqual(bad_response.status_code, 400)
        self.assertEqual(bad_response.get_json(), {"error": "invalid scrobble submission"})
        self.assertEqual(int(track_stats["play_count"]), 1)
        self.assertEqual(track_stats["last_played_at"], expected_played)
        self.assertEqual(event_row["source"], NATIVE_PLAYBACK_SOURCE)
        self.assertEqual(event_snapshot["track_path"], track.path)
        self.assertNotIn("track_key", event_snapshot)
        self.assertEqual(event_snapshot["genres"], ["Ambient", "Electronic"])
        self.assertEqual(event_snapshot["styles"], ["Downtempo", "Minimal"])
        self.assertEqual(track_stats["title"], "Emerald and Lime")
        self.assertEqual(track_stats["album"], "Small Craft on a Milk Sea")
        self.assertEqual(int(album_stats["play_count"]), 1)
        self.assertEqual(album_stats["last_played_at"], expected_played)
        self.assertEqual(
            artist_stats,
            [("Brian Eno", 1), ("Jon Hopkins", 1), ("Leo Abrahams", 1)],
        )
        self.assertEqual(genre_stats, [("Ambient", 1), ("Electronic", 1)])

    def test_runtime_audio_and_scrobble_skip_schema_preparation(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            audio_path = temp_path / "track.mp3"
            audio_path.write_bytes(b"0123456789")
            track = TrackRecord(
                path=str(audio_path),
                root_position=0,
                file_type="mp3",
                artist="Brian Eno",
                album_artist="Brian Eno",
                album="Ambient 1",
                title="1/1",
                track_number="1/1",
                disc_number="1/1",
                duration_seconds=10,
            )
            save_library(
                MusicLibrary(
                    roots=[str(temp_path)],
                    tracks=[track],
                    supported_extensions=[".mp3"],
                    generated_at="2026-05-01T00:00:00+00:00",
                ),
                database,
            )
            runtime = self.make_runtime(database)
            with patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime):
                app = create_player_app(self.make_options(temp_path))

            with patch(
                "kukicha.use_case.database.migrate_player_jobs_schema",
                side_effect=AssertionError("schema preparation should run at startup only"),
            ):
                client = app.test_client()
                scrobble_response = client.post(
                    "/api/scrobble",
                    json={
                        "playback_id": track.track_id,
                        "submission": False,
                        "time": 1770000000000,
                    },
                )
                audio_response = client.get(f"/audio/{track.track_id}")

            self.assertEqual(scrobble_response.status_code, 200)
            self.assertEqual(audio_response.status_code, 200)
            self.assertEqual(audio_response.data, b"0123456789")

    def test_track_listening_stats_use_path_when_metadata_collides(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            first = TrackRecord(
                path="/music/Copy A/Album/01.flac",
                root_position=0,
                file_type="flac",
                artist="Artist",
                album_artist="Artist",
                album="Album",
                title="Same Song",
                track_number="1",
                disc_number="1",
            )
            second = TrackRecord(
                path="/music/Copy B/Album/01.flac",
                root_position=0,
                file_type="flac",
                artist="Artist",
                album_artist="Artist",
                album="Album",
                title="Same Song",
                track_number="1",
                disc_number="1",
            )
            save_library(
                MusicLibrary(
                    roots=["/music"],
                    tracks=[first, second],
                    supported_extensions=[".flac"],
                    generated_at="2026-05-01T00:00:00+00:00",
                ),
                database,
            )

            record_playback(
                database,
                first.track_id or 1,
                submission=True,
                played_at=datetime(2026, 5, 11, 12, 0, tzinfo=UTC),
                source="test",
            )
            record_playback(
                database,
                second.track_id or 2,
                submission=True,
                played_at=datetime(2026, 5, 11, 13, 0, tzinfo=UTC),
                source="test",
            )
            record_playback(
                database,
                first.track_id or 1,
                submission=True,
                played_at=datetime(2026, 5, 11, 14, 0, tzinfo=UTC),
                source="test",
            )
            dashboard = home_dashboard(database)
            with connect_database(database, create=False) as connection:
                stats = [
                    (
                        str(row["track_path"]),
                        int(row["play_count"]),
                        int(row["track_id"]),
                        str(row["album_id"]),
                    )
                    for row in connection.execute(
                        """
                        SELECT track_path, play_count, track_id, album_id
                        FROM play_track_stats
                        ORDER BY track_path
                        """
                    )
                ]

        self.assertEqual(
            stats,
            [
                (first.path, 2, first.track_id, "artist::album"),
                (second.path, 1, second.track_id, "artist::album"),
            ],
        )
        self.assertEqual(
            [(track.path, track.play_count) for track in dashboard.recent_tracks],
            [(first.path, 2), (second.path, 1)],
        )

    def test_submitted_play_and_delayed_now_playing_do_not_replace_current_now_playing(
        self,
    ) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            first = TrackRecord(
                path="/music/Artist/Album/01.flac",
                root_position=0,
                file_type="flac",
                artist="Artist",
                album_artist="Artist",
                album="Album",
                title="Current Song",
                track_number="1",
            )
            second = TrackRecord(
                path="/music/Artist/Album/02.flac",
                root_position=0,
                file_type="flac",
                artist="Artist",
                album_artist="Artist",
                album="Album",
                title="Prior Song",
                track_number="2",
            )
            save_library(
                MusicLibrary(
                    roots=["/music"],
                    tracks=[first, second],
                    supported_extensions=[".flac"],
                    generated_at="2026-05-01T00:00:00+00:00",
                ),
                database,
            )
            current_time = datetime(2026, 5, 11, 12, 0, tzinfo=UTC)
            delayed_time = datetime(2026, 5, 11, 11, 59, tzinfo=UTC)
            submitted_time = datetime(2026, 5, 11, 12, 1, tzinfo=UTC)
            newer_time = datetime(2026, 5, 11, 12, 2, tzinfo=UTC)

            record_playback(
                database,
                first.track_id or 1,
                submission=False,
                played_at=current_time,
                source=NATIVE_PLAYBACK_SOURCE,
            )
            record_playback(
                database,
                second.track_id or 2,
                submission=False,
                played_at=delayed_time,
                source=NATIVE_PLAYBACK_SOURCE,
            )
            record_playback(
                database,
                second.track_id or 2,
                submission=True,
                played_at=submitted_time,
                source=NATIVE_PLAYBACK_SOURCE,
            )
            with connect_database(database, create=False) as connection:
                stale_guard_row = connection.execute(
                    "SELECT playback_id, updated_at FROM play_now_playing"
                ).fetchone()
                submitted_stats = connection.execute(
                    """
                    SELECT play_count, last_played_at
                    FROM play_track_stats
                    WHERE track_id = ?
                    """,
                    (second.track_id,),
                ).fetchone()

            record_playback(
                database,
                second.track_id or 2,
                submission=False,
                played_at=newer_time,
                source=NATIVE_PLAYBACK_SOURCE,
            )
            with connect_database(database, create=False) as connection:
                newer_row = connection.execute(
                    "SELECT playback_id, updated_at FROM play_now_playing"
                ).fetchone()

        self.assertEqual(int(stale_guard_row["playback_id"]), first.track_id)
        self.assertEqual(stale_guard_row["updated_at"], current_time.isoformat())
        self.assertEqual(int(submitted_stats["play_count"]), 1)
        self.assertEqual(submitted_stats["last_played_at"], submitted_time.isoformat())
        self.assertEqual(int(newer_row["playback_id"]), second.track_id)
        self.assertEqual(newer_row["updated_at"], newer_time.isoformat())

    def test_home_shows_twelve_recent_items(
        self,
    ) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            tracks = [
                TrackRecord(
                    path=f"/music/Artist {index:02d}/Album {index:02d}/{index + 1:02d}.flac",
                    root_position=0,
                    file_type="flac",
                    artist=f"Track Artist {index:02d}",
                    album_artist=f"Artist {index:02d}",
                    album=f"Album {index:02d}",
                    title=f"Song {index:02d}",
                    track_number=str(index + 1),
                    genres=[f"Genre {index:02d}"],
                )
                for index in range(22)
            ]
            playlist_paths = [
                temp_path / "music" / f"mix-{index:02d}.m3u8"
                for index in range(13)
            ]
            save_library(
                MusicLibrary(
                    roots=["/music"],
                    tracks=tracks,
                    playlists=[
                        PlaylistRecord(
                            path=str(playlist_path),
                            root_position=0,
                            name=f"Mix {index:02d}",
                            items=[
                                PlaylistItemRecord(
                                    path=f"https://example.test/live/{index:02d}",
                                    title=f"Live Stream {index:02d}",
                                    genre=f"Stream Genre {index:02d}",
                                )
                            ],
                        )
                        for index, playlist_path in enumerate(playlist_paths)
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-05-01T00:00:00+00:00",
                ),
                database,
            )
            for index, track in enumerate(tracks):
                record_playback(
                    database,
                    track.track_id or index + 1,
                    submission=True,
                    played_at=datetime(2026, 5, 11, 12, index, tzinfo=UTC),
                    source="test",
                )
            with connect_database(database, create=False) as connection:
                playlist_item_ids = [
                    int(row["playlist_item_id"])
                    for row in connection.execute(
                        """
                        SELECT playlist_item_id
                        FROM library_playlist_items
                        ORDER BY playlist_id
                        """
                    )
                ]
            for index, playlist_item_id in enumerate(playlist_item_ids):
                record_playback(
                    database,
                    -playlist_item_id,
                    submission=True,
                    played_at=datetime(2026, 5, 11, 13, index, tzinfo=UTC),
                    source="test",
                )
            dashboard = home_dashboard(database)
            runtime = self.make_runtime(database)
            with patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime):
                app = create_player_app(self.make_options(temp_path))
                response = app.test_client().get("/")

        html = response.data.decode()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(dashboard.recent_albums), 14)
        self.assertEqual(len(dashboard.recent_playlists), 12)
        self.assertEqual(len(dashboard.recent_artists), 14)
        self.assertEqual(len(dashboard.recent_genres), 6)
        self.assertEqual(len(dashboard.recent_tracks), 12)
        self.assertIsNone(dashboard.recent_tracks[0].art_track_id)
        self.assertIn('href="/albums?sort=recent" data-nav>Recent</a>', html)
        self.assertNotIn(">All albums</a>", html)
        self.assertLess(html.index("Recent Artists"), html.index("Recent Tracks"))
        self.assertIn("Played 2026-05-11", html)
        artist_section = html.split("Recent Artists", 1)[1].split("Recent Tracks", 1)[0]
        self.assertIn("Artist 21", artist_section)
        self.assertIn("Played 2026-05-11", artist_section)
        self.assertNotIn(">1</small>", artist_section)
        self.assertNotIn("Recent Genres", html)
        self.assertLess(html.index("Recent Tracks"), html.index("Recent Playlists"))
        playlist_section = html.split("Recent Playlists", 1)[1]
        self.assertIn("Played 2026-05-11", playlist_section)
        self.assertNotIn("play - 2026-05-11", playlist_section)
        self.assertIn('class="home-track-cover playlist-cover-image"', playlist_section)
        track_section = html.split("Recent Tracks", 1)[1].split("Recent Playlists", 1)[0]
        self.assertIn("Track Artist 21 - Album 21 - ", track_section)
        self.assertIn(
            '<time data-local-date-prefix="Played" datetime="2026-05-11T12:21:00+00:00">Played 2026-05-11</time>',
            track_section,
        )
        self.assertNotIn("play - 2026-05-11", track_section)
        self.assertIn(
            f'href="/albums/{dashboard.recent_tracks[0].album_id}?selectedTrackId={dashboard.recent_tracks[0].track_id}" data-nav',
            track_section,
        )
        self.assertIn('class="home-track-cover album-cover-placeholder"', track_section)
        self.assertNotIn(f'src="/art/250/{tracks[-1].track_id}"', track_section)

    def test_home_and_album_pages_skip_deleted_album_listening_stats(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            old_track = TrackRecord(
                path="/music/Yoshihiro Kanno/Deleted Album/01.flac",
                root_position=0,
                file_type="flac",
                artist="Yoshihiro Kanno",
                album_artist="Yoshihiro Kanno",
                album="Deleted Album",
                title="Water",
                track_number="1",
                genres=["Stale Genre"],
            )
            save_library(
                MusicLibrary(
                    roots=["/music"],
                    tracks=[old_track],
                    supported_extensions=[".flac"],
                    generated_at="2026-05-01T00:00:00+00:00",
                ),
                database,
            )
            record_playback(
                database,
                old_track.track_id or 1,
                submission=True,
                played_at=datetime(2026, 5, 11, 12, 0, tzinfo=UTC),
                source="test",
            )
            with connect_database(database, create=False) as connection:
                old_album_id = str(
                    connection.execute(
                        """
                        SELECT album_id
                        FROM library_albums
                        WHERE album = 'Deleted Album'
                        """
                    ).fetchone()["album_id"]
                )

            new_track = TrackRecord(
                path="/music/Yoshihiro Kanno/Current Album/01.flac",
                root_position=0,
                file_type="flac",
                artist="Yoshihiro Kanno",
                album_artist="Yoshihiro Kanno",
                album="Current Album",
                title="Water",
                track_number="1",
                genres=["Current Genre"],
            )
            save_library(
                MusicLibrary(
                    roots=["/music"],
                    tracks=[new_track],
                    supported_extensions=[".flac"],
                    generated_at="2026-05-02T00:00:00+00:00",
                ),
                database,
            )
            dashboard = home_dashboard(database)
            api = LibraryQueries(database)
            recent = api.list_album_page(AlbumListQuery(sort=ALBUM_LIST_SORT_RECENT))
            frequent = api.list_album_page(AlbumListQuery(sort=ALBUM_LIST_SORT_FREQUENT))
            with connect_database(database, create=False) as connection:
                stale_album_stat_count = int(
                    connection.execute(
                        """
                        SELECT COUNT(*) AS count
                        FROM play_album_stats
                        WHERE album_id = ?
                        """,
                        (old_album_id,),
                    ).fetchone()["count"]
                )
                stale_track_stat_count = int(
                    connection.execute(
                        """
                        SELECT COUNT(*) AS count
                        FROM play_track_stats
                        WHERE album_id = ?
                        """,
                        (old_album_id,),
                    ).fetchone()["count"]
                )
            runtime = self.make_runtime(database)
            with patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime):
                app = create_player_app(self.make_options(temp_path))
                response = app.test_client().get("/")

        html = response.data.decode()
        self.assertEqual(stale_album_stat_count, 1)
        self.assertEqual(stale_track_stat_count, 1)
        self.assertEqual(dashboard.recent_albums, ())
        self.assertEqual(dashboard.recent_tracks, ())
        self.assertEqual(dashboard.recent_artists, ())
        self.assertEqual(dashboard.recent_genres, ())
        self.assertEqual(recent.items, ())
        self.assertEqual(frequent.items, ())
        self.assertNotIn("Deleted Album", html)
        self.assertNotIn("Recently Listened Albums", html)
        self.assertNotIn("Recent Tracks", html)
        self.assertNotIn("Recent Artists", html)
        self.assertIn("No listening history yet", html)

    def test_playlist_play_history_hides_track_stats_for_missing_paths(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            root = temp_path / "music"
            playlist_path = root / "mix.m3u8"
            first_path = root / "Artist" / "Album" / "01.flac"
            save_library(
                MusicLibrary(
                    roots=[str(root)],
                    tracks=[
                        TrackRecord(
                            path=str(first_path),
                            root_position=0,
                            file_type="flac",
                            artist="Artist",
                            album_artist="Artist",
                            album="Album",
                            title="Stable Song",
                            track_number="1",
                            genres=["Jazz"],
                        )
                    ],
                    playlists=[
                        PlaylistRecord(
                            path=str(playlist_path),
                            root_position=0,
                            name="Mix",
                            items=[
                                PlaylistItemRecord(path=str(first_path)),
                                PlaylistItemRecord(
                                    path="https://example.test/live",
                                    title="Live Stream",
                                    genre="Downtempo",
                                ),
                            ],
                        )
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-05-01T00:00:00+00:00",
                ),
                database,
            )
            with connect_database(database, create=False) as connection:
                tracked_item_id = int(
                    connection.execute(
                        """
                        SELECT playlist_item_id
                        FROM library_playlist_items
                        WHERE track_id IS NOT NULL
                        """
                    ).fetchone()["playlist_item_id"]
                )
                external_item_id = int(
                    connection.execute(
                        """
                        SELECT playlist_item_id
                        FROM library_playlist_items
                        WHERE track_id IS NULL
                        """
                    ).fetchone()["playlist_item_id"]
                )

            record_playback(
                database,
                -tracked_item_id,
                submission=True,
                played_at=datetime(2026, 5, 1, 12, 0, tzinfo=UTC),
                source="test",
            )
            record_playback(
                database,
                -external_item_id,
                submission=True,
                played_at=datetime(2026, 5, 1, 12, 5, tzinfo=UTC),
                source="test",
            )

            rescanned_path = root / "Moved" / "Album" / "01.flac"
            save_library(
                MusicLibrary(
                    roots=[str(root)],
                    tracks=[
                        TrackRecord(
                            path=str(rescanned_path),
                            root_position=0,
                            file_type="flac",
                            artist="Artist",
                            album_artist="Artist",
                            album="Album",
                            title="Stable Song",
                            track_number="1",
                            genres=["Jazz"],
                        )
                    ],
                    playlists=[
                        PlaylistRecord(
                            path=str(playlist_path),
                            root_position=0,
                            playlist_id=1,
                            name="Mix",
                            items=[PlaylistItemRecord(path=str(rescanned_path))],
                        )
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-05-02T00:00:00+00:00",
                ),
                database,
            )
            dashboard = home_dashboard(database)
            with connect_database(database, create=False) as connection:
                playlist_stats = connection.execute(
                    "SELECT play_count, path FROM play_playlist_stats"
                ).fetchone()
                track_stats = connection.execute(
                    "SELECT track_path, play_count FROM play_track_stats"
                ).fetchone()
                genre_stats = [
                    (str(row["genre"]), int(row["play_count"]))
                    for row in connection.execute(
                        "SELECT genre, play_count FROM play_genre_stats ORDER BY genre"
                    )
                ]

        self.assertEqual(int(playlist_stats["play_count"]), 2)
        self.assertEqual(playlist_stats["path"], "")
        self.assertEqual(track_stats["track_path"], str(first_path))
        self.assertEqual(int(track_stats["play_count"]), 1)
        self.assertEqual(genre_stats, [("Downtempo", 1), ("Jazz", 1)])
        self.assertEqual(dashboard.recent_playlists[0].playlist.playlist_id, 1)
        self.assertEqual(dashboard.recent_albums[0].album.album, "Album")
        self.assertEqual(dashboard.recent_albums[0].play_count, 1)
        self.assertEqual(dashboard.recent_tracks, ())
        self.assertEqual(
            [(artist.name, artist.play_count) for artist in dashboard.recent_artists],
            [("Artist", 1)],
        )
        self.assertIn(("Jazz", 1), [(genre.name, genre.play_count) for genre in dashboard.recent_genres])

    def test_stream_now_playing_scrobble_counts_as_played_on_play(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            playlist_path = temp_path / "music" / "streams.m3u8"
            save_library(
                MusicLibrary(
                    roots=[str(temp_path / "music")],
                    tracks=[],
                    playlists=[
                        PlaylistRecord(
                            path=str(playlist_path),
                            root_position=0,
                            name="Streams",
                            items=[
                                PlaylistItemRecord(
                                    path="https://example.test/live",
                                    title="Live Stream",
                                    genre="Ambient",
                                    duration_is_indeterminate=True,
                                )
                            ],
                        )
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-05-01T00:00:00+00:00",
                ),
                database,
            )
            with connect_database(database, create=False) as connection:
                playlist_item_id = int(
                    connection.execute(
                        "SELECT playlist_item_id FROM library_playlist_items"
                    ).fetchone()["playlist_item_id"]
                )

            played_at = datetime(2026, 5, 11, 12, 0, tzinfo=UTC)
            record_playback(
                database,
                -playlist_item_id,
                submission=False,
                played_at=played_at,
                source=NATIVE_PLAYBACK_SOURCE,
            )

            with connect_database(database, create=False) as connection:
                now_playing = connection.execute(
                    """
                    SELECT playback_id, updated_at, source
                    FROM play_now_playing
                    """
                ).fetchone()
                event_count = int(
                    connection.execute(
                        "SELECT COUNT(*) AS count FROM play_events"
                    ).fetchone()["count"]
                )
                track_stats_count = int(
                    connection.execute(
                        "SELECT COUNT(*) AS count FROM play_track_stats"
                    ).fetchone()["count"]
                )
                playlist_stats = connection.execute(
                    """
                    SELECT play_count, last_played_at, path
                    FROM play_playlist_stats
                    """
                ).fetchone()
                genre_stats = connection.execute(
                    """
                    SELECT genre, play_count, last_played_at
                    FROM play_genre_stats
                    """
                ).fetchone()

        self.assertEqual(int(now_playing["playback_id"]), -playlist_item_id)
        self.assertEqual(now_playing["updated_at"], played_at.isoformat())
        self.assertEqual(now_playing["source"], NATIVE_PLAYBACK_SOURCE)
        self.assertEqual(event_count, 1)
        self.assertEqual(track_stats_count, 0)
        self.assertEqual(int(playlist_stats["play_count"]), 1)
        self.assertEqual(playlist_stats["last_played_at"], played_at.isoformat())
        self.assertEqual(playlist_stats["path"], "")
        self.assertEqual(genre_stats["genre"], "Ambient")
        self.assertEqual(int(genre_stats["play_count"]), 1)
        self.assertEqual(genre_stats["last_played_at"], played_at.isoformat())

    def test_home_continue_listening_uses_playlist_stream_now_playing(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            playlist_path = temp_path / "music" / "streams.m3u8"
            save_library(
                MusicLibrary(
                    roots=[str(temp_path / "music")],
                    tracks=[],
                    playlists=[
                        PlaylistRecord(
                            path=str(playlist_path),
                            root_position=0,
                            name="Streams",
                            items=[
                                PlaylistItemRecord(
                                    path="https://example.test/live",
                                    title="Live Stream",
                                    genre="Ambient",
                                    duration_is_indeterminate=True,
                                )
                            ],
                        )
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-05-01T00:00:00+00:00",
                ),
                database,
            )
            with connect_database(database, create=False) as connection:
                row = connection.execute(
                    """
                    SELECT items.playlist_item_id, playlists.playlist_id
                    FROM library_playlist_items AS items
                    JOIN library_playlists AS playlists
                        ON playlists.playlist_id = items.playlist_id
                    """
                ).fetchone()
                playlist_item_id = int(row["playlist_item_id"])
                playlist_id = int(row["playlist_id"])

            playback_id = -playlist_item_id
            record_playback(
                database,
                playback_id,
                submission=True,
                played_at=datetime(2026, 5, 11, 12, 0, tzinfo=UTC),
                source=NATIVE_PLAYBACK_SOURCE,
            )
            dashboard = home_dashboard(database)
            runtime = self.make_runtime(database)
            runtime.queue_state_copy.return_value = PlayerQueueState(
                track_ids=[playback_id],
                position=0,
                loaded_track_id=playback_id,
                paused=False,
            )
            with patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime):
                app = create_player_app(self.make_options(temp_path))
                response = app.test_client().get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(dashboard.now_playing)
        self.assertEqual(dashboard.now_playing.album.album, "Streams")
        self.assertEqual(dashboard.now_playing.track_title, "Live Stream")
        self.assertTrue(dashboard.now_playing.album.is_playlist)
        self.assertEqual(dashboard.now_playing.album.playlist_id, playlist_id)
        self.assertIn(b"Continue Listening", response.data)
        self.assertIn(b"Live Stream", response.data)
        self.assertIn(b"Streams", response.data)
        self.assertIn(f'href="/playlists/{playlist_id}"'.encode(), response.data)
        self.assertNotIn(b"No listening history yet", response.data)

    def test_create_player_app_preloads_library_filter_options_cache(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            with patch.object(
                PlayerRuntime,
                "library_filter_options",
                autospec=True,
                return_value=LibraryFilterOptions(),
            ) as preload:
                app = create_player_app(self.make_options(temp_path))

        self.assertIsNotNone(app)
        preload.assert_called_once()

    def test_bulk_metadata_edit_route_queues_bulk_job(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            runtime = self.make_runtime(temp_path / "kukicha.sqlite")
            with patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime):
                app = create_player_app(self.make_options(temp_path))

            with patch(
                "kukicha.player_web_adapter.start_bulk_album_metadata_edit",
                return_value={
                    "message": "Bulk metadata URL edit queued.",
                    "job": {"job_id": 22},
                },
            ) as start_bulk:
                response = app.test_client().post(
                    "/api/albums/metadata-urls/edit",
                    json={
                        "rows": [
                            {
                                "album_id": "old-artist::album",
                                "metadata_url": "",
                                "track_ids": [1],
                            }
                        ]
                    },
                )

            self.assertEqual(response.status_code, 202)
            self.assertEqual(
                response.get_json(),
                {
                    "message": "Bulk metadata URL edit queued.",
                    "job": {"job_id": 22},
                },
            )
            start_bulk.assert_called_once()
            self.assertIs(start_bulk.call_args.args[0], runtime)
            self.assertEqual(
                start_bulk.call_args.args[1],
                {
                    "rows": [
                        {
                            "album_id": "old-artist::album",
                            "metadata_url": "",
                            "track_ids": [1],
                        }
                    ]
                },
            )

    def test_start_player_sync_queues_config_root_sync(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            runtime = self.make_runtime(temp_path / "kukicha.sqlite")
            options = PlayerServerOptions(
                config_path=temp_path / "kukicha.toml",
                database=temp_path / "kukicha.sqlite",
                ffmpeg_path=None,
                roots=(temp_path / "music",),
            )
            with patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime):
                app = create_player_app(options)

            runtime.enqueue_job.assert_not_called()
            start_player_sync(app)

            runtime.enqueue_job.assert_called_once()
            kwargs = runtime.enqueue_job.call_args.kwargs
            self.assertEqual(kwargs["kind"], "sync")
            self.assertEqual(kwargs["context"], {"roots_configured": 1})
            self.assertEqual(kwargs["queued_message"], "Sync queued.")

    def test_serve_player_rejects_nested_roots_before_binding_server(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            options = PlayerServerOptions(
                config_path=temp_path / "kukicha.toml",
                database=database,
                ffmpeg_path=None,
                roots=(temp_path / "music", temp_path / "music" / "live"),
            )

            with (
                patch("kukicha.player_web_adapter.make_server") as make_server,
                patch("kukicha.player_web_adapter.LOGGER") as logger,
            ):
                result = serve_player(options)

            self.assertEqual(result, 1)
            make_server.assert_not_called()
            logger.error.assert_called_once()
            self.assertFalse(database.exists())

    def test_serve_player_logs_open_subsonic_mount(self) -> None:
        for mount_prefix, expected_url in (
            ("/", "http://127.0.0.1:4567/"),
            ("/sonic", "http://127.0.0.1:4567/sonic"),
        ):
            with self.subTest(mount_prefix=mount_prefix):
                with TemporaryDirectory() as tempdir:
                    temp_path = Path(tempdir)
                    secret_file = temp_path / "opensubsonic.secret"
                    secret_file.write_text("sonic-secret\n", encoding="utf-8")
                    secret_file.chmod(0o600)
                    runtime = self.make_runtime(temp_path / "kukicha.sqlite")
                    options = replace(
                        self.make_auth_options(temp_path),
                        opensubsonic=OpenSubsonicOptions(
                            mount_prefix=mount_prefix,
                            secret_file=secret_file,
                        ),
                    )
                    server = Mock()
                    server.server_port = 4567
                    server.serve_forever.side_effect = KeyboardInterrupt

                    with (
                        patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime),
                        patch("kukicha.player_web_adapter.make_server", return_value=server),
                        patch("kukicha.player_web_adapter.LOGGER") as logger,
                        patch(
                            "kukicha.player_web_adapter.register_player_signal_handlers",
                            return_value={},
                        ),
                        patch("kukicha.player_web_adapter.restore_signal_handlers"),
                    ):
                        result = serve_player(options)

                    self.assertEqual(result, 0)
                    logger.info.assert_any_call(
                        "OpenSubsonic server URL for clients: %s (mount prefix %s)",
                        expected_url,
                        mount_prefix,
                    )
                    server.server_close.assert_called_once()

    def test_serve_player_logs_resolved_remote_workers(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            runtime = self.make_runtime(temp_path / "kukicha.sqlite")
            options = replace(self.make_options(temp_path), remote_workers=None)
            server = Mock()
            server.server_port = 4567
            server.serve_forever.side_effect = KeyboardInterrupt

            with (
                patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime),
                patch("kukicha.player_web_adapter.make_server", return_value=server),
                patch("kukicha.player_web_adapter.LOGGER") as logger,
                patch("kukicha.player_web_adapter.resolve_remote_worker_count", return_value=9),
                patch(
                    "kukicha.player_web_adapter.register_player_signal_handlers",
                    return_value={},
                ),
                patch("kukicha.player_web_adapter.restore_signal_handlers"),
            ):
                result = serve_player(options)

        self.assertEqual(result, 0)
        logger.info.assert_any_call("remote workers: %s (%s)", 9, "auto")
        server.server_close.assert_called_once()

    def test_removed_placeholder_routes_return_not_found(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            runtime = self.make_runtime(temp_path / "kukicha.sqlite")
            with patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime):
                app = create_player_app(self.make_options(temp_path))
                client = app.test_client()

            self.assertEqual(client.get("/notifications").status_code, 404)
            self.assertEqual(client.get("/logs").status_code, 404)
            self.assertEqual(client.get("/settings").status_code, 302)
            self.assertEqual(client.get("/settings").headers["Location"], "/roots")

            response = client.get("/notifications")
            self.assertIn(b"<h1>Not Found</h1>", response.data)
            self.assertIn(b"page not found: /notifications", response.data)
            self.assertIn(b'href="/albums" data-nav>Albums</a>', response.data)
            self.assertIn(b'href="/playlists" data-nav>Playlists</a>', response.data)
            self.assertNotIn(b'class="filter-menu page-menu"', response.data)
            self.assertNotIn(b'href="/roots"', response.data)
            self.assertNotIn(b'href="/jobs"', response.data)

    def test_missing_album_page_returns_not_found_response(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            runtime = self.make_runtime(temp_path / "kukicha.sqlite")
            with (
                patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime),
                patch(
                    "kukicha.player_web_adapter.build_album_context",
                    side_effect=AlbumNotFoundError("missing-album"),
                ),
            ):
                app = create_player_app(self.make_options(temp_path))

            response = app.test_client().get("/albums/missing-album")

            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.content_type, "text/html; charset=utf-8")
            self.assertEqual(response.headers["Cache-Control"], HTML_CACHE_CONTROL)
            self.assertIn(b"<!doctype html>", response.data)
            self.assertIn(b"<h1>Not Found</h1>", response.data)
            self.assertIn(b"album not found: missing-album", response.data)
            self.assertIn(b'href="/artists" data-nav>Artists</a>', response.data)
            self.assertIn(b'href="/queue" data-nav>Queue</a>', response.data)

    def test_missing_album_playback_returns_json_not_found_response(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            runtime = self.make_runtime(temp_path / "kukicha.sqlite")
            with (
                patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime),
                patch(
                    "kukicha.player_web_adapter.album_playback_payload",
                    side_effect=AlbumNotFoundError("missing-album"),
                ),
            ):
                app = create_player_app(self.make_options(temp_path))

            response = app.test_client().get("/api/albums/missing-album/playback")

            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.get_json(), {"error": "album not found: missing-album"})

    def test_track_recommendation_route_returns_json_shape(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            self.seed_recommendation_database(database)
            with connect_database(database, create=False) as connection:
                connection.execute(
                    "UPDATE library_tracks SET track_number = ? WHERE track_id = ?",
                    ("9", 2),
                )

            app = create_player_app(self.make_options(temp_path))

            response = app.test_client().get(
                "/recommendations/radio/track/1",
                query_string={"limit": "1"},
            )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content_type, "application/json; charset=utf-8")
            payload = response.get_json()
            self.assertEqual(payload["type"], "track_radio")
            self.assertEqual(payload["mode"], "default")
            self.assertEqual(payload["limit"], 1)
            self.assertEqual(payload["count"], 1)
            self.assertEqual(len(payload["results"]), 1)

            result = payload["results"][0]
            self.assertEqual(result["rank"], 1)
            self.assertEqual(result["track"]["track_id"], 2)
            self.assertEqual(result["track"]["title"], "Closest Song")
            self.assertEqual(result["track"]["artist"], "Other Artist")
            self.assertEqual(result["track"]["album_id"], "album-match")
            self.assertEqual(result["track"]["genres"], ["Rock"])
            self.assertEqual(result["track"]["styles"], ["Dream Pop"])
            self.assertFalse(result["track"]["is_favorite"])
            self.assertEqual(result["listening"]["track_play_count"], 7)
            self.assertIn("base_similarity", result["score"])
            self.assertIn("final_score", result["score"])
            self.assertEqual(result["score"], result["explanation"]["score"])
            self.assertEqual(result["explanation"]["matched_genres"], ["Rock"])
            self.assertEqual(result["explanation"]["matched_styles"], ["Dream Pop"])
            self.assertEqual(result["explanation"]["matched_decade"], "1990s")
            self.assertFalse(result["explanation"]["same_artist"])

    def test_recommendation_route_renders_playable_html_for_browser_request(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            self.seed_recommendation_database(database)

            app = create_player_app(self.make_options(temp_path))

            response = app.test_client().get(
                "/recommendations/radio/track/1",
                query_string={"limit": "1"},
                headers={"Accept": "text/html"},
            )

            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.content_type.startswith("text/html"))
            html = response.get_data(as_text=True)
            self.assertIn('<div class="view-page recommendations-page"', html)
            self.assertLess(
                html.index('href="/" data-history-back data-nav>&larr; back</a>'),
                html.index("<h1>Track Radio</h1>"),
            )
            self.assertIn("<h1>Track Radio</h1>", html)
            self.assertIn("Seed Artist - Seed Song", html)
            self.assertIn(
                'class="button-link recommendation-mode-link current" href="/recommendations/radio/track/1?limit=1"',
                html,
            )
            self.assertNotIn("recommendation-mode-link primary", html)
            self.assertIn('href="/recommendations/radio/track/1?limit=1" data-nav', html)
            self.assertIn(
                'href="/recommendations/radio/track/1?mode=discovery&amp;limit=1" data-nav',
                html,
            )
            self.assertNotIn("<th>Artist</th>", html)
            self.assertIn('data-track-id="2"', html)
            self.assertIn('<td class="track-number">1</td>', html)
            self.assertNotIn('<td class="track-number">9</td>', html)
            self.assertNotIn('data-play-track="2"', html)
            self.assertNotIn("track-action-menu", html)
            self.assertIn("queue-add-icon", html)
            self.assertIn("data-queue-track data-queue-append", html)

    def test_album_artist_and_daily_recommendation_routes_return_json(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            self.seed_recommendation_database(database)

            app = create_player_app(self.make_options(temp_path))
            client = app.test_client()

            album_response = client.get(
                "/recommendations/radio/album/album-seed",
                query_string={"mode": "genre_only", "limit": "1"},
            )
            artist_response = client.get(
                "/recommendations/radio/artist/Seed%20Artist",
                query_string={"mode": "artist_only", "limit": "1"},
            )
            daily_response = client.get(
                "/recommendations/daily",
                query_string={"date": "2026-06-07", "limit": "1"},
            )

            album_payload = album_response.get_json()
            self.assertEqual(album_response.status_code, 200)
            self.assertEqual(album_payload["type"], "album_radio")
            self.assertEqual(album_payload["mode"], "genre_only")
            self.assertEqual(album_payload["limit"], 1)
            self.assertNotEqual(
                album_payload["results"][0]["track"]["album_id"],
                "album-seed",
            )

            artist_payload = artist_response.get_json()
            self.assertEqual(artist_response.status_code, 200)
            self.assertEqual(artist_payload["type"], "artist_radio")
            self.assertEqual(artist_payload["mode"], "artist_only")
            self.assertEqual(artist_payload["limit"], 1)
            self.assertEqual(
                artist_payload["results"][0]["track"]["artist"],
                "Seed Artist",
            )

            daily_payload = daily_response.get_json()
            self.assertEqual(daily_response.status_code, 200)
            self.assertEqual(daily_payload["type"], "daily")
            self.assertEqual(daily_payload["mode"], "default")
            self.assertEqual(daily_payload["limit"], 1)
            self.assertEqual(daily_payload["date"], "2026-06-07")
            self.assertEqual(daily_payload["count"], 1)
            self.assertEqual(daily_payload["results"][0]["rank"], 1)

    def test_recommendation_routes_return_json_errors(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            self.seed_recommendation_database(database)

            app = create_player_app(self.make_options(temp_path))
            client = app.test_client()

            invalid_mode_response = client.get(
                "/recommendations/radio/track/1",
                query_string={"mode": "ambient_only"},
            )
            missing_track_response = client.get("/recommendations/radio/track/404")
            missing_album_response = client.get(
                "/recommendations/radio/album/missing-album"
            )
            missing_artist_response = client.get(
                "/recommendations/radio/artist/Missing%20Artist"
            )

            self.assertEqual(invalid_mode_response.status_code, 400)
            self.assertIn(
                "unsupported recommendation mode",
                invalid_mode_response.get_json()["error"],
            )
            self.assertEqual(missing_track_response.status_code, 404)
            self.assertEqual(
                missing_track_response.get_json(),
                {"error": "track not found: 404"},
            )
            self.assertEqual(missing_album_response.status_code, 404)
            self.assertEqual(
                missing_album_response.get_json(),
                {"error": "album not found: missing-album"},
            )
            self.assertEqual(missing_artist_response.status_code, 404)
            self.assertEqual(
                missing_artist_response.get_json(),
                {"error": "artist not found: Missing Artist"},
            )

    def test_recommendation_route_clamps_limit(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            self.seed_recommendation_database(database)

            app = create_player_app(self.make_options(temp_path))

            high_response = app.test_client().get(
                "/recommendations/radio/track/1",
                query_string={"limit": "9999"},
            )
            low_response = app.test_client().get(
                "/recommendations/radio/track/1",
                query_string={"limit": "0"},
            )

            self.assertEqual(high_response.status_code, 200)
            self.assertEqual(high_response.get_json()["limit"], MAX_RECOMMENDATION_LIMIT)
            self.assertEqual(low_response.status_code, 200)
            self.assertEqual(low_response.get_json()["limit"], 1)

    def test_cancel_job_route_returns_job_payload(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            runtime = self.make_runtime(temp_path / "kukicha.sqlite")
            runtime.cancel_job.return_value = PlayerJobRecord(
                job_id=9,
                created_at="2026-04-21T10:00:00Z",
                updated_at="2026-04-21T10:00:01Z",
                started_at=None,
                finished_at="2026-04-21T10:00:01Z",
                cancel_requested_at="2026-04-21T10:00:01Z",
                kind="rescan_library",
                status="canceled",
                message="Rescan canceled.",
                reason="Canceled by user.",
                context={},
            )
            with patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime):
                app = create_player_app(self.make_options(temp_path))

            response = app.test_client().post("/api/jobs/9/cancel")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()["job"]["status"], "canceled")
            runtime.cancel_job.assert_called_once_with(9)

    def test_rescan_library_route_queues_job(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            connection = connect_database(database)
            try:
                connection.execute(
                    "INSERT INTO library_roots (position, root_path) VALUES (?, ?)",
                    (0, "/music/a"),
                )
                connection.commit()
            finally:
                connection.close()

            runtime = self.make_runtime(database)
            runtime.enqueue_job.return_value = PlayerJobRecord(
                job_id=12,
                created_at="2026-04-21T10:00:00Z",
                updated_at="2026-04-21T10:00:00Z",
                started_at=None,
                finished_at=None,
                cancel_requested_at=None,
                kind="rescan_library",
                status="queued",
                message="Rescan queued.",
                reason="",
                context={"roots_scanned": 1},
            )
            with patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime):
                app = create_player_app(self.make_options(temp_path))

            response = app.test_client().post("/api/roots/rescan")

            self.assertEqual(response.status_code, 202)
            payload = response.get_json()
            self.assertEqual(payload["message"], "Rescan queued.")
            self.assertEqual(payload["job"]["kind"], "rescan_library")
            runtime.enqueue_job.assert_called_once()
            self.assertEqual(runtime.enqueue_job.call_args.kwargs["kind"], "rescan_library")
            self.assertEqual(
                runtime.enqueue_job.call_args.kwargs["context"],
                {"roots_scanned": 1},
            )

    def test_rescan_library_route_rejects_empty_root_set(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            connection = connect_database(database)
            connection.close()

            runtime = self.make_runtime(database)
            with patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime):
                app = create_player_app(self.make_options(temp_path))

            response = app.test_client().post("/api/roots/rescan")

            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.get_json(), {"error": "no roots configured"})
            runtime.enqueue_job.assert_not_called()

    def test_per_root_rescan_route_is_removed(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            runtime = self.make_runtime(temp_path / "kukicha.sqlite")
            with patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime):
                app = create_player_app(self.make_options(temp_path))

            response = app.test_client().post("/api/roots/0/rescan")

            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.get_json(), {"error": "page not found: /api/roots/0/rescan"})
            runtime.enqueue_job.assert_not_called()

    def test_add_and_delete_root_routes_are_removed(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            runtime = self.make_runtime(temp_path / "kukicha.sqlite")
            with patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime):
                app = create_player_app(self.make_options(temp_path))
                client = app.test_client()

            add_response = client.post("/api/roots", json={"path": "/music/a"})
            delete_response = client.post("/api/roots/0/delete")

            self.assertEqual(add_response.status_code, 404)
            self.assertEqual(delete_response.status_code, 404)
            runtime.enqueue_job.assert_not_called()

    def test_delete_album_route_queues_background_job(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            runtime = self.make_runtime(temp_path / "kukicha.sqlite")
            queued_job = PlayerJobRecord(
                job_id=15,
                created_at="2026-04-21T10:00:00Z",
                updated_at="2026-04-21T10:00:00Z",
                started_at=None,
                finished_at=None,
                cancel_requested_at=None,
                kind="delete_album",
                status="queued",
                message="Delete queued for Old Artist - Album.",
                reason="",
                context={"album": "Album", "tracks_deleted": 2},
            )
            with (
                patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime),
                patch(
                    "kukicha.player_web_adapter.start_album_delete",
                    return_value={
                        "message": "Delete queued for Old Artist - Album.",
                        "job": job_payload(queued_job),
                    },
                ) as start_delete,
            ):
                app = create_player_app(self.make_options(temp_path))
                response = app.test_client().post("/api/albums/old-artist::album/delete")

            self.assertEqual(response.status_code, 202)
            payload = response.get_json()
            self.assertEqual(payload["message"], "Delete queued for Old Artist - Album.")
            self.assertEqual(payload["job"]["kind"], "delete_album")
            start_delete.assert_called_once_with(runtime, "old-artist::album")

    def test_page_rendering_can_return_full_document_or_fragment(self) -> None:
        accent_theme = player_accent_theme("brown")
        appearance_theme = APPEARANCE_THEMES["light"]
        context = {
            "app_title": "kukicha",
            "queue_state": {},
            "queue_url": "/queue",
            "accent_theme": accent_theme,
            "appearance_theme": appearance_theme,
            "control_accent": derived_control_accent(accent_theme.accent, appearance_theme),
            "page_name": "library",
            "page_key": "library",
            "page_heading": "Albums",
            "page_menu_items": (),
            "count_text": "",
            "view_template": "player/simple_page.html",
            "toast_timeout_ms": 12000,
        }
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            runtime = self.make_runtime(temp_path / "kukicha.sqlite")
            with (
                patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime),
                patch("kukicha.player_web_adapter.build_index_context", return_value=context),
            ):
                app = create_player_app(self.make_options(temp_path))
                client = app.test_client()
                full_response = client.get("/albums")
                fragment_response = client.get("/albums", headers={"X-Kukicha-Fragment": "1"})

            self.assertEqual(full_response.status_code, 200)
            self.assertEqual(full_response.headers["Cache-Control"], HTML_CACHE_CONTROL)
            full_html = full_response.data.decode()
            self.assertIn(b"<!doctype html>", full_response.data)
            self.assertIn(b"<h1>Albums</h1>", full_response.data)
            self.assertIn(b'class="toast-region"', full_response.data)
            self.assertIn(b'data-toast-timeout-ms="12000"', full_response.data)
            self.assertNotIn(b"data-linked-toast-timeout-ms", full_response.data)
            self.assertIn(f'href="{static_asset_url("favicon.svg")}"', full_html)
            self.assertIn(f'href="{static_asset_url("player.css")}"', full_html)
            self.assertIn(f'src="{static_asset_url("player.js")}"', full_html)
            self.assertNotIn('href="/static/favicon.svg"', full_html)
            self.assertNotIn('href="/static/player.css"', full_html)
            self.assertNotIn('src="/static/player.js"', full_html)
            self.assertIn('id="confirmation-dialog"', full_html)
            self.assertIn('<h2 id="confirmation-title" data-confirmation-title>Confirm Action</h2>', full_html)
            self.assertIn('data-confirmation-cancel>Cancel</button>', full_html)
            self.assertIn('data-confirmation-confirm>Confirm</button>', full_html)
            self.assertIn('id="keyboard-shortcuts-dialog"', full_html)
            self.assertIn('<h2 id="keyboard-shortcuts-title">Keyboard Shortcuts</h2>', full_html)
            self.assertIn('class="keyboard-shortcuts-actions"', full_html)
            self.assertIn('data-close-keyboard-shortcuts>Close</button>', full_html)
            self.assertNotIn('data-close-keyboard-shortcuts>x</button>', full_html)
            self.assertIn("<kbd>K</kbd>", full_html)
            self.assertIn("<kbd>4</kbd>", full_html)
            self.assertIn("<kbd>Shift</kbd><span>+</span><kbd>R</kbd>", full_html)
            self.assertIn('id="playback-progress"', full_html)
            self.assertIn('id="previous" class="transport-step-button"', full_html)
            self.assertIn('id="next" class="transport-step-button"', full_html)
            self.assertIn("transport-step-icon", full_html)
            self.assertIn("M3.3 1a.7.7", full_html)
            self.assertIn("M12.7 1a.7.7", full_html)
            self.assertIn("data-play-icon", full_html)
            self.assertIn("M3 1.713a.7.7", full_html)
            self.assertIn("data-pause-icon", full_html)
            self.assertIn("M2.7 1a.7.7", full_html)
            self.assertIn('<span class="play-toggle-icon" data-pause-icon hidden>', full_html)
            self.assertIn('id="volume"', full_html)
            self.assertIn('id="volume-toggle"', full_html)
            self.assertIn('id="volume-icon"', full_html)
            self.assertNotIn('<span class="volume-label">Volume</span>', full_html)
            self.assertIn('<audio id="audio" preload="metadata"></audio>', full_html)
            self.assertNotIn('<audio id="audio" controls', full_html)
            buttons_html = full_html.split('<div class="buttons">', 1)[1].split("</div>", 1)[0]
            self.assertIn('id="queue-link"', buttons_html)
            self.assertIn('aria-label="Queue"', buttons_html)
            self.assertIn("queue-link-icon", buttons_html)
            self.assertNotIn(">Queue<", buttons_html)
            self.assertNotIn('id="play"', buttons_html)
            self.assertNotIn('id="previous"', buttons_html)
            self.assertNotIn('id="next"', buttons_html)
            self.assertEqual(fragment_response.status_code, 200)
            self.assertEqual(fragment_response.headers["Cache-Control"], HTML_CACHE_CONTROL)
            self.assertNotIn(b"<!doctype html>", fragment_response.data)
            self.assertNotIn(b'id="confirmation-dialog"', fragment_response.data)
            self.assertNotIn(b'id="keyboard-shortcuts-dialog"', fragment_response.data)
            self.assertIn(b"<h1>Albums</h1>", fragment_response.data)

    def test_page_rendering_uses_default_toast_timeout(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            runtime = self.make_runtime(temp_path / "kukicha.sqlite")
            context = base_player_context(
                runtime,
                page_name="library",
                page_key="library",
                page_heading="Albums",
                page_menu_items=(),
                count_text="",
                view_template="player/simple_page.html",
            )
            with (
                patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime),
                patch("kukicha.player_web_adapter.build_index_context", return_value=context),
            ):
                app = create_player_app(self.make_options(temp_path))
                response = app.test_client().get("/albums")

            self.assertEqual(response.status_code, 200)
            self.assertIn(b'data-toast-timeout-ms="5000"', response.data)
            self.assertNotIn(b'data-toast-timeout-ms="10000"', response.data)

    def test_artists_page_renders_tag_cloud_with_album_filter_links(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            save_library(
                MusicLibrary(
                    roots=["/music"],
                    tracks=[
                        TrackRecord(
                            path="/music/Ahmad Jamal/At the Pershing/01.flac",
                            root_position=0,
                            file_type="flac",
                            artist="Ahmad Jamal",
                            album_artist="Ahmad Jamal",
                            album="At the Pershing",
                            title="But Not For Me",
                        ),
                        TrackRecord(
                            path="/music/Ahmad Jamal/At the Pershing/02.flac",
                            root_position=0,
                            file_type="flac",
                            artist="Ahmad Jamal",
                            album_artist="Ahmad Jamal",
                            album="At the Pershing",
                            title="Surrey With the Fringe on Top",
                        ),
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-04-29T00:00:00+00:00",
                ),
                database,
            )
            runtime = self.make_runtime(database)
            with patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime):
                app = create_player_app(self.make_options(temp_path))
                client = app.test_client()
                full_response = client.get("/artists")
                fragment_response = client.get(
                    "/artists",
                    headers={"X-Kukicha-Fragment": "1"},
                )

            self.assertEqual(full_response.status_code, 200)
            self.assertIn(b"<h1>Artists</h1>", full_response.data)
            self.assertIn(b'class="artist-cloud"', full_response.data)
            self.assertIn(b'class="artist-cloud-link"', full_response.data)
            self.assertIn(b"Ahmad Jamal", full_response.data)
            self.assertIn(b'href="/albums?artist=Ahmad+Jamal"', full_response.data)
            self.assertNotIn(b"data-filter-form", full_response.data)
            self.assertNotIn(b'type="search"', full_response.data)
            self.assertEqual(fragment_response.status_code, 200)
            self.assertNotIn(b"<!doctype html>", fragment_response.data)
            self.assertIn(b"<h1>Artists</h1>", fragment_response.data)

    def test_album_index_renders_eager_genre_filters_without_artist_filter(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            save_library(
                MusicLibrary(
                    roots=["/music"],
                    tracks=[
                        TrackRecord(
                            path="/music/Artist A/Album/01.flac",
                            root_position=0,
                            file_type="flac",
                            artist="Artist A",
                            album_artist="Artist A",
                            album="Album",
                            title="One",
                            genres=["Electronic"],
                            styles=["Ambient"],
                        ),
                        TrackRecord(
                            path="/music/Artist B/Album/01.flac",
                            root_position=0,
                            file_type="flac",
                            artist="Artist B",
                            album_artist="Artist B",
                            album="Album",
                            title="Two",
                            genres=["Electronic"],
                            styles=["Techno"],
                        ),
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-04-29T00:00:00+00:00",
                ),
                database,
            )
            runtime = self.make_runtime(database)
            with patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime):
                app = create_player_app(self.make_options(temp_path))
                client = app.test_client()
                response = client.get(
                    "/albums?artist=Artist+A&genre[0][p]=Electronic"
                    "&genre[0][c][]=Ambient"
                )
                album_response = client.get(
                    "/albums/artist-a::album?artist=Artist+A&size=80"
                )
                artist_response = client.get("/albums?artist=Artist+A")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"data-lazy-filter-options", response.data)
        self.assertNotIn(b'data-filter-summary="artists"', response.data)
        self.assertIn(b'type="hidden" name="artist" value="Artist A"', response.data)
        self.assertIn(
            b'class="readonly-filter artist-filter-readonly"',
            response.data,
        )
        self.assertIn(
            b'aria-label="Artist filter: Artist A"',
            response.data,
        )
        self.assertIn(
            b'<span class="readonly-filter-value">Artist A</span>',
            response.data,
        )
        self.assertIn(
            b'href="/albums/artist-a::album" data-nav data-album-nav',
            response.data,
        )
        self.assertNotIn(b"/albums/artist-a::album?", response.data)
        self.assertIn(
            b'<span class="album-artist" title="Artist A">',
            response.data,
        )
        self.assertIn(
            b'class="album-artist-link" href="/albums?artist=Artist+A&amp;genre[0][p]=Electronic&amp;genre[0][c][]=Ambient" data-nav>Artist A</a>',
            response.data,
        )
        self.assertIn(b'value="Ambient" data-genre-child-control checked', response.data)
        self.assertIn(b'value="Techno" data-genre-child-control', response.data)
        self.assertNotIn(
            b'value="Electronic" data-genre-parent-param disabled',
            response.data,
        )
        self.assertEqual(artist_response.status_code, 200)
        self.assertIn(b"Artist A", artist_response.data)
        self.assertNotIn(b"Artist B", artist_response.data)
        self.assertEqual(album_response.status_code, 200)
        self.assertIn(b'href="/albums" data-history-back data-nav', album_response.data)
        self.assertIn(
            b'href="/albums/artist-a::album/edit" data-nav>edit</a>',
            album_response.data,
        )
        self.assertIn(b'href="/albums?artist=Artist+A" data-nav>Artist A</a>', album_response.data)
        self.assertIn(b'href="/albums?genre[0][p]=Electronic"', album_response.data)
        self.assertNotIn(b"size=80", album_response.data)
        self.assertNotIn(b"/albums/artist-a::album/edit?", album_response.data)

    def test_lazy_filter_endpoints_are_not_registered(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            app = create_player_app(self.make_options(temp_path))
            client = app.test_client()
            artist_response = client.get("/api/filters/artists?artist=Artist+A")
            genre_response = client.get("/api/filters/genres")

        self.assertEqual(artist_response.status_code, 404)
        self.assertEqual(genre_response.status_code, 404)

    def test_albums_and_playlist_pages_are_separate_indexes(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            save_library(
                MusicLibrary(
                    roots=["/music"],
                    tracks=[
                        TrackRecord(
                            path="/music/Artist/Studio Album/01.flac",
                            root_position=0,
                            file_type="flac",
                            artist="Artist",
                            album_artist="Artist",
                            album="Studio Album",
                            title="Track",
                        )
                    ],
                    playlists=[
                        PlaylistRecord(
                            path="/music/playlists/road.m3u8",
                            root_position=0,
                            name="Road Mix",
                            created_at="2026-04-29T12:00:00+00:00",
                            items=[
                                PlaylistItemRecord(
                                    path="https://example.test/stream",
                                    title="Stream",
                                )
                            ],
                        ),
                        PlaylistRecord(
                            path="/music/playlists/alpha.m3u8",
                            root_position=0,
                            name="Alpha Mix",
                            created_at="2026-04-28T12:00:00+00:00",
                        )
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-04-29T00:00:00+00:00",
                ),
                database,
            )
            runtime = self.make_runtime(database)
            with patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime):
                app = create_player_app(self.make_options(temp_path))
                client = app.test_client()
                albums_response = client.get("/albums")
                playlist_response = client.get("/playlists?search=Road")
                playlist_sort_response = client.get("/playlists?sort=artist")
                playlist_detail_response = client.get("/playlists/1?search=Road")

        self.assertEqual(albums_response.status_code, 200)
        self.assertIn(b"<h1>Albums</h1>", albums_response.data)
        self.assertIn(b"Studio Album", albums_response.data)
        self.assertNotIn(b"Road Mix", albums_response.data)
        self.assertNotIn(b'name="playlist"', albums_response.data)
        self.assertNotIn(b"Not playlist", albums_response.data)
        self.assertNotIn(b"artist-filter-readonly", albums_response.data)

        self.assertEqual(playlist_response.status_code, 200)
        self.assertIn(b"<h1>Playlists</h1>", playlist_response.data)
        self.assertNotIn(b"data-filter-form", playlist_response.data)
        self.assertNotIn(b'action="/playlists"', playlist_response.data)
        self.assertNotIn(b'placeholder="Search playlists"', playlist_response.data)
        self.assertNotIn(b'type="search"', playlist_response.data)
        self.assertNotIn(b"album-artist-link", playlist_response.data)
        self.assertIn(b"Road Mix", playlist_response.data)
        self.assertIn(b"Alpha Mix", playlist_response.data)
        self.assertNotIn(b"Studio Album", playlist_response.data)
        self.assertNotIn(b'name="sort"', playlist_response.data)
        self.assertNotIn(b'data-pagination-next', playlist_response.data)
        self.assertNotIn(b'data-pagination-previous', playlist_response.data)
        self.assertNotIn(b'class="filter-menu sort-menu"', playlist_response.data)
        self.assertNotIn(b'data-filter-summary="roots"', playlist_response.data)
        self.assertNotIn(b'data-filter-summary="artists"', playlist_response.data)
        self.assertNotIn(b'data-filter-summary="genres"', playlist_response.data)
        self.assertNotIn(b'data-filter-summary="properties"', playlist_response.data)
        self.assertIn(b'href="/playlists/1"', playlist_response.data)

        self.assertEqual(playlist_detail_response.status_code, 200)
        self.assertIn(b'href="/playlists"', playlist_detail_response.data)
        self.assertIn(b"data-play-album", playlist_detail_response.data)
        self.assertNotIn(b"data-play-track", playlist_detail_response.data)

        self.assertEqual(playlist_sort_response.status_code, 200)
        self.assertLess(
            playlist_sort_response.data.index(b"Road Mix"),
            playlist_sort_response.data.index(b"Alpha Mix"),
        )
        self.assertIn(b'href="/playlists/1"', playlist_sort_response.data)
        self.assertNotIn(b"sort=artist", playlist_sort_response.data)

    def test_help_page_renders_config_values_and_sources(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            config_path = temp_path / "kukicha.toml"
            self.write_config(
                config_path,
                "\n".join(
                    (
                        "log_level = 'info'",
                        "roots = ['music-a', 'music-b']",
                        "host = '0.0.0.0'",
                        "port = 43210",
                        "accent_color = 'cyan'",
                        "appearance = 'dim'",
                        "album_artist_split_patterns = ['&', '/']",
                    )
                ),
            )
            options = load_player_options(config_path)
            app = create_player_app(options)
            client = self.logged_in_client(app)

            response = client.get(
                "/help",
                environ_base={"REMOTE_ADDR": "203.0.113.10"},
                headers={
                    "User-Agent": "KukichaTest/1.0",
                    "X-Forwarded-For": "198.51.100.25",
                },
            )

            self.assertEqual(response.status_code, 200)
            self.assertIn(b"<h1>Help</h1>", response.data)
            self.assertIn(b"<h2>Version</h2>", response.data)
            self.assertIn(f"<code>{version('kukicha')}</code>".encode(), response.data)
            self.assertIn(b"<h2>Browser Login</h2>", response.data)
            self.assertIn(b"<dt>Status</dt>", response.data)
            self.assertIn(b"<dd><code>Active</code></dd>", response.data)
            self.assertIn(b"<dt>Signed in as</dt>", response.data)
            self.assertIn(b"<dt>User Agent</dt>", response.data)
            self.assertIn(b"<dd><code>KukichaTest/1.0</code></dd>", response.data)
            self.assertIn(b"<dt>Client IP</dt>", response.data)
            self.assertIn(b"<dd><code>203.0.113.10</code></dd>", response.data)
            self.assertNotIn(b"198.51.100.25", response.data)
            self.assertIn(b"<dt>Expires</dt>", response.data)
            self.assertIn(b" left)", response.data)
            self.assertIn(b"<h2>OpenSubsonic Clients</h2>", response.data)
            self.assertIn(b"OpenSubsonic is not configured.", response.data)
            self.assertIn(b"<h2>Config</h2>", response.data)
            self.assertLess(
                response.data.index(b"<h2>Version</h2>"),
                response.data.index(b"<h2>Browser Login</h2>"),
            )
            self.assertLess(
                response.data.index(b"<h2>Browser Login</h2>"),
                response.data.index(b"<h2>OpenSubsonic Clients</h2>"),
            )
            self.assertLess(
                response.data.index(b"<h2>OpenSubsonic Clients</h2>"),
                response.data.index(b"<h2>Config</h2>"),
            )
            self.assertIn(f"<code>{config_path.resolve()}</code>".encode(), response.data)
            self.assertIn(b"<code>log_level</code>", response.data)
            self.assertIn(b"<code>INFO</code>", response.data)
            self.assertIn(b"<code>host</code>", response.data)
            self.assertIn(b"<code>0.0.0.0</code>", response.data)
            self.assertIn(b"<code>port</code>", response.data)
            self.assertIn(b"<code>43210</code>", response.data)
            self.assertIn(b"<code>accent_color</code>", response.data)
            self.assertIn(b"<code>cyan</code>", response.data)
            self.assertIn(b"<code>appearance</code>", response.data)
            self.assertIn(b"<code>dim</code>", response.data)
            self.assertIn(b"<code>roots</code>", response.data)
            self.assertIn(b'class="config-array-value"', response.data)
            self.assertIn(f"<code>{(temp_path / 'music-a').resolve()}</code>".encode(), response.data)
            self.assertIn(f"<code>{(temp_path / 'music-b').resolve()}</code>".encode(), response.data)
            self.assertIn(b"<code>album_artist_split_patterns</code>", response.data)
            self.assertIn(b"<code>&amp;</code>", response.data)
            self.assertIn(b"<code>/</code>", response.data)
            self.assertIn(b"<code>auth.username</code>", response.data)
            self.assertIn(b"<code>listener</code>", response.data)
            self.assertIn(b"color-scheme: dark;", response.data)
            self.assertIn(b"--bg: #1e293b;", response.data)
            self.assertIn(b"--surface: #475569;", response.data)
            self.assertIn(b"--surface-overlay: rgba(71, 85, 105, 0.94);", response.data)
            self.assertIn(b"--text: #f4f4f5;", response.data)
            self.assertIn(b"--line: #64748b;", response.data)
            self.assertIn(b"--track-row-highlight: #334155;", response.data)
            self.assertIn(b"--track-row-highlight-text: #f4f4f5;", response.data)
            self.assertIn(b"--accent: #06b6d4;", response.data)
            self.assertIn(b"--accent-strong: #04768a;", response.data)
            self.assertIn(b"--accent-soft: #e1f6fa;", response.data)
            self.assertIn(b"--accent-foreground: #111827;", response.data)
            self.assertIn(b"--control-accent: #06b6d4;", response.data)
            self.assertIn(b'<span class="config-source configured">configured</span>', response.data)
            self.assertIn(b"<code>ffmpeg_path</code>", response.data)
            self.assertIn(b"<code>&lt;unset&gt;</code>", response.data)
            self.assertIn(b'<span class="config-source default">default</span>', response.data)

    def test_help_page_renders_browser_login_without_auth(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            runtime = self.make_runtime(temp_path / "kukicha.sqlite")
            with patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime):
                app = create_player_app(self.make_options(temp_path))

            response = app.test_client().get("/help", headers={"User-Agent": "KukichaTest/1.0"})

            self.assertEqual(response.status_code, 200)
            self.assertIn(b"<h2>Browser Login</h2>", response.data)
            self.assertIn(b"<dd><code>Not configured</code></dd>", response.data)
            self.assertNotIn(b"<dt>Signed in as</dt>", response.data)

    def test_help_page_renders_opensubsonic_clients(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            secret_file = temp_path / "opensubsonic.secret"
            secret_file.write_text("sonic-secret\n", encoding="utf-8")
            secret_file.chmod(0o600)
            runtime = self.make_runtime(database)
            options = replace(
                self.make_auth_options(temp_path),
                opensubsonic=OpenSubsonicOptions(
                    mount_prefix="/",
                    secret_file=secret_file,
                ),
            )
            with patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime):
                app = create_player_app(options)
            client = app.test_client()
            login_response = client.post(
                "/login",
                data={"username": "listener", "password": "secret"},
            )
            self.assertEqual(login_response.status_code, 302)

            empty_response = client.get("/help")
            record_opensubsonic_client(
                database,
                "first-client",
                seen_at=datetime(2026, 5, 19, 12, 1, tzinfo=UTC),
            )
            record_opensubsonic_client(
                database,
                "second-client",
                seen_at=datetime(2026, 5, 19, 12, 2, tzinfo=UTC),
            )
            response = client.get("/help")

            self.assertEqual(empty_response.status_code, 200)
            self.assertIn(b"No OpenSubsonic clients seen yet.", empty_response.data)
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"<h2>OpenSubsonic Clients</h2>", response.data)
            self.assertIn(b"<td><code>first-client</code></td>", response.data)
            self.assertIn(b"<td><code>second-client</code></td>", response.data)
            self.assertIn(b' datetime="2026-05-19T12:01:00+00:00"', response.data)
            self.assertIn(b' datetime="2026-05-19T12:02:00+00:00"', response.data)
            self.assertLess(
                response.data.index(b"<td><code>second-client</code></td>"),
                response.data.index(b"<td><code>first-client</code></td>"),
            )

    def test_player_page_renders_dark_appearance_theme(self) -> None:
        with TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "kukicha.toml"
            self.write_config(config_path, "appearance = 'dark'\n")
            app = create_player_app(load_player_options(config_path))
            client = self.logged_in_client(app)

            response = client.get("/help")

            self.assertEqual(response.status_code, 200)
            self.assertIn(b"color-scheme: dark;", response.data)
            self.assertIn(b"--bg: #18181b;", response.data)
            self.assertIn(b"--surface: #27272a;", response.data)
            self.assertIn(b"--surface-overlay-hover: rgba(63, 63, 70, 0.97);", response.data)
            self.assertIn(b"--muted: #a1a1aa;", response.data)
            self.assertIn(b"--line: #3f3f46;", response.data)
            self.assertIn(b"--track-row-highlight: #3f3f46;", response.data)
            self.assertIn(b"--track-row-highlight-text: #f4f4f5;", response.data)
            dark_control_accent = derived_control_accent(
                player_accent_theme(DEFAULT_ACCENT_COLOR).accent,
                APPEARANCE_THEMES["dark"],
            )
            self.assertIn(f"--control-accent: {dark_control_accent};".encode(), response.data)

    def test_player_page_renders_system_appearance_theme_media_query(self) -> None:
        with TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "kukicha.toml"
            self.write_config(config_path, "appearance = 'system'\n")
            app = create_player_app(load_player_options(config_path))
            client = self.logged_in_client(app)

            response = client.get("/help")

            self.assertEqual(response.status_code, 200)
            self.assertIn(b"<code>system</code>", response.data)
            self.assertIn(b"color-scheme: light;", response.data)
            self.assertIn(b"--bg: #f4f4f5;", response.data)
            self.assertIn(b"--surface: #ffffff;", response.data)
            self.assertIn(b"@media (prefers-color-scheme: dark)", response.data)
            dark_media = response.data.split(
                b"@media (prefers-color-scheme: dark)",
                1,
            )[1]
            self.assertIn(b"--bg: #1e293b;", dark_media)
            self.assertIn(b"--surface: #475569;", dark_media)
            self.assertIn(b"--track-row-highlight: #334155;", dark_media)
            dim_control_accent = derived_control_accent(
                player_accent_theme(DEFAULT_ACCENT_COLOR).accent,
                APPEARANCE_THEMES["dim"],
            )
            self.assertIn(f"--control-accent: {dim_control_accent};".encode(), dark_media)

    def test_settings_pages_render_roots_artist_split_rules_musicbrainz_listening_data_and_cache(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            save_library(
                MusicLibrary(
                    roots=["/music/a"],
                    tracks=[
                        TrackRecord(
                            path="/music/a/Brian Eno/Ambient 1/01.flac",
                            root_position=0,
                            file_type="flac",
                            artist="Brian Eno",
                            album_artist="Brian Eno",
                            album="Ambient 1",
                            title="1/1",
                        ),
                        TrackRecord(
                            path="/music/a/Brian Eno/Ambient 1/02.flac",
                            root_position=0,
                            file_type="flac",
                            artist="Brian Eno",
                            album_artist="Brian Eno",
                            album="Ambient 1",
                            title="2/1",
                        ),
                        TrackRecord(
                            path="/music/a/Robert Fripp/Exposure/01.flac",
                            root_position=0,
                            file_type="flac",
                            artist="Robert Fripp",
                            album_artist="Robert Fripp",
                            album="Exposure",
                            title="Preface",
                        ),
                    ],
                    playlists=[
                        PlaylistRecord(
                            path="/music/a/mix.m3u8",
                            root_position=0,
                            name="Mix",
                            items=[
                                PlaylistItemRecord(
                                    path="/music/a/Brian Eno/Ambient 1/01.flac"
                                )
                            ],
                        ),
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-04-29T00:00:00+00:00",
                ),
                database,
            )
            connection = connect_database(database)
            try:
                connection.execute(
                    """
                    INSERT INTO album_artist_split_mappings (
                        album_artist,
                        mapped_artists
                    ) VALUES (?, ?)
                    """,
                    ("Brian Eno & Robert Fripp", "Brian Eno\nRobert Fripp"),
                )
                ambient_album_id = str(
                    connection.execute(
                        """
                        SELECT album_id
                        FROM library_albums
                        WHERE album = ?
                        """,
                        ("Ambient 1",),
                    ).fetchone()["album_id"]
                )
                exposure_album_id = str(
                    connection.execute(
                        """
                        SELECT album_id
                        FROM library_albums
                        WHERE album = ?
                        """,
                        ("Exposure",),
                    ).fetchone()["album_id"]
                )
                connection.executemany(
                    """
                    INSERT INTO album_musicbrainz_links (
                        file_album_id,
                        release_mbid,
                        release_group_mbid
                    ) VALUES (?, ?, ?)
                    """,
                    (
                        (
                            ambient_album_id,
                            "33333333-3333-3333-3333-333333333333",
                            "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                        ),
                        (
                            exposure_album_id,
                            "11111111-1111-1111-1111-111111111111",
                            None,
                        ),
                        (
                            "stale-album-id",
                            None,
                            "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                        ),
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            runtime = self.make_runtime(database)
            with patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime):
                app = create_player_app(self.make_options(temp_path))
                client = app.test_client()
                roots_response = client.get("/roots")
                split_rules_response = client.get("/artist-split-rules")
                musicbrainz_response = client.get("/metadata-overrides")
                listening_data_response = client.get("/listening-data")
                cache_response = client.get("/cache")

            self.assertEqual(roots_response.status_code, 200)
            self.assertIn(b"<h1>Roots</h1>", roots_response.data)
            self.assertNotIn(b"<h2>Add Root</h2>", roots_response.data)
            self.assertNotIn(b"data-root-form", roots_response.data)
            self.assertNotIn(b"data-delete-root", roots_response.data)
            self.assertIn(b"<h2>Current Roots</h2>", roots_response.data)
            self.assertIn(b"data-rescan-library", roots_response.data)
            self.assertNotIn(b"data-rescan-root", roots_response.data)
            self.assertIn(b"/music/a", roots_response.data)
            self.assertIn(b"Tracks scanned</dt>\n                <dd>3</dd>", roots_response.data)
            self.assertIn(b"Albums in root</dt>\n                <dd>2</dd>", roots_response.data)
            self.assertNotIn(b"Playlists scanned", roots_response.data)
            self.assertNotIn(b"<h2>Artists Split Rules</h2>", roots_response.data)
            self.assertNotIn(b"<h2>Cache</h2>", roots_response.data)

            self.assertEqual(split_rules_response.status_code, 200)
            self.assertIn(b"<h1>Artists Split Rules</h1>", split_rules_response.data)
            self.assertIn(b"<h2>Artists Split Rules</h2>", split_rules_response.data)
            self.assertNotIn(b"<h2>Custom Album Artist Split Rules</h2>", split_rules_response.data)
            self.assertNotIn(b"data-album-artist-mapping-form", split_rules_response.data)
            self.assertIn(b"data-edit-album-artist-mapping", split_rules_response.data)
            self.assertIn(b"Brian Eno &amp; Robert Fripp", split_rules_response.data)
            self.assertIn(b"Brian Eno\nRobert Fripp", split_rules_response.data)
            self.assertNotIn(b"<h2>Add Root</h2>", split_rules_response.data)

            self.assertEqual(musicbrainz_response.status_code, 200)
            self.assertIn(b"<h1>Metadata Overrides</h1>", musicbrainz_response.data)
            self.assertIn(b"<h2>Metadata Overrides</h2>", musicbrainz_response.data)
            self.assertIn(b"3 overrides", musicbrainz_response.data)
            self.assertIn(b"Ambient 1", musicbrainz_response.data)
            self.assertIn(b"Brian Eno", musicbrainz_response.data)
            self.assertIn(
                b"11111111-1111-1111-1111-111111111111",
                musicbrainz_response.data,
            )
            self.assertIn(
                b"bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                musicbrainz_response.data,
            )
            self.assertIn(b"stale-album-id", musicbrainz_response.data)
            self.assertIn(b"Not in current library", musicbrainz_response.data)
            self.assertIn(
                b"aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                musicbrainz_response.data,
            )
            self.assertIn(b"Not set", musicbrainz_response.data)
            self.assertIn(b"data-delete-metadata-override", musicbrainz_response.data)
            self.assertIn(
                b'data-delete-url="/api/metadata-overrides/stale-album-id/delete"',
                musicbrainz_response.data,
            )
            self.assertIn(
                f'data-delete-url="/api/metadata-overrides/{ambient_album_id}/delete"'.encode(),
                musicbrainz_response.data,
            )
            self.assertIn(b'class="delete-icon-button"', musicbrainz_response.data)
            self.assertIn(b">Edit</a>", musicbrainz_response.data)
            self.assertNotIn(b"<h2>Add Root</h2>", musicbrainz_response.data)

            self.assertEqual(listening_data_response.status_code, 200)
            self.assertIn(b"<h1>Listening Data</h1>", listening_data_response.data)
            self.assertIn(b"<h2>Reset Listening Data</h2>", listening_data_response.data)
            self.assertIn(b"data-reset-listening-data", listening_data_response.data)
            self.assertIn(
                b'data-reset-url="/api/listening-data/reset"',
                listening_data_response.data,
            )
            self.assertIn(b"<h2>History</h2>", listening_data_response.data)
            self.assertIn(b"<h2>Stats</h2>", listening_data_response.data)
            self.assertIn(b">Play Events</div>", listening_data_response.data)
            self.assertIn(b">Now Playing</div>", listening_data_response.data)
            self.assertIn(b">Albums</div>", listening_data_response.data)
            self.assertIn(b">Tracks</div>", listening_data_response.data)
            self.assertIn(b">Playlists</div>", listening_data_response.data)
            self.assertIn(b">Artists</div>", listening_data_response.data)
            self.assertIn(b">Genres</div>", listening_data_response.data)
            self.assertNotIn(b"<h2>Add Root</h2>", listening_data_response.data)
            self.assertNotIn(b"data-edit-album-artist-mapping", listening_data_response.data)

            self.assertEqual(cache_response.status_code, 200)
            self.assertIn(b"<h1>Cache</h1>", cache_response.data)
            self.assertIn(b"<h2>MusicBrainz</h2>", cache_response.data)
            self.assertIn(b"<h2>iTunes</h2>", cache_response.data)
            self.assertIn(b">Entities</div>", cache_response.data)
            self.assertIn(b">Cover Artwork + Metadata</div>", cache_response.data)
            self.assertIn(b">Cover Artwork</div>", cache_response.data)
            self.assertIn(b"data-clear-cache", cache_response.data)
            self.assertIn(
                b'data-clear-url="/api/cache/musicbrainz-entities/clear"',
                cache_response.data,
            )
            self.assertIn(
                b'data-clear-url="/api/cache/musicbrainz-cover-artwork-metadata/clear"',
                cache_response.data,
            )
            self.assertIn(
                b'data-clear-url="/api/cache/itunes-cover-artwork/clear"',
                cache_response.data,
            )
            self.assertLess(
                cache_response.data.index(b"<h2>MusicBrainz</h2>"),
                cache_response.data.index(b"<h2>iTunes</h2>"),
            )
            self.assertNotIn(b"<h2>Add Root</h2>", cache_response.data)
            self.assertNotIn(b"data-edit-album-artist-mapping", cache_response.data)

    def test_cache_clear_routes_truncate_configured_cache_tables(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            connection = connect_database(database)
            try:
                connection.execute(
                    """
                    INSERT INTO musicbrainz_entity_cache (
                        entity_type,
                        mbid,
                        fetched_at,
                        endpoint_url,
                        response_json
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        "release",
                        "11111111-1111-1111-1111-111111111111",
                        "2026-05-23T12:00:00+00:00",
                        "https://musicbrainz.example/release/1",
                        "{}",
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO cover_art_archive_entity_cache (
                        entity_type,
                        mbid,
                        fetched_at,
                        endpoint_url,
                        response_json
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        "release",
                        "22222222-2222-2222-2222-222222222222",
                        "2026-05-23T12:00:00+00:00",
                        "https://coverartarchive.example/release/2",
                        "{}",
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO cover_art_archive_image_cache (
                        image_url,
                        fetched_at,
                        mime_type,
                        data
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        "https://coverartarchive.example/image/2.jpg",
                        "2026-05-23T12:00:00+00:00",
                        "image/jpeg",
                        b"caa-image",
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO itunes_lookup_image_cache (
                        cache_key,
                        lookup_kind,
                        lookup_id,
                        result_kind,
                        fetched_at,
                        lookup_url,
                        artwork_url,
                        mime_type,
                        data
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "album:440769149",
                        "album",
                        "440769149",
                        "hit",
                        "2026-05-23T12:00:00+00:00",
                        "https://itunes.apple.com/lookup?id=440769149&media=music",
                        "https://is1-ssl.mzstatic.com/image/thumb/art.jpg",
                        "image/jpeg",
                        b"itunes-image",
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            runtime = self.make_runtime(database)
            with patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime):
                app = create_player_app(self.make_options(temp_path))
                client = app.test_client()

                cover_response = client.post(
                    "/api/cache/musicbrainz-cover-artwork-metadata/clear"
                )
                entities_response = client.post("/api/cache/musicbrainz-entities/clear")
                itunes_response = client.post("/api/cache/itunes-cover-artwork/clear")
                missing_response = client.post("/api/cache/not-real/clear")

            self.assertEqual(cover_response.status_code, 200)
            self.assertEqual(
                cover_response.get_json(),
                {
                    "cache_key": "musicbrainz-cover-artwork-metadata",
                    "cleared_entries": 2,
                    "message": "Cleared MusicBrainz Cover Artwork + Metadata cache.",
                },
            )
            self.assertEqual(entities_response.status_code, 200)
            self.assertEqual(entities_response.get_json()["cleared_entries"], 1)
            self.assertEqual(itunes_response.status_code, 200)
            self.assertEqual(
                itunes_response.get_json()["message"],
                "Cleared iTunes Cover Artwork cache.",
            )
            self.assertEqual(missing_response.status_code, 404)
            self.assertEqual(
                missing_response.get_json(),
                {"error": "cache target does not exist: not-real"},
            )

            connection = connect_database(database, create=False)
            try:
                for table_name in (
                    "musicbrainz_entity_cache",
                    "cover_art_archive_entity_cache",
                    "cover_art_archive_image_cache",
                    "itunes_lookup_image_cache",
                ):
                    self.assertEqual(
                        int(
                            connection.execute(
                                f"SELECT COUNT(*) AS count FROM {table_name}"
                            ).fetchone()["count"]
                        ),
                        0,
                    )
            finally:
                connection.close()

    def test_listening_data_reset_clears_history_without_removing_library_data(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            save_library(
                MusicLibrary(
                    roots=["/music"],
                    tracks=[
                        TrackRecord(
                            path="/music/Artist/Album/01.flac",
                            root_position=0,
                            file_created_at="2026-05-20T00:00:00+00:00",
                            file_type="flac",
                            artist="Artist",
                            album_artist="Artist",
                            album="Album",
                            title="Track",
                            genres=["Jazz"],
                        )
                    ],
                    playlists=[
                        PlaylistRecord(
                            path="/music/mix.m3u8",
                            root_position=0,
                            name="Mix",
                            items=[PlaylistItemRecord(path="/music/Artist/Album/01.flac")],
                        ),
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-05-20T00:00:00+00:00",
                ),
                database,
            )
            connection = connect_database(database, create=False)
            try:
                album_id = str(
                    connection.execute(
                        "SELECT album_id FROM library_albums WHERE album = ?",
                        ("Album",),
                    ).fetchone()["album_id"]
                )
                playlist_item_id = int(
                    connection.execute(
                        "SELECT playlist_item_id FROM library_playlist_items"
                    ).fetchone()["playlist_item_id"]
                )
                connection.execute(
                    """
                    INSERT INTO album_user_state (album_id, starred_at)
                    VALUES (?, ?)
                    """,
                    (album_id, "2026-05-21T00:00:00+00:00"),
                )
                connection.execute(
                    """
                    INSERT INTO player_queue_state (state_id, position, paused, updated_at)
                    VALUES (1, 0, 0, ?)
                    """,
                    ("2026-05-21T00:00:00+00:00",),
                )
                connection.execute(
                    """
                    INSERT INTO player_queue_items (position, playback_id, snapshot_json)
                    VALUES (0, 1, ?)
                    """,
                    ("{}",),
                )
                connection.execute(
                    """
                    INSERT INTO opensubsonic_clients (client_name, last_seen_at)
                    VALUES (?, ?)
                    """,
                    ("ampache", "2026-05-21T00:00:00+00:00"),
                )
                connection.execute(
                    """
                    INSERT INTO musicbrainz_entity_cache (
                        entity_type,
                        mbid,
                        fetched_at,
                        endpoint_url,
                        response_json
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        "release",
                        "11111111-1111-1111-1111-111111111111",
                        "2026-05-21T00:00:00+00:00",
                        "https://musicbrainz.example/release/1",
                        "{}",
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            record_playback(
                database,
                1,
                submission=True,
                played_at=datetime(2026, 5, 21, 12, 0, tzinfo=UTC),
                source=NATIVE_PLAYBACK_SOURCE,
            )
            record_playback(
                database,
                -playlist_item_id,
                submission=True,
                played_at=datetime(2026, 5, 21, 12, 5, tzinfo=UTC),
                source=NATIVE_PLAYBACK_SOURCE,
            )
            record_playback(
                database,
                1,
                submission=False,
                played_at=datetime(2026, 5, 21, 12, 10, tzinfo=UTC),
                source=NATIVE_PLAYBACK_SOURCE,
            )

            runtime = self.make_runtime(database)
            runtime.queue_state_copy.return_value = PlayerQueueState(
                track_ids=[1],
                position=0,
                loaded_track_id=1,
                paused=False,
            )
            with patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime):
                app = create_player_app(self.make_options(temp_path))
                client = app.test_client()
                home_before_response = client.get("/")
                reset_response = client.post("/api/listening-data/reset")
                home_after_response = client.get("/")

            self.assertEqual(home_before_response.status_code, 200)
            self.assertIn(b"Continue Listening", home_before_response.data)
            self.assertIn(b"Recently Listened Albums", home_before_response.data)
            self.assertIn(b"Added in the Last Month", home_before_response.data)

            self.assertEqual(reset_response.status_code, 200)
            self.assertEqual(
                reset_response.get_json(),
                {
                    "cleared_entries": 8,
                    "message": "Reset listening data.",
                },
            )

            self.assertEqual(home_after_response.status_code, 200)
            self.assertNotIn(b"Continue Listening", home_after_response.data)
            self.assertNotIn(b"Recently Listened Albums", home_after_response.data)
            self.assertIn(b"Added in the Last Month", home_after_response.data)

            connection = connect_database(database, create=False)
            try:
                for table_name in (
                    "play_events",
                    "play_now_playing",
                    "play_track_stats",
                    "play_album_stats",
                    "play_artist_stats",
                    "play_playlist_stats",
                    "play_genre_stats",
                ):
                    self.assertEqual(
                        int(
                            connection.execute(
                                f"SELECT COUNT(*) AS count FROM {table_name}"
                            ).fetchone()["count"]
                        ),
                        0,
                    )
                preserved_counts = {
                    table_name: int(
                        connection.execute(
                            f"SELECT COUNT(*) AS count FROM {table_name}"
                        ).fetchone()["count"]
                    )
                    for table_name in (
                        "library_tracks",
                        "library_albums",
                        "album_user_state",
                        "player_queue_state",
                        "player_queue_items",
                        "opensubsonic_clients",
                        "musicbrainz_entity_cache",
                    )
                }
            finally:
                connection.close()
            self.assertEqual(
                preserved_counts,
                {
                    "library_tracks": 1,
                    "library_albums": 1,
                    "album_user_state": 1,
                    "player_queue_state": 1,
                    "player_queue_items": 1,
                    "opensubsonic_clients": 1,
                    "musicbrainz_entity_cache": 1,
                },
            )

    def test_album_artist_mapping_route_updates_mapping_without_starting_job(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            connection = connect_database(database)
            try:
                connection.execute(
                    """
                    INSERT INTO album_artist_split_mappings (
                        album_artist,
                        mapped_artists
                    ) VALUES (?, ?)
                    """,
                    ("Brian Eno & Robert Fripp", "Old Value"),
                )
                connection.commit()
            finally:
                connection.close()
            runtime = self.make_runtime(database)
            with patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime):
                app = create_player_app(self.make_options(temp_path))
                response = app.test_client().post(
                    "/api/album-artist-mappings",
                    json={
                        "album_artist": "  Brian Eno & Robert Fripp  ",
                        "mapped_artists": "  Brian Eno  \n\n  Robert Fripp  \n",
                    },
                )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                response.get_json(),
                {
                    "mapping": {
                        "album_artist": "Brian Eno & Robert Fripp",
                        "mapped_artists": "Brian Eno\nRobert Fripp",
                    },
                    "message": (
                        "Saved mapping for Brian Eno & Robert Fripp. "
                        "Rescan the library to update library filters, artists, and stats."
                    ),
                },
            )
            runtime.enqueue_job.assert_not_called()
            connection = connect_database(database, create=False)
            try:
                row = connection.execute(
                    """
                    SELECT mapped_artists
                    FROM album_artist_split_mappings
                    WHERE album_artist = ?
                    """,
                    ("Brian Eno & Robert Fripp",),
                ).fetchone()
            finally:
                connection.close()
            self.assertIsNotNone(row)
            self.assertEqual(str(row["mapped_artists"]), "Brian Eno\nRobert Fripp")

    def test_album_artist_mapping_route_does_not_create_new_mapping(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            connection = connect_database(database)
            connection.close()
            runtime = self.make_runtime(database)
            with patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime):
                app = create_player_app(self.make_options(temp_path))
                response = app.test_client().post(
                    "/api/album-artist-mappings",
                    json={
                        "album_artist": "Missing Artist",
                        "mapped_artists": "One\nTwo",
                    },
                )

            self.assertEqual(response.status_code, 404)
            runtime.enqueue_job.assert_not_called()
            connection = connect_database(database, create=False)
            try:
                count = int(
                    connection.execute(
                        "SELECT COUNT(*) AS count FROM album_artist_split_mappings"
                    ).fetchone()["count"]
                )
            finally:
                connection.close()
            self.assertEqual(count, 0)

    def test_musicbrainz_override_route_deletes_saved_rows(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            save_library(
                MusicLibrary(
                    roots=["/music/a"],
                    tracks=[
                        TrackRecord(
                            path="/music/a/Brian Eno/Ambient 1/01.flac",
                            root_position=0,
                            file_type="flac",
                            artist="Brian Eno",
                            album_artist="Brian Eno",
                            album="Ambient 1",
                            title="1/1",
                        ),
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-04-29T00:00:00+00:00",
                ),
                database,
            )
            connection = connect_database(database, create=False)
            try:
                current_album_id = str(
                    connection.execute(
                        "SELECT album_id FROM library_albums WHERE album = ?",
                        ("Ambient 1",),
                    ).fetchone()["album_id"]
                )
                connection.executemany(
                    """
                    INSERT INTO album_musicbrainz_links (
                        file_album_id,
                        release_mbid,
                        release_group_mbid
                    ) VALUES (?, ?, ?)
                    """,
                    (
                        (
                            current_album_id,
                            "11111111-1111-1111-1111-111111111111",
                            None,
                        ),
                        (
                            "stale-album-id",
                            None,
                            "22222222-2222-2222-2222-222222222222",
                        ),
                    ),
                )
                connection.executemany(
                    """
                    INSERT INTO album_musicbrainz_track_links (
                        path,
                        file_album_id,
                        release_mbid,
                        release_group_mbid
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        (
                            "/music/a/Brian Eno/Ambient 1/01.flac",
                            current_album_id,
                            "11111111-1111-1111-1111-111111111111",
                            None,
                        ),
                        (
                            "/missing/stale.flac",
                            "stale-album-id",
                            None,
                            "22222222-2222-2222-2222-222222222222",
                        ),
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            runtime = self.make_runtime(database)
            with patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime):
                app = create_player_app(self.make_options(temp_path))
                client = app.test_client()
                stale_response = client.post(
                    "/api/metadata-overrides/stale-album-id/delete"
                )
                current_response = client.post(
                    f"/api/metadata-overrides/{current_album_id}/delete"
                )

            self.assertEqual(stale_response.status_code, 200)
            self.assertEqual(
                stale_response.get_json(),
                {
                    "album_id": "stale-album-id",
                    "message": "Deleted metadata override for stale-album-id.",
                },
            )
            self.assertEqual(current_response.status_code, 200)
            self.assertEqual(
                current_response.get_json(),
                {
                    "album_id": current_album_id,
                    "message": f"Deleted metadata override for {current_album_id}.",
                },
            )
            runtime.enqueue_job.assert_not_called()

            connection = connect_database(database, create=False)
            try:
                stale_count = int(
                    connection.execute(
                        """
                        SELECT COUNT(*) AS count
                        FROM album_musicbrainz_links
                        WHERE file_album_id = ?
                        """,
                        ("stale-album-id",),
                    ).fetchone()["count"]
                )
                current_count = int(
                    connection.execute(
                        """
                        SELECT COUNT(*) AS count
                        FROM album_musicbrainz_links
                        WHERE file_album_id = ?
                        """,
                        (current_album_id,),
                    ).fetchone()["count"]
                )
                stale_track_count = int(
                    connection.execute(
                        """
                        SELECT COUNT(*) AS count
                        FROM album_musicbrainz_track_links
                        WHERE file_album_id = ?
                        """,
                        ("stale-album-id",),
                    ).fetchone()["count"]
                )
                current_track_count = int(
                    connection.execute(
                        """
                        SELECT COUNT(*) AS count
                        FROM album_musicbrainz_track_links
                        WHERE file_album_id = ?
                        """,
                        (current_album_id,),
                    ).fetchone()["count"]
                )
            finally:
                connection.close()

            self.assertEqual(stale_count, 0)
            self.assertEqual(current_count, 0)
            self.assertEqual(stale_track_count, 0)
            self.assertEqual(current_track_count, 0)

    def test_post_json_body_is_passed_to_command(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            runtime = self.make_runtime(temp_path / "kukicha.sqlite")
            with (
                patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime),
                patch(
                    "kukicha.player_web_adapter.update_playback_command",
                    return_value={"paused": False},
                ) as command,
            ):
                app = create_player_app(self.make_options(temp_path))

                response = app.test_client().post("/api/playback", json={"paused": False})

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json(), {"paused": False})
            command.assert_called_once_with(runtime, {"paused": False})

    def test_queue_append_and_remove_routes_persist_queue(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            save_library(
                MusicLibrary(
                    roots=["/music"],
                    tracks=[
                        TrackRecord(
                            path="/music/Artist/Album/01.mp3",
                            file_type="mp3",
                            artist="Artist",
                            album_artist="Artist",
                            album="Album",
                            title="One",
                        ),
                        TrackRecord(
                            path="/music/Artist/Album/02.mp3",
                            file_type="mp3",
                            artist="Artist",
                            album_artist="Artist",
                            album="Album",
                            title="Two",
                        ),
                    ],
                    supported_extensions=[".mp3"],
                    generated_at="2026-05-01T00:00:00+00:00",
                ),
                database,
            )
            app = create_player_app(self.make_options(temp_path))
            client = app.test_client()

            first = client.post("/api/queue/append", json={"track_ids": [1]})
            second = client.post("/api/queue/append", json={"track_ids": [2]})
            removed = client.post("/api/queue/remove", json={"position": 0})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.get_json()["track_ids"], [1])
        self.assertTrue(first.get_json()["paused"])
        self.assertEqual(second.get_json()["track_ids"], [1, 2])
        self.assertEqual(removed.get_json()["queue"]["track_ids"], [2])
        self.assertFalse(removed.get_json()["play_next"])

    def test_queue_route_preserves_display_track_number_snapshot(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            save_library(
                MusicLibrary(
                    roots=["/music"],
                    tracks=[
                        TrackRecord(
                            path="/music/Artist/Album/09.mp3",
                            file_type="mp3",
                            artist="Artist",
                            album_artist="Artist",
                            album="Album",
                            title="Nine",
                            track_number="9",
                        ),
                    ],
                    supported_extensions=[".mp3"],
                    generated_at="2026-05-01T00:00:00+00:00",
                ),
                database,
            )
            app = create_player_app(self.make_options(temp_path))
            client = app.test_client()

            response = client.post(
                "/api/queue",
                json={
                    "track_ids": [1],
                    "position": 0,
                    "loaded_track_id": 1,
                    "paused": False,
                    "track_snapshots": [{"trackId": 1, "trackNumber": "1"}],
                },
            )
            queue_response = client.get("/queue", headers={"Accept": "text/html"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["track_snapshots"][0]["trackNumber"], "1")
        html = queue_response.get_data(as_text=True)
        self.assertIn('<td class="track-number">1</td>', html)
        self.assertNotIn('<td class="track-number">9</td>', html)

    def test_full_document_load_pauses_persisted_queue_without_clearing_it(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            save_library(
                MusicLibrary(
                    roots=["/music"],
                    tracks=[
                        TrackRecord(
                            path="/music/Artist/Album/01.mp3",
                            file_type="mp3",
                            artist="Artist",
                            album_artist="Artist",
                            album="Album",
                            title="One",
                        )
                    ],
                    supported_extensions=[".mp3"],
                    generated_at="2026-05-01T00:00:00+00:00",
                ),
                database,
            )
            app = create_player_app(self.make_options(temp_path))
            client = app.test_client()
            client.post(
                "/api/queue",
                json={
                    "track_ids": [1],
                    "position": 0,
                    "loaded_track_id": 1,
                    "paused": False,
                },
            )

            response = client.get("/")
            state = load_queue_state_database(database)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(state.track_ids, [1])
        self.assertEqual(state.loaded_track_id, 1)
        self.assertTrue(state.paused)

    def test_post_json_body_rejects_invalid_json(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            runtime = self.make_runtime(temp_path / "kukicha.sqlite")
            with patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime):
                app = create_player_app(self.make_options(temp_path))

            response = app.test_client().post(
                "/api/playback",
                data="{",
                content_type="application/json",
            )

            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.get_json(), {"error": "invalid JSON"})

    def test_api_errors_are_returned_as_json(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            runtime = self.make_runtime(temp_path / "kukicha.sqlite")
            with (
                patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime),
                patch(
                    "kukicha.player_web_adapter.update_playback_command",
                    side_effect=PlayerConflictError("busy"),
                ),
            ):
                app = create_player_app(self.make_options(temp_path))

                response = app.test_client().post("/api/playback", json={"paused": False})

            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.get_json(), {"error": "busy"})

    def test_track_playlist_membership_route_uses_typed_parameters(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            runtime = self.make_runtime(temp_path / "kukicha.sqlite")
            with (
                patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime),
                patch(
                    "kukicha.player_web_adapter.update_track_playlist_membership_command",
                    return_value={"checked": True},
                ) as command,
            ):
                app = create_player_app(self.make_options(temp_path))

                response = app.test_client().post(
                    "/api/tracks/12/playlists/34",
                    json={"checked": True},
                )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json(), {"checked": True})
            command.assert_called_once_with(
                runtime,
                12,
                34,
                {"checked": True},
            )

    def test_create_playlist_route_seeds_playlist_with_track(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            track_path = temp_path / "music" / "Artist" / "Album" / "01.flac"
            save_library(
                MusicLibrary(
                    roots=[str(temp_path / "music")],
                    tracks=[
                        TrackRecord(
                            path=str(track_path),
                            root_position=0,
                            file_type="flac",
                            artist="Artist",
                            album_artist="Artist",
                            album="Album",
                            title="Track",
                        )
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-05-30T00:00:00+00:00",
                ),
                database,
            )
            app = create_player_app(self.make_options(temp_path))

            response = app.test_client().post(
                "/api/playlists",
                json={"name": "Track Picks", "track_ids": [1]},
            )

            with connect_database(database, create=False) as connection:
                playlist = connection.execute(
                    """
                    SELECT name, source, kind
                    FROM library_playlists
                    WHERE playlist_id = 1
                    """
                ).fetchone()
                item = connection.execute(
                    """
                    SELECT track_id, path
                    FROM library_playlist_items
                    WHERE playlist_id = 1
                    """
                ).fetchone()

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()["playlist_id"], 1)
        self.assertEqual(str(playlist["name"]), "Track Picks")
        self.assertEqual(str(playlist["source"]), "manual")
        self.assertEqual(str(playlist["kind"]), "local")
        self.assertEqual(int(item["track_id"]), 1)
        self.assertEqual(str(item["path"]), str(track_path))

    def test_playlist_edit_cover_upload_and_delete_routes_update_database(self) -> None:
        png_cover = b"\x89PNG\r\n\x1a\nplaylist-cover"
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            track_path = temp_path / "music" / "Artist" / "Album" / "01.flac"
            save_library(
                MusicLibrary(
                    roots=[str(temp_path / "music")],
                    tracks=[
                        TrackRecord(
                            path=str(track_path),
                            root_position=0,
                            file_type="flac",
                            artist="Artist",
                            album_artist="Artist",
                            album="Album",
                            title="Track",
                        )
                    ],
                    playlists=[
                        PlaylistRecord(
                            playlist_id=1,
                            name="Road Mix",
                            source="manual",
                            items=[PlaylistItemRecord(path=str(track_path), track_id=1)],
                        )
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-05-30T00:00:00+00:00",
                ),
                database,
            )
            with connect_database(database, create=False) as connection:
                connection.execute(
                    """
                    INSERT INTO play_playlist_stats (
                        playlist_key, play_count, last_played_at, playlist_id, name
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    ("1", 1, "2026-05-30T00:00:00+00:00", 1, "Road Mix"),
                )
                connection.commit()
            app = create_player_app(self.make_options(temp_path))
            client = app.test_client()

            edit_response = client.get("/playlists/1/edit")
            upload_response = client.post(
                "/api/playlists/1/cover",
                data={"cover": (io.BytesIO(png_cover), "front.png")},
            )
            cover_response = client.get("/api/playlists/1/cover")
            playlist_response = client.get("/playlists/1")
            delete_response = client.post("/api/playlists/1/delete")
            with connect_database(database, create=False) as connection:
                playlist_count = int(
                    connection.execute("SELECT COUNT(*) FROM library_playlists").fetchone()[0]
                )
                item_count = int(
                    connection.execute("SELECT COUNT(*) FROM library_playlist_items").fetchone()[0]
                )
                stats_count = int(
                    connection.execute("SELECT COUNT(*) FROM play_playlist_stats").fetchone()[0]
                )

        self.assertEqual(edit_response.status_code, 200)
        self.assertIn(b'data-page="playlist-edit"', edit_response.data)
        self.assertIn(b'data-playlist-delete-form', edit_response.data)
        self.assertIn(b'data-playlist-cover-form', edit_response.data)
        self.assertEqual(upload_response.status_code, 200)
        self.assertEqual(upload_response.get_json()["cover_mime_type"], "image/png")
        self.assertEqual(cover_response.status_code, 200)
        self.assertEqual(cover_response.content_type, "image/png")
        self.assertEqual(cover_response.data, png_cover)
        self.assertIn(b'src="/api/playlists/1/cover"', playlist_response.data)
        self.assertEqual(delete_response.status_code, 200)
        self.assertEqual(delete_response.get_json()["redirect_url"], "/playlists")
        self.assertEqual(playlist_count, 0)
        self.assertEqual(item_count, 0)
        self.assertEqual(stats_count, 0)

    def test_static_file_and_favicon_are_served(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            runtime = self.make_runtime(temp_path / "kukicha.sqlite")
            with patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime):
                app = create_player_app(self.make_options(temp_path))
                client = app.test_client()
                static_response = client.get("/static/player.css")
                css_response = client.get(static_asset_url("player.css"))
                js_response = client.get(static_asset_url("player.js"))
                svg_response = client.get(static_asset_url("favicon.svg"))
                favicon_response = client.get("/favicon.ico")
                stale_url = "/static/player.000000000000.css"
                if stale_url == static_asset_url("player.css"):
                    stale_url = "/static/player.111111111111.css"
                stale_response = client.get(stale_url)

            self.assertEqual(static_response.status_code, 200)
            self.assertEqual(static_response.content_type, "text/css; charset=utf-8")
            self.assertEqual(static_response.headers["Cache-Control"], STATIC_COMPAT_CACHE_CONTROL)
            self.assertEqual(css_response.status_code, 200)
            self.assertEqual(css_response.content_type, "text/css; charset=utf-8")
            self.assertEqual(css_response.headers["Cache-Control"], STATIC_ASSET_CACHE_CONTROL)
            self.assertEqual(js_response.status_code, 200)
            self.assertEqual(js_response.content_type, "application/javascript; charset=utf-8")
            self.assertEqual(js_response.headers["Cache-Control"], STATIC_ASSET_CACHE_CONTROL)
            self.assertEqual(svg_response.status_code, 200)
            self.assertEqual(svg_response.content_type, "image/svg+xml")
            self.assertEqual(svg_response.headers["Cache-Control"], STATIC_ASSET_CACHE_CONTROL)
            self.assertEqual(favicon_response.status_code, 200)
            self.assertEqual(favicon_response.content_type, "image/svg+xml")
            self.assertEqual(favicon_response.headers["Cache-Control"], STATIC_COMPAT_CACHE_CONTROL)
            self.assertEqual(stale_response.status_code, 404)

    def test_artwork_responses_are_cached_for_one_week(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            save_library(
                MusicLibrary(
                    roots=[],
                    tracks=[
                        TrackRecord(
                            path="/music/Artist/Album/01.flac",
                            file_type="flac",
                            artist="Artist",
                            album_artist="Artist",
                            album="Album",
                            title="Track",
                            artwork=TrackArtwork(
                                mime_type="image/png",
                                data=b"cover",
                            ),
                        )
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-05-01T00:00:00+00:00",
                ),
                database,
            )
            runtime = self.make_runtime(database)
            with patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime):
                app = create_player_app(self.make_options(temp_path))
                response = app.test_client().get("/art/1")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content_type, "image/png")
            self.assertEqual(response.headers["Cache-Control"], "private, max-age=604800")

    def test_audio_file_supports_full_range_partial_range_and_head(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            audio_path = temp_path / "track.mp3"
            audio_path.write_bytes(b"0123456789")
            runtime = self.make_runtime(temp_path / "kukicha.sqlite")
            with (
                patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime),
                patch("kukicha.player_web_adapter.track_audio_path", return_value=audio_path),
            ):
                app = create_player_app(self.make_options(temp_path))
                client = app.test_client()
                full_response = client.get("/audio/7")
                partial_response = client.get("/audio/7", headers={"Range": "bytes=2-5"})
                invalid_range_response = client.get(
                    "/audio/7",
                    headers={"Range": "bytes=99-120"},
                )
                head_response = client.head("/audio/7", headers={"Range": "bytes=2-5"})

            self.assertEqual(full_response.status_code, 200)
            self.assertEqual(full_response.data, b"0123456789")
            self.assertEqual(full_response.headers["Accept-Ranges"], "bytes")
            self.assertEqual(full_response.headers["Content-Length"], "10")
            self.assertEqual(partial_response.status_code, 206)
            self.assertEqual(partial_response.data, b"2345")
            self.assertEqual(partial_response.headers["Content-Range"], "bytes 2-5/10")
            self.assertEqual(invalid_range_response.status_code, 200)
            self.assertEqual(invalid_range_response.data, b"0123456789")
            self.assertEqual(head_response.status_code, 206)
            self.assertEqual(head_response.data, b"")
            self.assertEqual(head_response.headers["Content-Length"], "4")

    def test_s3_client_factory_reuses_remote_clients(self) -> None:
        remote = RemoteRootConfig(
            name="Remote",
            endpoint_url="https://s3.example.test",
            bucket="bucket",
            prefix="tracks/",
            profile="remote-profile",
        )
        session = Mock()
        created_client = object()
        session.create_client.return_value = created_client

        clear_s3_client_cache()
        try:
            with patch("botocore.session.Session", return_value=session) as session_class:
                first = create_s3_client(remote)
                second = create_s3_client(
                    RemoteRootConfig(
                        name="Remote",
                        endpoint_url="https://s3.example.test",
                        bucket="bucket",
                        prefix="tracks/",
                        profile="remote-profile",
                    )
                )
        finally:
            clear_s3_client_cache()

        self.assertIs(first, created_client)
        self.assertIs(second, created_client)
        session_class.assert_called_once_with(profile="remote-profile")
        session.create_client.assert_called_once()

    def test_remote_audio_resource_supports_head_full_and_range(self) -> None:
        class FakeS3Client:
            def __init__(self, data: bytes) -> None:
                self.data = data
                self.get_ranges: list[str | None] = []

            def head_object(self, **kwargs: object) -> dict[str, object]:
                assert kwargs["Bucket"] == "bucket"
                assert kwargs["Key"] == "tracks/01.flac"
                return {
                    "ContentLength": len(self.data),
                    "ContentType": "application/octet-stream",
                }

            def get_object(self, **kwargs: object) -> dict[str, object]:
                assert kwargs["Bucket"] == "bucket"
                assert kwargs["Key"] == "tracks/01.flac"
                range_header = kwargs.get("Range")
                self.get_ranges.append(str(range_header) if range_header is not None else None)
                if isinstance(range_header, str) and range_header.startswith("bytes="):
                    start_text, end_text = range_header.removeprefix("bytes=").split("-", 1)
                    start = int(start_text)
                    end = int(end_text)
                    return {"Body": io.BytesIO(self.data[start : end + 1])}
                return {"Body": io.BytesIO(self.data)}

        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            remote = RemoteRootConfig(
                name="Remote",
                endpoint_url="https://s3.example.test",
                bucket="bucket",
                prefix="tracks/",
            )
            track_path = canonical_s3_path(remote, "tracks/01.flac")
            connection = connect_database(database)
            try:
                connection.execute(
                    """
                    INSERT INTO library_roots (
                        position, root_path, kind, source_json
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (0, remote.root_path, "s3", remote.source_json),
                )
                connection.execute(
                    """
                    INSERT INTO library_tracks (
                        track_id, root_position, path, file_type, title
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (7, 0, track_path, "flac", "Remote Track"),
                )
                connection.execute(
                    """
                    INSERT INTO library_track_sources (
                        track_id,
                        source_kind,
                        root_position,
                        canonical_path,
                        object_key,
                        content_type,
                        size_bytes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (7, "s3", 0, track_path, "tracks/01.flac", "audio/flac", 10),
                )
                connection.commit()
            finally:
                connection.close()

            runtime = self.make_runtime(database)
            fake_client = FakeS3Client(b"0123456789")
            with (
                patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime),
                patch("kukicha.player_media.create_s3_client", return_value=fake_client),
            ):
                app = create_player_app(self.make_options(temp_path))
                client = app.test_client()
                full_response = client.get("/audio/7")
                partial_response = client.get("/audio/7", headers={"Range": "bytes=2-5"})
                head_response = client.head("/audio/7", headers={"Range": "bytes=2-5"})

        self.assertEqual(full_response.status_code, 200)
        self.assertEqual(full_response.data, b"0123456789")
        self.assertEqual(full_response.headers["Content-Length"], "10")
        self.assertEqual(full_response.content_type, "audio/flac")
        self.assertEqual(partial_response.status_code, 206)
        self.assertEqual(partial_response.data, b"2345")
        self.assertEqual(partial_response.headers["Content-Range"], "bytes 2-5/10")
        self.assertEqual(head_response.status_code, 206)
        self.assertEqual(head_response.data, b"")
        self.assertEqual(head_response.headers["Content-Length"], "4")

    def test_playlist_audio_route_streams_tracked_remote_item(self) -> None:
        class FakeS3Client:
            def __init__(self, data: bytes) -> None:
                self.data = data

            def head_object(self, **kwargs: object) -> dict[str, object]:
                assert kwargs["Bucket"] == "bucket"
                assert kwargs["Key"] == "tracks/01.flac"
                return {
                    "ContentLength": len(self.data),
                    "ContentType": "application/octet-stream",
                }

            def get_object(self, **kwargs: object) -> dict[str, object]:
                assert kwargs["Bucket"] == "bucket"
                assert kwargs["Key"] == "tracks/01.flac"
                range_header = kwargs.get("Range")
                if isinstance(range_header, str) and range_header.startswith("bytes="):
                    start_text, end_text = range_header.removeprefix("bytes=").split("-", 1)
                    start = int(start_text)
                    end = int(end_text)
                    return {"Body": io.BytesIO(self.data[start : end + 1])}
                return {"Body": io.BytesIO(self.data)}

        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            remote = RemoteRootConfig(
                name="Remote",
                endpoint_url="https://s3.example.test",
                bucket="bucket",
                prefix="tracks/",
            )
            track_path = canonical_s3_path(remote, "tracks/01.flac")
            connection = connect_database(database)
            try:
                connection.execute(
                    """
                    INSERT INTO library_roots (
                        position, root_path, kind, source_json
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (0, remote.root_path, "s3", remote.source_json),
                )
                connection.execute(
                    """
                    INSERT INTO library_tracks (
                        track_id, root_position, path, file_type, title
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (7, 0, track_path, "flac", "Remote Track"),
                )
                connection.execute(
                    """
                    INSERT INTO library_track_sources (
                        track_id,
                        source_kind,
                        root_position,
                        canonical_path,
                        object_key,
                        content_type,
                        size_bytes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (7, "s3", 0, track_path, "tracks/01.flac", "audio/flac", 10),
                )
                connection.execute(
                    """
                    INSERT INTO library_playlists (
                        playlist_id, name, kind, source
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (3, "Remote Mix", "local", "manual"),
                )
                connection.execute(
                    """
                    INSERT INTO library_playlist_items (
                        playlist_item_id, playlist_id, position, path, track_id
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (12, 3, 0, track_path, 7),
                )
                connection.commit()
            finally:
                connection.close()

            runtime = self.make_runtime(database)
            fake_client = FakeS3Client(b"0123456789")
            with (
                patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime),
                patch("kukicha.player_media.create_s3_client", return_value=fake_client),
            ):
                app = create_player_app(self.make_options(temp_path))
                client = app.test_client()
                response = client.get(
                    "/playlist-audio/12",
                    headers={"Range": "bytes=2-5"},
                )

        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.data, b"2345")
        self.assertEqual(response.headers["Content-Range"], "bytes 2-5/10")
        self.assertEqual(response.content_type, "audio/flac")

    def test_job_events_stream_retries_and_unsubscribes_on_close(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            runtime = self.make_runtime(temp_path / "kukicha.sqlite")
            runtime.active_job_payloads.return_value = [
                {
                    "job_id": 4,
                    "kind": "sync",
                    "kind_label": "Sync",
                    "status": "running",
                    "status_label": "Running",
                    "message": "Sync running.",
                }
            ]
            with patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime):
                app = create_player_app(self.make_options(temp_path))

            response = app.test_client().get("/api/jobs/events", buffered=False)
            stream = iter(response.response)
            first_chunk = next(stream)
            second_chunk = next(stream)
            third_chunk = next(stream)
            fourth_chunk = next(stream)
            response.close()

            self.assertEqual(response.status_code, 200)
            self.assertEqual(first_chunk, b"retry: 1000\n\n")
            self.assertEqual(second_chunk, b"event: job\n")
            self.assertEqual(third_chunk, b"data: ")
            self.assertIn(b'"kind": "sync"', fourth_chunk)
            self.assertIn(b'"status": "running"', fourth_chunk)
            runtime.subscribe_jobs.assert_called_once()
            runtime.active_job_payloads.assert_called_once()
            runtime.unsubscribe_jobs.assert_called_once()


class PlayerRootMutationTest(unittest.TestCase):
    def track_snapshot_kwargs(self, path: Path) -> dict[str, object]:
        stat_result = path.stat()
        return {
            "file_modified_at_ns": stat_result.st_mtime_ns,
            "file_size_bytes": stat_result.st_size,
        }

    def fake_audio(
        self,
        *,
        artist: str = "Artist",
        album_artist: str = "Artist",
        album: str = "Album",
        title: str = "Track",
    ) -> SimpleNamespace:
        return SimpleNamespace(
            tags={
                "artist": [artist],
                "albumartist": [album_artist],
                "album": [album],
                "title": [title],
            },
            info=SimpleNamespace(length=123.0, bitrate=128000),
        )

    def test_sync_library_roots_noops_when_config_matches_database(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            root_a = (temp_path / "music-a").resolve()
            root_b = (temp_path / "music-b").resolve()
            root_a.mkdir()
            root_b.mkdir()
            connection = connect_database(database)
            try:
                connection.execute(
                    "INSERT INTO library_roots (position, root_path) VALUES (?, ?)",
                    (0, str(root_a)),
                )
                connection.execute(
                    "INSERT INTO library_roots (position, root_path) VALUES (?, ?)",
                    (2, str(root_b)),
                )
                connection.commit()
            finally:
                connection.close()

            with patch("kukicha.use_case.commands.roots.build_incremental_library") as build_library:
                result = sync_library_roots(database, (root_a, root_b))

            self.assertFalse(result.changed)
            self.assertEqual(result.roots_configured, 2)
            self.assertEqual(result.roots_added, 0)
            self.assertEqual(result.roots_removed, 0)
            self.assertEqual(result.roots_scanned, 0)
            build_library.assert_not_called()

    def test_sync_library_roots_adds_new_roots_and_preserves_positions(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            root_a = (temp_path / "music-a").resolve()
            root_b = (temp_path / "music-b").resolve()
            root_c = (temp_path / "music-c").resolve()
            root_a.mkdir()
            root_b.mkdir()
            root_c.mkdir()
            connection = connect_database(database)
            try:
                connection.execute(
                    "INSERT INTO library_roots (position, root_path) VALUES (?, ?)",
                    (0, str(root_a)),
                )
                connection.execute(
                    "INSERT INTO library_roots (position, root_path) VALUES (?, ?)",
                    (2, str(root_b)),
                )
                connection.commit()
            finally:
                connection.close()

            scanned_library = MusicLibrary(
                roots=[str(root_a), str(root_b), str(root_c)],
                tracks=[
                    TrackRecord(
                        path=str(root_a / "Artist A" / "Album A" / "01.flac"),
                        root_position=0,
                        file_type="flac",
                        artist="Artist A",
                        album_artist="Artist A",
                        album="Album A",
                        title="Track A",
                    ),
                    TrackRecord(
                        path=str(root_b / "Artist B" / "Album B" / "01.flac"),
                        root_position=1,
                        file_type="flac",
                        artist="Artist B",
                        album_artist="Artist B",
                        album="Album B",
                        title="Track B",
                    ),
                    TrackRecord(
                        path=str(root_c / "Artist C" / "Album C" / "01.flac"),
                        root_position=2,
                        file_type="flac",
                        artist="Artist C",
                        album_artist="Artist C",
                        album="Album C",
                        title="Track C",
                    ),
                ],
                supported_extensions=[".flac"],
                generated_at="2026-04-21T12:00:00+00:00",
            )

            def fake_build_library(roots: object, *_args: object, **_kwargs: object) -> SimpleNamespace:
                self.assertEqual([str(root.path) for root in roots], [str(root_a), str(root_b), str(root_c)])
                return SimpleNamespace(library=scanned_library)

            with (
                patch("kukicha.use_case.commands.roots.build_incremental_library", side_effect=fake_build_library),
                patch("kukicha.use_case.commands.roots.resolve_library_genres", return_value=None),
                patch("kukicha.use_case.commands.roots.resolve_library_cover_art", return_value=None),
            ):
                result = sync_library_roots(database, (root_a, root_b, root_c))

            self.assertTrue(result.changed)
            self.assertEqual(result.roots_added, 1)
            self.assertEqual(result.roots_removed, 0)
            connection = connect_database(database, create=False)
            try:
                roots = list(connection.execute("SELECT position, root_path FROM library_roots ORDER BY position"))
                self.assertEqual(
                    [(int(row["position"]), str(row["root_path"])) for row in roots],
                    [(0, str(root_a)), (2, str(root_b)), (3, str(root_c))],
                )
                tracks = list(
                    connection.execute(
                        "SELECT root_position, path FROM library_tracks ORDER BY root_position, path"
                    )
                )
                self.assertEqual(
                    [(int(row["root_position"]), str(row["path"])) for row in tracks],
                    [
                        (0, str(root_a / "Artist A" / "Album A" / "01.flac")),
                        (2, str(root_b / "Artist B" / "Album B" / "01.flac")),
                        (3, str(root_c / "Artist C" / "Album C" / "01.flac")),
                    ],
                )
            finally:
                connection.close()

    def test_sync_library_roots_removes_roots_and_rescans_remaining_roots(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            root_a = (temp_path / "music-a").resolve()
            root_b = (temp_path / "music-b").resolve()
            root_a.mkdir()
            root_b.mkdir()
            connection = connect_database(database)
            try:
                connection.execute(
                    "INSERT INTO library_roots (position, root_path) VALUES (?, ?)",
                    (0, str(root_a)),
                )
                connection.execute(
                    "INSERT INTO library_roots (position, root_path) VALUES (?, ?)",
                    (1, str(root_b)),
                )
                insert_library_album(connection, "old::album", "Old", "Album", 2000, 1)
                connection.execute(
                    """
                    INSERT INTO library_tracks (
                        album_id, root_position, path, album_artist, artist, album, title
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    ("old::album", 0, str(root_a / "old.flac"), "Old", "Old", "Album", "Old"),
                )
                connection.commit()
            finally:
                connection.close()

            scanned_library = MusicLibrary(
                roots=[str(root_b)],
                tracks=[
                    TrackRecord(
                        path=str(root_b / "Artist B" / "Album B" / "01.flac"),
                        root_position=0,
                        file_type="flac",
                        artist="Artist B",
                        album_artist="Artist B",
                        album="Album B",
                        title="Track B",
                    )
                ],
                supported_extensions=[".flac"],
                generated_at="2026-04-21T12:00:00+00:00",
            )

            with (
                patch(
                    "kukicha.use_case.commands.roots.build_incremental_library",
                    return_value=SimpleNamespace(library=scanned_library),
                ),
                patch("kukicha.use_case.commands.roots.resolve_library_genres", return_value=None),
                patch("kukicha.use_case.commands.roots.resolve_library_cover_art", return_value=None),
            ):
                result = sync_library_roots(database, (root_b,))

            self.assertTrue(result.changed)
            self.assertEqual(result.roots_added, 0)
            self.assertEqual(result.roots_removed, 1)
            connection = connect_database(database, create=False)
            try:
                roots = list(connection.execute("SELECT position, root_path FROM library_roots ORDER BY position"))
                self.assertEqual(
                    [(int(row["position"]), str(row["root_path"])) for row in roots],
                    [(1, str(root_b))],
                )
                tracks = list(connection.execute("SELECT root_position, path FROM library_tracks"))
                self.assertEqual([(int(row["root_position"]), str(row["path"])) for row in tracks], [(1, str(root_b / "Artist B" / "Album B" / "01.flac"))])
            finally:
                connection.close()

    def test_sync_library_roots_empty_config_clears_library_data(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            connection = connect_database(database)
            try:
                connection.execute("INSERT INTO library_roots (position, root_path) VALUES (?, ?)", (0, "/music/a"))
                insert_library_album(connection, "artist::album", "Artist", "Album", 2000, 1)
                connection.execute(
                    """
                    INSERT INTO library_tracks (
                        album_id, root_position, path, album_artist, artist, album, title
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    ("artist::album", 0, "/music/a/01.flac", "Artist", "Artist", "Album", "Track"),
                )
                connection.commit()
            finally:
                connection.close()

            result = sync_library_roots(database, ())

            self.assertTrue(result.changed)
            self.assertEqual(result.roots_removed, 1)
            connection = connect_database(database, create=False)
            try:
                self.assertEqual(int(connection.execute("SELECT COUNT(*) AS count FROM library_roots").fetchone()["count"]), 0)
                self.assertEqual(int(connection.execute("SELECT COUNT(*) AS count FROM library_tracks").fetchone()["count"]), 0)
                self.assertEqual(int(connection.execute("SELECT COUNT(*) AS count FROM library_albums").fetchone()["count"]), 0)
            finally:
                connection.close()

    def test_sync_library_roots_rolls_back_on_failure(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            root_a = (temp_path / "music-a").resolve()
            root_b = (temp_path / "music-b").resolve()
            root_a.mkdir()
            root_b.mkdir()
            connection = connect_database(database)
            try:
                connection.execute("INSERT INTO library_roots (position, root_path) VALUES (?, ?)", (0, str(root_a)))
                insert_library_album(connection, "artist::album", "Artist", "Album", 2000, 1)
                connection.execute(
                    """
                    INSERT INTO library_tracks (
                        album_id, root_position, path, album_artist, artist, album, title
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    ("artist::album", 0, str(root_a / "01.flac"), "Artist", "Artist", "Album", "Track"),
                )
                connection.commit()
            finally:
                connection.close()

            scanned_library = MusicLibrary(
                roots=[str(root_a), str(root_b)],
                tracks=[
                    TrackRecord(
                        path=str(root_b / "replacement.flac"),
                        root_position=1,
                        file_type="flac",
                        artist="New",
                        album_artist="New",
                        album="Album",
                        title="Replacement",
                    )
                ],
                supported_extensions=[".flac"],
                generated_at="2026-04-21T12:00:00+00:00",
            )

            def failing_resolve(*_args: object, **kwargs: object) -> None:
                connection = kwargs.get("connection")
                assert connection is not None
                connection.execute(
                    """
                    INSERT INTO musicbrainz_entity_cache (
                        entity_type, mbid, fetched_at, endpoint_url, response_json
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    ("release", "release-1", "2026-04-21T12:00:00Z", "https://example.test/release-1", "{}"),
                )
                raise RuntimeError("boom")

            with (
                patch(
                    "kukicha.use_case.commands.roots.build_incremental_library",
                    return_value=SimpleNamespace(library=scanned_library),
                ),
                patch("kukicha.use_case.commands.roots.resolve_library_genres", side_effect=failing_resolve),
            ):
                with self.assertRaisesRegex(RuntimeError, "boom"):
                    sync_library_roots(database, (root_a, root_b))

            connection = connect_database(database, create=False)
            try:
                roots = list(connection.execute("SELECT position, root_path FROM library_roots ORDER BY position"))
                self.assertEqual([(int(row["position"]), str(row["root_path"])) for row in roots], [(0, str(root_a))])
                tracks = list(connection.execute("SELECT root_position, path FROM library_tracks"))
                self.assertEqual([(int(row["root_position"]), str(row["path"])) for row in tracks], [(0, str(root_a / "01.flac"))])
                self.assertEqual(int(connection.execute("SELECT COUNT(*) AS count FROM musicbrainz_entity_cache").fetchone()["count"]), 0)
            finally:
                connection.close()

    def test_sync_library_roots_cancellation_leaves_database_unchanged(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            root_a = (temp_path / "music-a").resolve()
            root_b = (temp_path / "music-b").resolve()
            root_a.mkdir()
            root_b.mkdir()
            connection = connect_database(database)
            try:
                connection.execute("INSERT INTO library_roots (position, root_path) VALUES (?, ?)", (0, str(root_a)))
                connection.commit()
            finally:
                connection.close()

            scanned_library = MusicLibrary(
                roots=[str(root_a), str(root_b)],
                tracks=[],
                supported_extensions=[".flac"],
                generated_at="2026-04-21T12:00:00+00:00",
            )

            for cancel_at, expected_build_calls in ((1, 0), (3, 1)):
                with self.subTest(cancel_at=cancel_at):
                    calls = {"count": 0}

                    def cancel_check() -> None:
                        calls["count"] += 1
                        if calls["count"] == cancel_at:
                            raise PlayerJobCanceled("Canceled by user.")

                    with patch(
                        "kukicha.use_case.commands.roots.build_incremental_library",
                        return_value=SimpleNamespace(library=scanned_library),
                    ) as build_library:
                        with self.assertRaises(PlayerJobCanceled):
                            sync_library_roots(database, (root_a, root_b), cancel_check=cancel_check)

                    self.assertEqual(build_library.call_count, expected_build_calls)
                    connection = connect_database(database, create=False)
                    try:
                        roots = list(connection.execute("SELECT position, root_path FROM library_roots ORDER BY position"))
                        self.assertEqual([(int(row["position"]), str(row["root_path"])) for row in roots], [(0, str(root_a))])
                    finally:
                        connection.close()

    def test_rescan_library_skips_unchanged_track_metadata_rows(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            root = (temp_path / "music").resolve()
            track_path = root / "Artist" / "Album" / "01.flac"
            track_path.parent.mkdir(parents=True)
            track_path.write_bytes(b"stable audio")
            save_library(
                MusicLibrary(
                    roots=[str(root)],
                    tracks=[
                        TrackRecord(
                            path=str(track_path),
                            root_position=0,
                            file_type="flac",
                            artist="Artist",
                            album_artist="Artist",
                            album="Album",
                            title="Stored Title",
                            genres=["Electronic"],
                            **self.track_snapshot_kwargs(track_path),
                        )
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-04-21T12:00:00+00:00",
                ),
                database,
            )
            connection = connect_database(database, create=False)
            try:
                original_track_id = int(
                    connection.execute(
                        "SELECT track_id FROM library_tracks WHERE path = ?",
                        (str(track_path),),
                    ).fetchone()["track_id"]
                )
            finally:
                connection.close()

            with patch("kukicha.scanner.MutagenFile", side_effect=AssertionError("unexpected scan")):
                result = rescan_library(database)

            self.assertEqual(result.tracks_scanned, 1)
            connection = connect_database(database, create=False)
            try:
                row = connection.execute(
                    "SELECT track_id, title FROM library_tracks WHERE path = ?",
                    (str(track_path),),
                ).fetchone()
                self.assertEqual(int(row["track_id"]), original_track_id)
                self.assertEqual(str(row["title"]), "Stored Title")
            finally:
                connection.close()

    def test_rescan_library_reports_persisted_album_count_for_reused_musicbrainz_variants(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            root_a = (temp_path / "music-a").resolve()
            root_b = (temp_path / "music-b").resolve()
            path_a = root_a / "Artist" / "Album" / "01.flac"
            path_b = root_b / "Artist" / "Album" / "01.flac"
            path_a.parent.mkdir(parents=True)
            path_b.parent.mkdir(parents=True)
            path_a.write_bytes(b"root a audio")
            path_b.write_bytes(b"root b audio")
            save_library(
                MusicLibrary(
                    roots=[str(root_a), str(root_b)],
                    tracks=[
                        TrackRecord(
                            path=str(path_a),
                            root_position=0,
                            file_type="flac",
                            artist="Artist",
                            album_artist="Artist",
                            album="Album",
                            title="Root A",
                            musicbrainz_release_variant="aaa",
                            **self.track_snapshot_kwargs(path_a),
                        ),
                        TrackRecord(
                            path=str(path_b),
                            root_position=1,
                            file_type="flac",
                            artist="Artist",
                            album_artist="Artist",
                            album="Album",
                            title="Root B",
                            musicbrainz_release_variant="bbb",
                            **self.track_snapshot_kwargs(path_b),
                        ),
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-04-21T12:00:00+00:00",
                ),
                database,
            )

            with patch("kukicha.scanner.MutagenFile", side_effect=AssertionError("unexpected scan")):
                result = rescan_library(database)

            self.assertEqual(result.tracks_scanned, 2)
            self.assertEqual(result.albums_scanned, 2)
            connection = connect_database(database, create=False)
            try:
                self.assertEqual(
                    int(connection.execute("SELECT COUNT(*) AS count FROM library_albums").fetchone()["count"]),
                    2,
                )
            finally:
                connection.close()

    def test_rescan_library_refreshes_same_path_when_file_snapshot_changes(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            root = (temp_path / "music").resolve()
            track_path = root / "Artist" / "Album" / "01.flac"
            track_path.parent.mkdir(parents=True)
            track_path.write_bytes(b"old audio")
            save_library(
                MusicLibrary(
                    roots=[str(root)],
                    tracks=[
                        TrackRecord(
                            path=str(track_path),
                            root_position=0,
                            file_type="flac",
                            artist="Artist",
                            album_artist="Artist",
                            album="Album",
                            title="Old Title",
                            **self.track_snapshot_kwargs(track_path),
                        )
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-04-21T12:00:00+00:00",
                ),
                database,
            )
            original_stat = track_path.stat()
            track_path.write_bytes(b"new audio bytes")
            os.utime(
                track_path,
                ns=(
                    original_stat.st_mtime_ns + 1_000_000_000,
                    original_stat.st_mtime_ns + 1_000_000_000,
                ),
            )
            connection = connect_database(database, create=False)
            try:
                original_track_id = int(
                    connection.execute(
                        "SELECT track_id FROM library_tracks WHERE path = ?",
                        (str(track_path),),
                    ).fetchone()["track_id"]
                )
            finally:
                connection.close()

            with (
                patch("kukicha.scanner.MutagenFile", return_value=self.fake_audio(title="New Title")) as mutagen_file,
                patch("kukicha.use_case.commands.roots.resolve_library_genres", return_value=None),
                patch("kukicha.use_case.commands.roots.resolve_library_cover_art", return_value=None),
            ):
                rescan_library(database)

            self.assertGreaterEqual(mutagen_file.call_count, 1)
            connection = connect_database(database, create=False)
            try:
                row = connection.execute(
                    """
                    SELECT track_id, title, file_modified_at_ns, file_size_bytes
                    FROM library_tracks
                    WHERE path = ?
                    """,
                    (str(track_path),),
                ).fetchone()
                self.assertEqual(int(row["track_id"]), original_track_id)
                self.assertEqual(str(row["title"]), "New Title")
                self.assertEqual(int(row["file_modified_at_ns"]), track_path.stat().st_mtime_ns)
                self.assertEqual(int(row["file_size_bytes"]), track_path.stat().st_size)
            finally:
                connection.close()

    def test_rescan_library_prunes_stale_paths_without_rewriting_unchanged_tracks(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            root = (temp_path / "music").resolve()
            keep_path = root / "Artist" / "Keep" / "01.flac"
            stale_path = root / "Artist" / "Gone" / "01.flac"
            keep_path.parent.mkdir(parents=True)
            stale_path.parent.mkdir(parents=True)
            keep_path.write_bytes(b"keep audio")
            stale_path.write_bytes(b"gone audio")
            save_library(
                MusicLibrary(
                    roots=[str(root)],
                    tracks=[
                        TrackRecord(
                            path=str(keep_path),
                            root_position=0,
                            file_type="flac",
                            artist="Artist",
                            album_artist="Artist",
                            album="Keep",
                            title="Keep",
                            **self.track_snapshot_kwargs(keep_path),
                        ),
                        TrackRecord(
                            path=str(stale_path),
                            root_position=0,
                            file_type="flac",
                            artist="Artist",
                            album_artist="Artist",
                            album="Gone",
                            title="Gone",
                            **self.track_snapshot_kwargs(stale_path),
                        ),
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-04-21T12:00:00+00:00",
                ),
                database,
            )
            stale_path.unlink()
            connection = connect_database(database, create=False)
            try:
                keep_track_id = int(
                    connection.execute(
                        "SELECT track_id FROM library_tracks WHERE path = ?",
                        (str(keep_path),),
                    ).fetchone()["track_id"]
                )
            finally:
                connection.close()

            with (
                patch("kukicha.scanner.MutagenFile", side_effect=AssertionError("unexpected scan")),
                patch("kukicha.use_case.commands.roots.resolve_library_genres", return_value=None),
                patch("kukicha.use_case.commands.roots.resolve_library_cover_art", return_value=None),
                patch("kukicha.use_case.commands.roots.LOGGER") as logger,
            ):
                rescan_library(database)

            progress_logs = [
                str(args[1])
                for args, _kwargs in logger.info.call_args_list
                if len(args) >= 2
            ]
            self.assertIn(
                f"rescan progress: pruning stale track: {stale_path}",
                progress_logs,
            )
            self.assertNotIn(str(keep_path), "\n".join(progress_logs))

            connection = connect_database(database, create=False)
            try:
                tracks = list(
                    connection.execute(
                        "SELECT track_id, path FROM library_tracks ORDER BY path"
                    )
                )
                self.assertEqual(
                    [(int(row["track_id"]), str(row["path"])) for row in tracks],
                    [(keep_track_id, str(keep_path))],
                )
                self.assertIsNone(
                    connection.execute(
                        "SELECT 1 FROM library_albums WHERE album = ?",
                        ("Gone",),
                    ).fetchone()
                )
                self.assertEqual(
                    int(connection.execute("SELECT COUNT(*) AS count FROM library_album_search").fetchone()["count"]),
                    1,
                )
            finally:
                connection.close()

    def test_rescan_library_preserves_existing_db_playlist_contents(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            root = (temp_path / "music").resolve()
            track_path = root / "Artist" / "Album" / "01.flac"
            playlist_path = root / "mix.m3u8"
            track_path.parent.mkdir(parents=True)
            track_path.write_bytes(b"stable audio")
            playlist_path.write_text("Artist/Album/01.flac\n", encoding="utf-8")
            save_library(
                MusicLibrary(
                    roots=[str(root)],
                    tracks=[
                        TrackRecord(
                            path=str(track_path),
                            root_position=0,
                            file_type="flac",
                            artist="Artist",
                            album_artist="Artist",
                            album="Album",
                            title="Track",
                            **self.track_snapshot_kwargs(track_path),
                        )
                    ],
                    playlists=[
                        PlaylistRecord(
                            path=str(playlist_path),
                            name="mix",
                            root_position=0,
                            items=[PlaylistItemRecord(path=str(track_path))],
                        )
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-04-21T12:00:00+00:00",
                ),
                database,
            )
            playlist_path.write_text(
                "#EXTM3U\n#EXTINF:123,Stream Title\nhttps://example.test/stream.mp3\n",
                encoding="utf-8",
            )

            with patch("kukicha.scanner.MutagenFile", side_effect=AssertionError("unexpected scan")):
                rescan_library(database)

            connection = connect_database(database, create=False)
            try:
                row = connection.execute(
                    """
                    SELECT items.path, items.track_id, items.title, items.duration_seconds
                    FROM library_playlist_items AS items
                    WHERE items.playlist_id = ?
                    """,
                    (1,),
                ).fetchone()
                self.assertEqual(str(row["path"]), str(track_path))
                self.assertEqual(int(row["track_id"]), 1)
                self.assertIsNone(row["title"])
                self.assertIsNone(row["duration_seconds"])
            finally:
                connection.close()

    def test_rescan_library_refreshes_when_sidecar_cover_snapshot_changes(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            root = (temp_path / "music").resolve()
            track_path = root / "Artist" / "Album" / "01.flac"
            cover_path = root / "Artist" / "Album" / "cover.jpg"
            track_path.parent.mkdir(parents=True)
            track_path.write_bytes(b"stable audio")
            save_library(
                MusicLibrary(
                    roots=[str(root)],
                    tracks=[
                        TrackRecord(
                            path=str(track_path),
                            root_position=0,
                            file_type="flac",
                            artist="Artist",
                            album_artist="Artist",
                            album="Album",
                            title="Track",
                            **self.track_snapshot_kwargs(track_path),
                        )
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-04-21T12:00:00+00:00",
                ),
                database,
            )
            cover_path.write_bytes(b"fake image bytes")
            artwork = TrackArtwork(mime_type="image/jpeg", data=b"thumb")

            def fake_thumbnail_artworks(_artwork: object, *, heights: object) -> dict[int, TrackArtwork]:
                return {int(height): artwork for height in heights}

            with (
                patch("kukicha.scanner.MutagenFile", return_value=self.fake_audio()) as mutagen_file,
                patch("kukicha.scanner.thumbnail_artworks", side_effect=fake_thumbnail_artworks),
                patch("kukicha.use_case.commands.roots.resolve_library_genres", return_value=None),
                patch("kukicha.use_case.commands.roots.resolve_library_cover_art", return_value=None),
            ):
                rescan_library(database)

            self.assertGreaterEqual(mutagen_file.call_count, 1)
            connection = connect_database(database, create=False)
            try:
                row = connection.execute(
                    """
                    SELECT tracks.sidecar_artwork_path, artwork.data
                    FROM library_tracks AS tracks
                    JOIN library_track_artwork AS artwork
                        ON artwork.track_id = tracks.track_id
                    WHERE tracks.path = ?
                        AND artwork.height_px = ?
                    """,
                    (str(track_path), 32),
                ).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(str(row["sidecar_artwork_path"]), str(cover_path))
                self.assertEqual(bytes(row["data"]), b"thumb")
            finally:
                connection.close()

    def test_rescan_library_scans_all_roots_and_preserves_root_positions(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            connection = connect_database(database)
            try:
                connection.execute(
                    "INSERT INTO library_roots (position, root_path) VALUES (?, ?)",
                    (0, "/music/a"),
                )
                connection.execute(
                    "INSERT INTO library_roots (position, root_path) VALUES (?, ?)",
                    (2, "/music/b"),
                )
                insert_library_album(
                    connection,
                    "old::album",
                    "Old",
                    "Album",
                    2002,
                    1,
                )
                connection.execute(
                    """
                    INSERT INTO library_tracks (
                        album_id, root_position, path, album_artist, artist, album, title, date
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "old::album",
                        2,
                        "/music/b/Old/Album/01.flac",
                        "Old",
                        "Old",
                        "Album",
                        "Old Track",
                        "2002",
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO library_playlists (
                        playlist_id, name, kind, source, cover_svg, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        1,
                        "Root B Mix",
                        "local",
                        "manual",
                        "",
                        "2026-04-20T12:00:00+00:00",
                        "2026-04-20T12:00:00+00:00",
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            rescanned_library = MusicLibrary(
                roots=["/music/a", "/music/b"],
                tracks=[
                    TrackRecord(
                        path="/music/a/Artist A/Album A/01.flac",
                        root_position=0,
                        file_type="flac",
                        artist="Artist A",
                        album_artist="Artist A",
                        album="Album A",
                        title="Track A",
                        date="2003",
                        genres=["Electronic"],
                    ),
                    TrackRecord(
                        path="/music/b/Artist B/Album B/01.flac",
                        root_position=1,
                        file_type="flac",
                        artist="Artist B",
                        album_artist="Artist B",
                        album="Album B",
                        title="Track B",
                        date="2004",
                        genres=["Electronic"],
                    ),
                ],
                supported_extensions=[".flac"],
                generated_at="2026-04-21T12:00:00+00:00",
            )

            def fake_build_library(roots: object, *_args: object, **_kwargs: object) -> SimpleNamespace:
                self.assertEqual([str(root.path) for root in roots], ["/music/a", "/music/b"])
                return SimpleNamespace(
                    library=rescanned_library,
                    scanned_paths=frozenset(track.path for track in rescanned_library.tracks),
                    reused_paths=frozenset(),
                )

            with (
                patch("kukicha.use_case.commands.roots.build_incremental_library", side_effect=fake_build_library),
                patch("kukicha.use_case.commands.roots.resolve_library_genres", return_value=None),
                patch("kukicha.use_case.commands.roots.resolve_library_cover_art", return_value=None),
            ):
                result = rescan_library(database)

            self.assertEqual(result.roots_scanned, 2)
            self.assertEqual(result.tracks_scanned, 2)
            self.assertEqual(result.albums_scanned, 2)

            connection = connect_database(database)
            try:
                roots = list(connection.execute("SELECT position, root_path FROM library_roots ORDER BY position"))
                self.assertEqual(
                    [(int(row["position"]), str(row["root_path"])) for row in roots],
                    [(0, "/music/a"), (2, "/music/b")],
                )
                tracks = list(
                    connection.execute(
                        "SELECT root_position, path FROM library_tracks ORDER BY root_position, path"
                    )
                )
                self.assertEqual(
                    [(int(row["root_position"]), str(row["path"])) for row in tracks],
                    [
                        (0, "/music/a/Artist A/Album A/01.flac"),
                        (2, "/music/b/Artist B/Album B/01.flac"),
                    ],
                )
                self.assertIsNone(
                    connection.execute(
                        "SELECT 1 FROM library_tracks WHERE path = ?",
                        ("/music/b/Old/Album/01.flac",),
                    ).fetchone()
                )

                album_b = connection.execute(
                    "SELECT track_count FROM library_albums WHERE album = ?",
                    ("Album B",),
                ).fetchone()
                self.assertIsNotNone(album_b)
                self.assertEqual(int(album_b["track_count"]), 1)
                self.assertIsNotNone(
                    connection.execute(
                        "SELECT 1 FROM library_albums WHERE album = ?",
                        ("Album A",),
                    ).fetchone()
                )
                self.assertIsNone(
                    connection.execute(
                        "SELECT 1 FROM library_albums WHERE album_id = ?",
                        ("old::album",),
                    ).fetchone()
                )
                self.assertEqual(
                    int(connection.execute("SELECT COUNT(*) AS count FROM library_album_search").fetchone()["count"]),
                    2,
                )
                playlist = connection.execute(
                    """
                    SELECT name, source, created_at
                    FROM library_playlists
                    WHERE playlist_id = 1
                    """
                ).fetchone()
                self.assertIsNotNone(playlist)
                self.assertEqual(str(playlist["name"]), "Root B Mix")
                self.assertEqual(str(playlist["source"]), "manual")
                self.assertEqual(str(playlist["created_at"]), "2026-04-20T12:00:00+00:00")
                stats = list(
                    connection.execute(
                        """
                        SELECT root_position, tracks_scanned, albums_scanned
                        FROM library_root_stats
                        ORDER BY root_position
                        """
                    )
                )
                self.assertEqual(
                    [
                        (
                            int(row["root_position"]),
                            int(row["tracks_scanned"]),
                            int(row["albums_scanned"]),
                        )
                        for row in stats
                    ],
                    [
                        (0, 1, 1),
                        (2, 1, 1),
                    ],
                )
                total_stats = connection.execute(
                    """
                    SELECT tracks_scanned, albums_scanned
                    FROM library_stats
                    WHERE stats_id = 1
                    """
                ).fetchone()
                self.assertIsNotNone(total_stats)
                self.assertEqual(
                    (
                        int(total_stats["tracks_scanned"]),
                        int(total_stats["albums_scanned"]),
                    ),
                    (2, 2),
                )
            finally:
                connection.close()

    def test_rescan_library_rolls_back_all_changes_on_failure(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            connection = connect_database(database)
            try:
                connection.execute(
                    "INSERT INTO library_roots (position, root_path) VALUES (?, ?)",
                    (0, "/music/a"),
                )
                insert_library_album(
                    connection,
                    "artist::album",
                    "Artist",
                    "Album",
                    2000,
                    1,
                )
                connection.execute(
                    """
                    INSERT INTO library_tracks (
                        album_id, root_position, path, album_artist, artist, album, title, date
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "artist::album",
                        0,
                        "/music/a/Artist/Album/01.flac",
                        "Artist",
                        "Artist",
                        "Album",
                        "Track",
                        "2000",
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            rescanned_library = MusicLibrary(
                roots=["/music/a"],
                tracks=[
                    TrackRecord(
                        path="/music/a/Artist/Album/02.flac",
                        root_position=0,
                        file_type="flac",
                        artist="Artist",
                        album_artist="Artist",
                        album="Album",
                        title="Replacement",
                        date="2001",
                    ),
                ],
                supported_extensions=[".flac"],
                generated_at="2026-04-21T12:00:00+00:00",
            )

            def failing_resolve(*_args: object, **kwargs: object) -> None:
                connection = kwargs.get("connection")
                assert connection is not None
                connection.execute(
                    """
                    INSERT INTO musicbrainz_entity_cache (
                        entity_type, mbid, fetched_at, endpoint_url, response_json
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    ("release", "release-1", "2026-04-21T12:00:00Z", "https://example.test/release-1", "{}"),
                )
                raise RuntimeError("boom")

            with (
                patch(
                    "kukicha.use_case.commands.roots.build_incremental_library",
                    return_value=SimpleNamespace(
                        library=rescanned_library,
                        scanned_paths=frozenset(track.path for track in rescanned_library.tracks),
                        reused_paths=frozenset(),
                    ),
                ),
                patch("kukicha.use_case.commands.roots.resolve_library_genres", side_effect=failing_resolve),
            ):
                with self.assertRaisesRegex(RuntimeError, "boom"):
                    rescan_library(database)

            connection = connect_database(database)
            try:
                self.assertEqual(
                    int(connection.execute("SELECT COUNT(*) AS count FROM library_tracks").fetchone()["count"]),
                    1,
                )
                self.assertIsNotNone(
                    connection.execute(
                        "SELECT 1 FROM library_tracks WHERE path = ?",
                        ("/music/a/Artist/Album/01.flac",),
                    ).fetchone()
                )
                self.assertEqual(
                    int(connection.execute("SELECT COUNT(*) AS count FROM musicbrainz_entity_cache").fetchone()["count"]),
                    0,
                )
                stats = list(
                    connection.execute(
                        """
                        SELECT root_position, tracks_scanned, albums_scanned
                        FROM library_root_stats
                        ORDER BY root_position
                        """
                    )
                )
                self.assertEqual(
                    [
                        (
                            int(row["root_position"]),
                            int(row["tracks_scanned"]),
                            int(row["albums_scanned"]),
                        )
                        for row in stats
                    ],
                    [(0, 1, 1)],
                )
                total_stats = connection.execute(
                    """
                    SELECT tracks_scanned, albums_scanned
                    FROM library_stats
                    WHERE stats_id = 1
                    """
                ).fetchone()
                self.assertIsNotNone(total_stats)
                self.assertEqual(
                    (
                        int(total_stats["tracks_scanned"]),
                        int(total_stats["albums_scanned"]),
                    ),
                    (1, 1),
                )
            finally:
                connection.close()

    def test_rescan_library_rejects_empty_root_set(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            connection = connect_database(database)
            connection.close()

            with self.assertRaisesRegex(ValueError, "no roots configured"):
                rescan_library(database)



class PlayerJobLogTest(unittest.TestCase):
    def test_library_job_summary_text_formats_info_log_message(self) -> None:
        self.assertEqual(
            library_job_summary_text(
                "add and scan",
                "/music/a",
                tracks_scanned=12,
                albums_scanned=3,
                duration_seconds=4.125,
            ),
            "add and scan completed for /music/a (tracks=12, albums=3, duration=4.12s)",
        )

    def test_library_scan_progress_text_formats_progress_log_message(self) -> None:
        self.assertEqual(
            library_scan_progress_text("rescan", "scanned 500 music files"),
            "rescan progress: scanned 500 music files",
        )

    def test_library_job_detail_lines_include_scan_and_resolution_stats(self) -> None:
        self.assertEqual(
            library_job_detail_lines(
                tracks_scanned=12,
                albums_scanned=3,
                genre_resolution=GenreResolutionStats(
                    exact_genre_matches=4,
                    exact_style_matches=5,
                    fuzzy_genre_matches=6,
                    fuzzy_style_matches=7,
                    unmatched=8,
                    unknown_albums=9,
                    unknown_tracks=10,
                    musicbrainz_api_calls=11,
                    musicbrainz_cached_calls=12,
                    musicbrainz_rate_limit_retries=13,
                    musicbrainz_fetch_failures=14,
                    musicbrainz_album_overrides=15,
                    musicbrainz_unmatched_genres=16,
                ),
                cover_art_resolution=CoverArtResolutionStats(
                    itunes_lookup_api_calls=17,
                    itunes_lookup_cached_calls=18,
                    metadata_api_calls=19,
                    metadata_cached_calls=20,
                    image_downloads=21,
                    image_cached_calls=22,
                    fetch_failures=23,
                    missing_art=24,
                    album_cover_overrides=25,
                    tracks_updated=26,
                ),
            ),
            (
                "tracks in library: 12",
                "albums in library: 3",
                "exact genre matches: 4",
                "exact style matches: 5",
                "fuzzy genre matches: 6",
                "fuzzy style matches: 7",
                "unmatched genre terms: 8",
                "albums set to __Unknown: 9",
                "tracks set to __Unknown: 10",
                "musicbrainz api calls: 11",
                "musicbrainz cached calls: 12",
                "musicbrainz rate-limit retries: 13",
                "musicbrainz fetch failures: 14",
                "musicbrainz album overrides: 15",
                "unmatched musicbrainz genres: 16",
                "itunes lookup api calls: 17",
                "itunes lookup cached calls: 18",
                "cover art metadata api calls: 19",
                "cover art metadata cached calls: 20",
                "cover art image downloads: 21",
                "cover art image cached calls: 22",
                "cover art fetch failures: 23",
                "cover art missing: 24",
                "cover art album overrides: 25",
                "cover art tracks updated: 26",
            ),
        )

    def test_library_job_detail_lines_describe_incremental_rescan_fast_path(self) -> None:
        self.assertEqual(
            library_job_detail_lines(
                tracks_scanned=5_475,
                albums_scanned=407,
                audio_files_checked=5_475,
                audio_files_read=0,
                audio_files_reused=5_475,
                stale_tracks_pruned=0,
                metadata_resolution_skipped=True,
                genre_resolution=GenreResolutionStats(),
                cover_art_resolution=CoverArtResolutionStats(),
            ),
            (
                "tracks in library: 5475",
                "albums in library: 407",
                "audio files checked: 5475",
                "audio files read: 0",
                "audio files reused: 5475",
                "stale tracks pruned: 0",
                "metadata resolution skipped: no audio file changes",
            ),
        )

    def test_player_jobs_list_newest_first_and_format_payload(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            connection = connect_database(database)
            connection.close()

            with patch(
                "kukicha.use_case.commands.jobs.utc_now_iso",
                return_value="2026-04-21T10:00:00Z",
            ):
                queued = create_player_job(
                    database,
                    kind="delete_root",
                    message="Delete queued for /music/a.",
                    context={"path": "/music/a", "root_position": 0},
                )
                succeeded = create_player_job(
                    database,
                    kind="delete_root",
                    message="Delete queued for /music/a.",
                    context={
                        "path": "/music/a",
                        "root_position": 0,
                    },
                )
                succeeded = update_player_job(
                    database,
                    succeeded.job_id,
                    status="succeeded",
                    message="Delete completed for /music/a.",
                    context={
                        "path": "/music/a",
                        "root_position": 0,
                        "duration_seconds": 1.25,
                    },
                    finished_at="2026-04-21T10:00:00Z",
                )

            jobs = list_player_jobs(database)

            self.assertEqual([job.job_id for job in jobs], [succeeded.job_id, queued.job_id])
            self.assertEqual(jobs[0].context["path"], "/music/a")

            payload = job_payload(jobs[0])
            self.assertEqual(payload["status_label"], "Succeeded")
            self.assertEqual(payload["kind_label"], "Delete Root")
            self.assertEqual(payload["created_at_label"], "2026-04-21 10:00:00 UTC")
            self.assertEqual(payload["message"], "Delete completed for .../a.")
            self.assertEqual(
                payload["context_items"],
                [
                    {"label": "Root", "value": ".../a"},
                    {"label": "Duration", "value": "1.25 seconds"},
                ],
            )

    def test_scan_job_payload_formats_scan_counts(self) -> None:
        payload = job_payload(
            PlayerJobRecord(
                job_id=1,
                created_at="2026-04-21T10:00:00Z",
                updated_at="2026-04-21T10:00:00Z",
                started_at=None,
                finished_at=None,
                cancel_requested_at=None,
                kind="rescan_library",
                status="succeeded",
                message="Rescan completed.",
                reason="",
                context={
                    "roots_scanned": 2,
                    "tracks_scanned": 1_200,
                    "albums_scanned": 12_300,
                    "duration_seconds": 4.125,
                },
            )
        )

        self.assertEqual(payload["kind_label"], "Rescan")
        self.assertEqual(payload["message"], "Rescan completed.")
        self.assertEqual(
            payload["context_items"],
            [
                {"label": "Roots", "value": "2"},
                {"label": "Tracks", "value": "1.2k"},
                {"label": "Albums", "value": "12.3k"},
                {"label": "Duration", "value": "4.12 seconds"},
            ],
        )


class PlayerAlbumTagEditTest(unittest.TestCase):
    def seed_album(
        self,
        database: Path,
        paths: tuple[Path, Path],
        *,
        album_id: str = "old-artist::album",
        album_artist: str = "Old Artist",
        album: str = "Album",
    ) -> None:
        connection = connect_database(database)
        try:
            connection.execute(
                "INSERT INTO library_roots (position, root_path) VALUES (?, ?)",
                (0, str(paths[0].parent.parent)),
            )
            insert_library_album(
                connection,
                album_id,
                album_artist,
                album,
                1980,
                2,
            )
            connection.execute(
                """
                INSERT INTO library_tracks (
                    track_id,
                    album_id,
                    root_position,
                    path,
                    file_type,
                    artist,
                    album_artist,
                    album,
                    title,
                    track_number,
                    date,
                    duration_seconds,
                    bitrate
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    1,
                    album_id,
                    0,
                    str(paths[0]),
                    "mp3",
                    "Artist One",
                    album_artist,
                    album,
                    "Track One",
                    "1",
                    "1980",
                    100.0,
                    128000,
                ),
            )
            connection.execute(
                """
                INSERT INTO library_tracks (
                    track_id,
                    album_id,
                    root_position,
                    path,
                    file_type,
                    artist,
                    album_artist,
                    album,
                    title,
                    track_number,
                    date,
                    duration_seconds,
                    bitrate
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    2,
                    album_id,
                    0,
                    str(paths[1]),
                    "mp3",
                    "Artist Two",
                    album_artist,
                    album,
                    "Track Two",
                    "2",
                    "1980",
                    120.0,
                    128000,
                ),
            )
            connection.execute(
                """
                INSERT INTO library_track_genres (track_id, position, genre)
                VALUES (?, ?, ?), (?, ?, ?)
                """,
                (1, 0, "Soundtrack", 2, 0, "Soundtrack"),
            )
            connection.execute(
                """
                INSERT INTO library_track_styles (track_id, position, style)
                VALUES (?, ?, ?), (?, ?, ?)
                """,
                (1, 0, "Score", 2, 0, "Score"),
            )
            connection.execute(
                """
                INSERT INTO library_track_artwork (track_id, height_px, mime_type, data)
                VALUES (?, ?, ?, ?)
                """,
                (1, 32, "image/jpeg", b"artwork-bytes"),
            )
            connection.commit()
        finally:
            connection.close()

    def seed_remote_album(
        self,
        database: Path,
        *,
        source_json: str | None = None,
        object_key: str | None = "tracks/Album/01.flac",
        content_type: str | None = "audio/flac",
        album_id: str = "old-artist::album",
    ) -> tuple[RemoteRootConfig, str]:
        remote = RemoteRootConfig(
            name="Remote",
            endpoint_url="https://s3.example.test",
            bucket="bucket",
            prefix="tracks/",
        )
        fallback_object_key = object_key or "tracks/Album/01.flac"
        track_path = canonical_s3_path(remote, fallback_object_key)
        root_source_json = remote.source_json if source_json is None else source_json
        connection = connect_database(database)
        try:
            connection.execute(
                """
                INSERT INTO library_roots (
                    position, root_path, kind, source_json
                ) VALUES (?, ?, ?, ?)
                """,
                (0, remote.root_path, "s3", root_source_json),
            )
            insert_library_album(
                connection,
                album_id,
                "Old Artist",
                "Album",
                1980,
                1,
            )
            connection.execute(
                """
                INSERT INTO library_tracks (
                    track_id,
                    album_id,
                    root_position,
                    path,
                    file_type,
                    artist,
                    album_artist,
                    album,
                    title,
                    track_number
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    1,
                    album_id,
                    0,
                    track_path,
                    "flac",
                    "Artist",
                    "Old Artist",
                    "Album",
                    "Track",
                    "1",
                ),
            )
            connection.execute(
                """
                INSERT INTO library_track_sources (
                    track_id,
                    source_kind,
                    root_position,
                    canonical_path,
                    object_key,
                    content_type
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (1, "s3", 0, track_path, object_key, content_type),
            )
            connection.commit()
        finally:
            connection.close()
        return remote, track_path

    def test_prepare_album_tag_edit_job_accepts_remote_tracks(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            remote, track_path = self.seed_remote_album(database)

            job = prepare_album_tag_edit_job(
                database,
                "old-artist::album",
                {
                    "album": "Album",
                    "album_artist": "Old Artist",
                    "genre": "Electronic",
                    "tracks": [
                        {
                            "track_id": 1,
                            "artist": "Artist",
                            "track_number": "1",
                            "title": "Track",
                        }
                    ],
                },
            )

            self.assertEqual(job.tracks[0].path, track_path)
            self.assertEqual(job.tracks[0].source_kind, "s3")
            self.assertEqual(job.tracks[0].source_json, remote.source_json)
            self.assertEqual(job.tracks[0].object_key, "tracks/Album/01.flac")
            self.assertEqual(job.tracks[0].content_type, "audio/flac")

    def test_edit_library_album_tags_rewrites_remote_audio_object(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            self.seed_remote_album(database)
            job = prepare_album_tag_edit_job(
                database,
                "old-artist::album",
                {
                    "album": "New Album",
                    "album_artist": "Various Artists",
                    "genre": "Electronic; Score",
                    "tracks": [
                        {
                            "track_id": 1,
                            "artist": "Wendy Carlos",
                            "track_number": "7",
                            "title": "Main Title",
                        }
                    ],
                },
            )
            client = FakeRemoteEditS3Client(
                b"remote audio",
                metadata={"Local-Created-At": "2026-05-16T12:00:00+00:00"},
                content_type="application/octet-stream",
            )
            write_calls: list[tuple[Path, dict[str, object]]] = []

            def fake_write(path: Path, **kwargs: object) -> None:
                self.assertEqual(path.read_bytes(), b"remote audio")
                path.write_bytes(b"edited remote audio")
                write_calls.append((path, kwargs))

            with (
                patch("kukicha.use_case.commands.album_edits.create_s3_client", return_value=client),
                patch("kukicha.audio_types.mimetypes.guess_type", return_value=(None, None)),
                patch(
                    "kukicha.use_case.commands.album_edits.write_track_audio_tags",
                    side_effect=fake_write,
                ),
            ):
                result = edit_library_album_tags(database, job)

            self.assertEqual(result.tracks_updated, 1)
            self.assertEqual(len(write_calls), 1)
            self.assertEqual(write_calls[0][1]["artist"], "Wendy Carlos")
            self.assertEqual(write_calls[0][1]["album_artist"], "Various Artists")
            self.assertEqual(write_calls[0][1]["album"], "New Album")
            self.assertEqual(write_calls[0][1]["track_number"], "7")
            self.assertEqual(write_calls[0][1]["title"], "Main Title")
            self.assertEqual(write_calls[0][1]["genre"], "Electronic; Score")
            self.assertEqual(
                client.gets,
                [{"Bucket": "bucket", "Key": "tracks/Album/01.flac"}],
            )
            self.assertEqual(
                client.puts,
                [
                    {
                        "Bucket": "bucket",
                        "Key": "tracks/Album/01.flac",
                        "Body": b"edited remote audio",
                        "ContentType": "audio/flac",
                        "Metadata": {
                            "local-created-at": "2026-05-16T12:00:00+00:00",
                        },
                    }
                ],
            )

    def test_edit_library_album_tags_requires_remote_object_key_metadata(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            self.seed_remote_album(database, object_key=None)
            job = prepare_album_tag_edit_job(
                database,
                "old-artist::album",
                {
                    "album": "Album",
                    "album_artist": "Old Artist",
                    "genre": "Electronic",
                    "tracks": [
                        {
                            "track_id": 1,
                            "artist": "Artist",
                            "track_number": "1",
                            "title": "Track",
                        }
                    ],
                },
            )

            with self.assertRaisesRegex(ValueError, "S3 object key metadata"):
                edit_library_album_tags(database, job)

    def test_delete_album_files_removes_local_tracks_sidecar_and_empty_folder(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            album_dir = temp_path / "Album"
            album_dir.mkdir()
            first = album_dir / "01.mp3"
            second = album_dir / "02.mp3"
            cover = album_dir / "cover.jpg"
            first.write_bytes(b"one")
            second.write_bytes(b"two")
            cover.write_bytes(b"cover")
            database = temp_path / "kukicha.sqlite"
            self.seed_album(database, (first, second))
            connection = connect_database(database)
            try:
                connection.execute(
                    """
                    UPDATE library_tracks
                    SET sidecar_artwork_path = ?,
                        sidecar_artwork_modified_at_ns = ?,
                        sidecar_artwork_size_bytes = ?
                    WHERE album_id = ?
                    """,
                    (str(cover), 1, 5, "old-artist::album"),
                )
                connection.commit()
            finally:
                connection.close()

            job = prepare_album_delete_job(database, "old-artist::album")
            result = delete_album_files(job)

            self.assertFalse(first.exists())
            self.assertFalse(second.exists())
            self.assertFalse(cover.exists())
            self.assertFalse(album_dir.exists())
            self.assertTrue(temp_path.exists())
            self.assertEqual(result.tracks_deleted, 2)
            self.assertEqual(result.local_files_deleted, 3)
            self.assertEqual(result.local_folders_pruned, 1)
            connection = connect_database(database)
            try:
                row = connection.execute(
                    "SELECT COUNT(*) AS count FROM library_tracks WHERE album_id = ?",
                    ("old-artist::album",),
                ).fetchone()
                self.assertEqual(int(row["count"]), 2)
                self.assertIsNotNone(
                    connection.execute(
                        "SELECT 1 FROM library_albums WHERE album_id = ?",
                        ("old-artist::album",),
                    ).fetchone()
                )
            finally:
                connection.close()

    def test_delete_album_files_preserves_shared_local_sidecar(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            album_dir = temp_path / "Album"
            album_dir.mkdir()
            first = album_dir / "01.mp3"
            second = album_dir / "02.mp3"
            cover = album_dir / "cover.jpg"
            first.write_bytes(b"one")
            second.write_bytes(b"two")
            cover.write_bytes(b"cover")
            database = temp_path / "kukicha.sqlite"
            self.seed_album(database, (first, second))
            connection = connect_database(database)
            try:
                insert_library_album(connection, "other::album", "Other", "Album", 1981, 1)
                connection.execute(
                    """
                    INSERT INTO library_tracks (
                        track_id, album_id, root_position, path, file_type, title,
                        sidecar_artwork_path
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (3, "other::album", 0, str(temp_path / "Other" / "01.mp3"), "mp3", "Other", str(cover)),
                )
                connection.execute(
                    """
                    UPDATE library_tracks
                    SET sidecar_artwork_path = ?
                    WHERE album_id = ?
                    """,
                    (str(cover), "old-artist::album"),
                )
                connection.commit()
            finally:
                connection.close()

            job = prepare_album_delete_job(database, "old-artist::album")
            result = delete_album_files(job)

            self.assertEqual(job.local_sidecar_paths, ())
            self.assertFalse(first.exists())
            self.assertFalse(second.exists())
            self.assertTrue(cover.exists())
            self.assertTrue(album_dir.exists())
            self.assertEqual(result.local_files_deleted, 2)
            self.assertEqual(result.local_folders_pruned, 0)

    def test_delete_album_files_tolerates_missing_local_files(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            album_dir = temp_path / "Album"
            album_dir.mkdir()
            first = album_dir / "01.mp3"
            second = album_dir / "02.mp3"
            first.write_bytes(b"one")
            database = temp_path / "kukicha.sqlite"
            self.seed_album(database, (first, second))

            job = prepare_album_delete_job(database, "old-artist::album")
            result = delete_album_files(job)

            self.assertFalse(first.exists())
            self.assertFalse(album_dir.exists())
            self.assertEqual(result.local_files_deleted, 1)
            self.assertEqual(result.local_folders_pruned, 1)

    def test_delete_album_files_removes_remote_object_and_empty_prefix_marker(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            self.seed_remote_album(database)
            job = prepare_album_delete_job(database, "old-artist::album")
            client = FakeRemoteEditS3Client(
                objects=("tracks/Album/01.flac", "tracks/Album/", "tracks/")
            )

            with patch("kukicha.use_case.commands.album_deletes.create_s3_client", return_value=client):
                result = delete_album_files(job)

            self.assertEqual(result.remote_objects_deleted, 1)
            self.assertEqual(result.remote_prefixes_pruned, 1)
            self.assertEqual(
                client.deletes,
                [
                    {"Bucket": "bucket", "Key": "tracks/Album/01.flac"},
                    {"Bucket": "bucket", "Key": "tracks/Album/"},
                ],
            )

    def test_delete_album_files_preserves_nonempty_remote_prefix_marker(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            self.seed_remote_album(database)
            job = prepare_album_delete_job(database, "old-artist::album")
            client = FakeRemoteEditS3Client(
                objects=(
                    "tracks/Album/01.flac",
                    "tracks/Album/02.flac",
                    "tracks/Album/",
                )
            )

            with patch("kukicha.use_case.commands.album_deletes.create_s3_client", return_value=client):
                result = delete_album_files(job)

            self.assertEqual(result.remote_objects_deleted, 1)
            self.assertEqual(result.remote_prefixes_pruned, 0)
            self.assertEqual(
                client.deletes,
                [{"Bucket": "bucket", "Key": "tracks/Album/01.flac"}],
            )

    def test_delete_album_files_preserves_shared_remote_sidecar(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            remote, track_path = self.seed_remote_album(database)
            cover_path = canonical_s3_path(remote, "tracks/Album/cover.jpg")
            other_path = canonical_s3_path(remote, "tracks/Other/01.flac")
            connection = connect_database(database)
            try:
                insert_library_album(connection, "other::album", "Other", "Album", 1981, 1)
                connection.execute(
                    """
                    INSERT INTO library_tracks (
                        track_id, album_id, root_position, path, file_type, title,
                        sidecar_artwork_path
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (3, "other::album", 0, other_path, "flac", "Other", cover_path),
                )
                connection.execute(
                    """
                    INSERT INTO library_track_sources (
                        track_id, source_kind, root_position, canonical_path,
                        object_key, sidecar_object_key
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (3, "s3", 0, other_path, "tracks/Other/01.flac", "tracks/Album/cover.jpg"),
                )
                connection.execute(
                    "UPDATE library_tracks SET sidecar_artwork_path = ? WHERE track_id = ?",
                    (cover_path, 1),
                )
                connection.execute(
                    "UPDATE library_track_sources SET sidecar_object_key = ? WHERE track_id = ?",
                    ("tracks/Album/cover.jpg", 1),
                )
                connection.commit()
            finally:
                connection.close()

            job = prepare_album_delete_job(database, "old-artist::album")
            client = FakeRemoteEditS3Client(
                objects=(
                    "tracks/Album/01.flac",
                    "tracks/Album/cover.jpg",
                    "tracks/Album/",
                )
            )

            with patch("kukicha.use_case.commands.album_deletes.create_s3_client", return_value=client):
                result = delete_album_files(job)

            self.assertEqual(job.remote_sidecar_refs, ())
            self.assertEqual(result.remote_objects_deleted, 1)
            self.assertEqual(
                client.deletes,
                [{"Bucket": "bucket", "Key": "tracks/Album/01.flac"}],
            )
            self.assertEqual(track_path, job.tracks[0].path)

    def test_delete_album_files_requires_remote_object_key_metadata(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            self.seed_remote_album(database, object_key=None)
            job = prepare_album_delete_job(database, "old-artist::album")

            with self.assertRaisesRegex(ValueError, "S3 object key metadata"):
                delete_album_files(job)

    def test_upload_album_cover_files_writes_cover_to_each_local_track_folder(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            album_id = "old-artist::album"
            first = temp_path / "One" / "Album" / "01.mp3"
            second = temp_path / "Two" / "Album" / "02.mp3"
            first.parent.mkdir(parents=True)
            second.parent.mkdir(parents=True)
            first.write_bytes(b"one")
            second.write_bytes(b"two")
            existing_cover = first.parent / "cover.png"
            existing_cover.write_bytes(b"old cover")
            database = temp_path / "kukicha.sqlite"
            self.seed_album(database, (first, second), album_id=album_id)

            job = prepare_album_cover_upload_job(
                database,
                album_id,
                filename="Front.PNG",
                data=b"new cover",
            )
            result = upload_album_cover_files(job)

            self.assertEqual(job.cover_filename, "cover.png")
            self.assertEqual(result.targets_updated, 2)
            self.assertEqual(result.local_files_updated, 2)
            self.assertEqual(result.remote_objects_updated, 0)
            self.assertEqual((first.parent / "cover.png").read_bytes(), b"new cover")
            self.assertEqual((second.parent / "cover.png").read_bytes(), b"new cover")

    def test_prepare_album_cover_upload_job_rejects_unknown_album_metadata(self) -> None:
        cases = (
            ("unknown::unknown", "__Unknown", "__Unknown"),
            ("foo::unknown", "Foo", "__Unknown"),
            ("unknown::foo", "__Unknown", "Foo"),
        )
        for album_id, album_artist, album in cases:
            with self.subTest(album_id=album_id), TemporaryDirectory() as tempdir:
                temp_path = Path(tempdir)
                first = temp_path / "Album" / "01.mp3"
                second = temp_path / "Album" / "02.mp3"
                first.parent.mkdir()
                first.write_bytes(b"one")
                second.write_bytes(b"two")
                database = temp_path / "kukicha.sqlite"
                self.seed_album(
                    database,
                    (first, second),
                    album_id=album_id,
                    album_artist=album_artist,
                    album=album,
                )

                with self.assertRaisesRegex(
                    ValueError,
                    "unknown album artist or album",
                ):
                    prepare_album_cover_upload_job(
                        database,
                        album_id,
                        filename="front.jpg",
                        data=b"cover",
                    )

    def test_album_edit_context_shows_cover_upload_for_single_musicbrainz_group(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            album_id = "old-artist::album"
            database = temp_path / "kukicha.sqlite"
            first = temp_path / "Album" / "01.mp3"
            second = temp_path / "Album" / "02.mp3"
            first.parent.mkdir()
            first.write_bytes(b"one")
            second.write_bytes(b"two")
            self.seed_album(database, (first, second), album_id=album_id)

            context = build_album_edit_context(PlayerRuntime(database), album_id, "")

            self.assertTrue(context["album_cover_upload_enabled"])

    def test_album_edit_context_hides_cover_upload_for_unknown_album_metadata(self) -> None:
        cases = (
            ("unknown::unknown", "__Unknown", "__Unknown"),
            ("foo::unknown", "Foo", "__Unknown"),
            ("unknown::foo", "__Unknown", "Foo"),
        )
        for album_id, album_artist, album in cases:
            with self.subTest(album_id=album_id), TemporaryDirectory() as tempdir:
                temp_path = Path(tempdir)
                database = temp_path / "kukicha.sqlite"
                first = temp_path / "Album" / "01.mp3"
                second = temp_path / "Album" / "02.mp3"
                first.parent.mkdir()
                first.write_bytes(b"one")
                second.write_bytes(b"two")
                self.seed_album(
                    database,
                    (first, second),
                    album_id=album_id,
                    album_artist=album_artist,
                    album=album,
                )

                context = build_album_edit_context(PlayerRuntime(database), album_id, "")

                self.assertFalse(context["album_cover_upload_enabled"])

    def test_album_edit_context_hides_cover_upload_for_multiple_musicbrainz_groups(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            album_id = "old-artist::album"
            database = temp_path / "kukicha.sqlite"
            first = temp_path / "One" / "Album" / "01.mp3"
            second = temp_path / "Two" / "Album" / "02.mp3"
            first.parent.mkdir(parents=True)
            second.parent.mkdir(parents=True)
            first.write_bytes(b"one")
            second.write_bytes(b"two")
            self.seed_album(database, (first, second), album_id=album_id)

            context = build_album_edit_context(PlayerRuntime(database), album_id, "")

            self.assertFalse(context["album_cover_upload_enabled"])

    def test_upload_album_cover_files_puts_remote_cover_object(self) -> None:
        with TemporaryDirectory() as tempdir:
            album_id = "old-artist::album::abc"
            database = Path(tempdir) / "kukicha.sqlite"
            self.seed_remote_album(database, album_id=album_id)
            job = prepare_album_cover_upload_job(
                database,
                album_id,
                filename="front.JPEG",
                data=b"remote cover",
            )
            client = FakeRemoteEditS3Client()

            with patch("kukicha.use_case.commands.album_covers.create_s3_client", return_value=client):
                result = upload_album_cover_files(job)

            self.assertEqual(result.targets_updated, 1)
            self.assertEqual(result.remote_objects_updated, 1)
            self.assertEqual(
                client.puts,
                [
                    {
                        "Bucket": "bucket",
                        "Key": "tracks/Album/cover.jpeg",
                        "Body": b"remote cover",
                        "ContentType": "image/jpeg",
                        "Metadata": {},
                    }
                ],
            )

    def test_start_album_delete_queues_background_job(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            first = temp_path / "Album" / "01.mp3"
            second = temp_path / "Album" / "02.mp3"
            first.parent.mkdir()
            first.write_bytes(b"one")
            second.write_bytes(b"two")
            self.seed_album(database, (first, second))
            runtime = Mock()
            runtime.database = database
            runtime.enqueue_job.return_value = PlayerJobRecord(
                job_id=14,
                created_at="2026-04-21T10:00:00Z",
                updated_at="2026-04-21T10:00:00Z",
                started_at=None,
                finished_at=None,
                cancel_requested_at=None,
                kind="delete_album",
                status="queued",
                message="Delete queued for Old Artist - Album.",
                reason="",
                context={
                    "album": "Album",
                    "tracks_deleted": 2,
                },
            )

            result = start_album_delete(runtime, "old-artist::album")

            self.assertEqual(result["message"], "Delete queued for Old Artist - Album.")
            self.assertEqual(result["job"]["job_id"], 14)
            runtime.enqueue_job.assert_called_once()
            enqueue_kwargs = runtime.enqueue_job.call_args.kwargs
            self.assertEqual(enqueue_kwargs["kind"], "delete_album")
            self.assertEqual(enqueue_kwargs["queued_message"], "Delete queued for Old Artist - Album.")
            self.assertEqual(enqueue_kwargs["running_message"], "Delete running for Old Artist - Album.")
            self.assertEqual(enqueue_kwargs["failed_message"], "Delete failed for Old Artist - Album.")
            self.assertEqual(enqueue_kwargs["context"]["tracks_deleted"], 2)
            self.assertTrue(callable(enqueue_kwargs["runner"]))

    def test_start_album_cover_upload_queues_background_job(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            album_id = "old-artist::album::abc"
            database = temp_path / "kukicha.sqlite"
            first = temp_path / "Album" / "01.mp3"
            second = temp_path / "Album" / "02.mp3"
            first.parent.mkdir()
            first.write_bytes(b"one")
            second.write_bytes(b"two")
            self.seed_album(database, (first, second), album_id=album_id)
            runtime = Mock()
            runtime.database = database
            runtime.enqueue_job.return_value = PlayerJobRecord(
                job_id=15,
                created_at="2026-04-21T10:00:00Z",
                updated_at="2026-04-21T10:00:00Z",
                started_at=None,
                finished_at=None,
                cancel_requested_at=None,
                kind="upload_album_cover",
                status="queued",
                message="Cover upload queued for Old Artist - Album.",
                reason="",
                context={
                    "album": "Album",
                    "cover_filename": "cover.jpg",
                    "cover_targets": 1,
                },
            )

            result = start_album_cover_upload(
                runtime,
                album_id,
                filename="folder.jpg",
                data=b"cover",
            )

            self.assertEqual(result["message"], "Cover upload queued for Old Artist - Album.")
            self.assertEqual(result["job"]["job_id"], 15)
            runtime.enqueue_job.assert_called_once()
            enqueue_kwargs = runtime.enqueue_job.call_args.kwargs
            self.assertEqual(enqueue_kwargs["kind"], "upload_album_cover")
            self.assertEqual(enqueue_kwargs["queued_message"], "Cover upload queued for Old Artist - Album.")
            self.assertEqual(enqueue_kwargs["running_message"], "Cover upload running for Old Artist - Album.")
            self.assertEqual(enqueue_kwargs["failed_message"], "Cover upload failed for Old Artist - Album.")
            self.assertEqual(enqueue_kwargs["context"]["cover_filename"], "cover.jpg")
            self.assertEqual(enqueue_kwargs["context"]["cover_targets"], 1)
            self.assertTrue(callable(enqueue_kwargs["runner"]))

    def test_edit_library_album_musicbrainz_rewrites_remote_audio_and_stores_links(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            self.seed_remote_album(database)
            job = prepare_album_musicbrainz_edit_job(
                database,
                "old-artist::album",
                {
                    "metadata_url": (
                        "https://musicbrainz.org/release/"
                        "11111111-1111-1111-1111-111111111111"
                    ),
                },
            )
            release_payload = {
                "title": "Remote Album",
                "artist-credit": [{"name": "Remote Artist"}],
                "genres": [{"name": "electronic", "count": 3}],
                "release-group": {"id": "22222222-2222-2222-2222-222222222222"},
            }
            release_group_payload = {
                "title": "Remote Album",
                "artist-credit": [{"name": "Ignored Artist"}],
                "genres": [{"name": "ambient", "count": 2}],
            }

            def fake_get_musicbrainz_entity(
                _connection: object,
                _client: object,
                *,
                entity_type: str,
                mbid: str,
            ) -> dict[str, object]:
                if entity_type == "release":
                    self.assertEqual(mbid, "11111111-1111-1111-1111-111111111111")
                    return release_payload
                self.assertEqual(entity_type, "release-group")
                self.assertEqual(mbid, "22222222-2222-2222-2222-222222222222")
                return release_group_payload

            client = FakeRemoteEditS3Client(b"remote musicbrainz audio")
            write_calls: list[tuple[Path, dict[str, object]]] = []

            def fake_write(path: Path, **kwargs: object) -> None:
                self.assertEqual(path.read_bytes(), b"remote musicbrainz audio")
                path.write_bytes(b"edited musicbrainz audio")
                write_calls.append((path, kwargs))

            with (
                patch(
                    "kukicha.use_case.commands.album_edits.get_musicbrainz_entity",
                    side_effect=fake_get_musicbrainz_entity,
                ),
                patch("kukicha.use_case.commands.album_edits.create_s3_client", return_value=client),
                patch(
                    "kukicha.use_case.commands.album_edits.write_album_audio_tags",
                    side_effect=fake_write,
                ),
            ):
                result = edit_library_album_musicbrainz(database, job)

            self.assertEqual(result.album, "Remote Album")
            self.assertEqual(result.album_artist, "Remote Artist")
            self.assertEqual(result.genre, "Electronic; Ambient")
            self.assertEqual(result.tracks_updated, 1)
            self.assertEqual(write_calls[0][1]["album_artist"], "Remote Artist")
            self.assertEqual(write_calls[0][1]["album"], "Remote Album")
            self.assertEqual(write_calls[0][1]["genre"], "Electronic; Ambient")
            self.assertEqual(client.puts[0]["Body"], b"edited musicbrainz audio")

            connection = connect_database(database, create=False)
            try:
                link_row = connection.execute(
                    """
                    SELECT release_mbid, release_group_mbid
                    FROM album_musicbrainz_links
                    WHERE file_album_id = ?
                    """,
                    ("remote-artist::remote-album",),
                ).fetchone()
                track_link_row = connection.execute(
                    """
                    SELECT file_album_id, release_mbid, release_group_mbid
                    FROM album_musicbrainz_track_links
                    WHERE path = ?
                    """,
                    (job.tracks[0].path,),
                ).fetchone()
            finally:
                connection.close()

            self.assertIsNotNone(link_row)
            self.assertEqual(
                str(link_row["release_mbid"]),
                "11111111-1111-1111-1111-111111111111",
            )
            self.assertEqual(
                str(link_row["release_group_mbid"]),
                "22222222-2222-2222-2222-222222222222",
            )
            self.assertIsNotNone(track_link_row)
            self.assertEqual(str(track_link_row["file_album_id"]), "remote-artist::remote-album")
            self.assertEqual(
                str(track_link_row["release_mbid"]),
                "11111111-1111-1111-1111-111111111111",
            )
            self.assertEqual(
                str(track_link_row["release_group_mbid"]),
                "22222222-2222-2222-2222-222222222222",
            )

    def test_prepare_album_musicbrainz_edit_job_scopes_to_requested_tracks(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            paths = (
                temp_path / "Album" / "01.mp3",
                temp_path / "Album" / "02.mp3",
            )
            self.seed_album(database, paths)

            job = prepare_album_musicbrainz_edit_job(
                database,
                "old-artist::album",
                {
                    "metadata_url": (
                        "https://musicbrainz.org/release-group/"
                        "22222222-2222-2222-2222-222222222222"
                    ),
                    "track_ids": [2],
                },
            )

            self.assertEqual(job.request.track_ids, (2,))
            self.assertEqual([snapshot.track_id for snapshot in job.tracks], [2])
            self.assertEqual(job.tracks[0].path, str(paths[1]))
            self.assertEqual(job.tracks[0].genres, ())
            self.assertEqual(job.tracks[0].styles, ())
            self.assertIsNone(job.tracks[0].track_artwork)

    def test_prepare_album_musicbrainz_edit_job_rejects_track_from_another_album(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            paths = (
                temp_path / "Album" / "01.mp3",
                temp_path / "Album" / "02.mp3",
            )
            self.seed_album(database, paths)
            connection = connect_database(database, create=False)
            try:
                insert_library_album(
                    connection,
                    "other-artist::other-album",
                    "Other Artist",
                    "Other Album",
                    1981,
                    1,
                )
                connection.execute(
                    """
                    INSERT INTO library_tracks (
                        track_id,
                        album_id,
                        root_position,
                        path,
                        file_type,
                        artist,
                        album_artist,
                        album,
                        title,
                        track_number,
                        date,
                        duration_seconds,
                        bitrate
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        3,
                        "other-artist::other-album",
                        0,
                        str(temp_path / "Other Album" / "01.mp3"),
                        "mp3",
                        "Other Artist",
                        "Other Artist",
                        "Other Album",
                        "Other Track",
                        "1",
                        "1981",
                        90.0,
                        128000,
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            with self.assertRaisesRegex(ValueError, "track does not belong to album: 3"):
                prepare_album_musicbrainz_edit_job(
                    database,
                    "old-artist::album",
                    {
                        "metadata_url": (
                            "https://musicbrainz.org/release-group/"
                            "22222222-2222-2222-2222-222222222222"
                        ),
                        "track_ids": [3],
                    },
                )

    def test_prepare_album_musicbrainz_edit_request_rejects_duplicate_track_ids(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            paths = (
                temp_path / "Album" / "01.mp3",
                temp_path / "Album" / "02.mp3",
            )
            self.seed_album(database, paths)

            with self.assertRaisesRegex(ValueError, "duplicate track id: 1"):
                prepare_album_musicbrainz_edit_request(
                    database,
                    "old-artist::album",
                    {
                        "metadata_url": (
                            "https://musicbrainz.org/release-group/"
                            "22222222-2222-2222-2222-222222222222"
                        ),
                        "track_ids": [1, 1],
                    },
                )

    def test_prepare_album_musicbrainz_edit_request_accepts_release_and_release_group_urls(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            paths = (
                temp_path / "Album" / "01.mp3",
                temp_path / "Album" / "02.mp3",
            )
            self.seed_album(database, paths)

            request = prepare_album_musicbrainz_edit_request(
                database,
                "old-artist::album",
                {
                    "groups": [
                        {
                            "musicbrainz_url": (
                                "https://musicbrainz.org/release/"
                                "6d0aaf02-f571-4c03-8677-23018ff628ee"
                            ),
                            "track_ids": [1],
                        },
                        {
                            "musicbrainz_url": (
                                "https://musicbrainz.org/release-group/"
                                "0e7a233f-81f8-3e63-ad07-6cdfe2faecc3"
                            ),
                            "track_ids": [2],
                        },
                    ],
                },
            )

            self.assertEqual(
                request.groups[0].musicbrainz_release_mbid,
                "6d0aaf02-f571-4c03-8677-23018ff628ee",
            )
            self.assertIsNone(request.groups[0].musicbrainz_release_group_mbid)
            self.assertIsNone(request.groups[1].musicbrainz_release_mbid)
            self.assertEqual(
                request.groups[1].musicbrainz_release_group_mbid,
                "0e7a233f-81f8-3e63-ad07-6cdfe2faecc3",
            )

    def test_edit_library_album_musicbrainz_writes_musicbrainz_tags_without_reconciling_database(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            paths = (
                temp_path / "Album" / "01.mp3",
                temp_path / "Album" / "02.mp3",
            )
            self.seed_album(database, paths)

            job = prepare_album_musicbrainz_edit_job(
                database,
                "old-artist::album",
                {
                    "metadata_url": (
                        "https://musicbrainz.org/release/"
                        "11111111-1111-1111-1111-111111111111"
                    ),
                },
            )

            release_payload = {
                "title": "Foo",
                "disambiguation": "expanded edition",
                "artist-credit": [
                    {"name": "Brian Eno", "joinphrase": " & "},
                    {"name": "Robert Fripp"},
                ],
                "genres": [{"name": "electronic", "count": 3}],
                "release-group": {"id": "22222222-2222-2222-2222-222222222222"},
            }
            release_group_payload = {
                "title": "Foo",
                "artist-credit": [{"name": "Ignored Artist"}],
                "genres": [{"name": "ambient", "count": 2}],
            }

            def fake_get_musicbrainz_entity(
                _connection: object,
                _client: object,
                *,
                entity_type: str,
                mbid: str,
            ) -> dict[str, object]:
                if entity_type == "release":
                    self.assertEqual(mbid, "11111111-1111-1111-1111-111111111111")
                    return release_payload
                self.assertEqual(entity_type, "release-group")
                self.assertEqual(mbid, "22222222-2222-2222-2222-222222222222")
                return release_group_payload

            with (
                patch(
                    "kukicha.use_case.commands.album_edits.get_musicbrainz_entity",
                    side_effect=fake_get_musicbrainz_entity,
                ),
                patch("kukicha.use_case.commands.album_edits.write_album_audio_tags") as write_album_tags,
            ):
                result = edit_library_album_musicbrainz(database, job)

            self.assertEqual(
                write_album_tags.call_args_list,
                [
                    call(
                        paths[0],
                        album_artist="Brian Eno & Robert Fripp",
                        album="Foo",
                        genre="Electronic; Ambient",
                    ),
                    call(
                        paths[1],
                        album_artist="Brian Eno & Robert Fripp",
                        album="Foo",
                        genre="Electronic; Ambient",
                    ),
                ],
            )
            self.assertEqual(result.album, "Foo")
            self.assertEqual(result.album_artist, "Brian Eno & Robert Fripp")
            self.assertEqual(result.genre, "Electronic; Ambient")
            self.assertEqual(result.tracks_updated, 2)
            self.assertFalse(result.ids_cleared)
            self.assertEqual(result.genre_resolution.musicbrainz_album_overrides, 1)

            connection = connect_database(database, create=False)
            try:
                row = connection.execute(
                    """
                    SELECT release_mbid, release_group_mbid
                    FROM album_musicbrainz_links
                    WHERE file_album_id = ?
                    """,
                    ("brian-eno-robert-fripp::foo",),
                ).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(str(row["release_mbid"]), "11111111-1111-1111-1111-111111111111")
                self.assertEqual(str(row["release_group_mbid"]), "22222222-2222-2222-2222-222222222222")
                track_link_rows = [
                    (
                        str(row["path"]),
                        str(row["file_album_id"]),
                        str(row["release_mbid"]),
                        str(row["release_group_mbid"]),
                    )
                    for row in connection.execute(
                        """
                        SELECT path, file_album_id, release_mbid, release_group_mbid
                        FROM album_musicbrainz_track_links
                        ORDER BY path
                        """
                    )
                ]
                self.assertEqual(
                    track_link_rows,
                    [
                        (
                            str(paths[0]),
                            "brian-eno-robert-fripp::foo",
                            "11111111-1111-1111-1111-111111111111",
                            "22222222-2222-2222-2222-222222222222",
                        ),
                        (
                            str(paths[1]),
                            "brian-eno-robert-fripp::foo",
                            "11111111-1111-1111-1111-111111111111",
                            "22222222-2222-2222-2222-222222222222",
                        ),
                    ],
                )
                self.assertIsNone(
                    connection.execute(
                        """
                        SELECT 1
                        FROM album_musicbrainz_links
                        WHERE file_album_id = ?
                        """,
                        ("old-artist::album",),
                    ).fetchone()
                )
                track_row = connection.execute(
                    """
                    SELECT artist, album_artist, album
                    FROM library_tracks
                    WHERE track_id = ?
                    """,
                    (1,),
                ).fetchone()
                self.assertIsNotNone(track_row)
                self.assertEqual(str(track_row["artist"]), "Artist One")
                self.assertEqual(str(track_row["album_artist"]), "Old Artist")
                self.assertEqual(str(track_row["album"]), "Album")
                self.assertEqual(
                    [
                        str(row["genre"])
                        for row in connection.execute(
                            "SELECT genre FROM library_track_genres WHERE track_id = ? ORDER BY position",
                            (1,),
                        )
                    ],
                    ["Soundtrack"],
                )
                self.assertEqual(
                    [
                        str(row["style"])
                        for row in connection.execute(
                            "SELECT style FROM library_track_styles WHERE track_id = ? ORDER BY position",
                            (1,),
                        )
                    ],
                    ["Score"],
                )
                artwork_row = connection.execute(
                    """
                    SELECT mime_type, data
                    FROM library_track_artwork
                    WHERE track_id = ? AND height_px = ?
                    """,
                    (1, 32),
                ).fetchone()
                self.assertIsNotNone(artwork_row)
                self.assertEqual(str(artwork_row["mime_type"]), "image/jpeg")
                self.assertEqual(bytes(artwork_row["data"]), b"artwork-bytes")
            finally:
                connection.close()

    def test_edit_library_album_metadata_writes_discogs_tags_and_stores_link(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            paths = (
                temp_path / "Album" / "01.mp3",
                temp_path / "Album" / "02.mp3",
            )
            self.seed_album(database, paths)

            job = prepare_album_musicbrainz_edit_job(
                database,
                "old-artist::album",
                {
                    "metadata_url": (
                        "https://www.discogs.com/release/"
                        "35645122-Sun-Ra-And-His-Arkestra-Super-Sonic-Jazz-Expanded-Edition"
                    ),
                },
            )

            release_payload = {
                "title": "Super-Sonic Jazz (Expanded Edition)",
                "artists": [
                    {
                        "name": "The Sun Ra Arkestra",
                        "anv": "Sun Ra And His Arkestra",
                        "join": "",
                    },
                ],
                "genres": ["Jazz"],
                "styles": ["Bop"],
                "master_id": 143615,
            }
            master_payload = {
                "title": "Super-Sonic Jazz",
                "artists": [
                    {
                        "name": "The Sun Ra Arkestra",
                        "anv": "Le Sun Ra And His Arkestra",
                        "join": "",
                    },
                ],
                "genres": ["Jazz"],
                "styles": ["Avant-garde Jazz"],
            }

            def fake_get_discogs_entity(
                _connection: object,
                _client: object,
                *,
                entity_type: str,
                entity_id: str,
            ) -> dict[str, object]:
                if entity_type == "release":
                    self.assertEqual(entity_id, "35645122")
                    return release_payload
                self.assertEqual(entity_type, "master")
                self.assertEqual(entity_id, "143615")
                return master_payload

            with (
                patch(
                    "kukicha.use_case.commands.album_edits.get_discogs_entity",
                    side_effect=fake_get_discogs_entity,
                ),
                patch("kukicha.use_case.commands.album_edits.write_album_audio_tags") as write_album_tags,
            ):
                result = edit_library_album_musicbrainz(database, job)

            self.assertEqual(
                write_album_tags.call_args_list,
                [
                    call(
                        paths[0],
                        album_artist="Sun Ra And His Arkestra",
                        album="Super-Sonic Jazz (Expanded Edition)",
                        genre="Jazz; Bop; Avant-garde Jazz",
                    ),
                    call(
                        paths[1],
                        album_artist="Sun Ra And His Arkestra",
                        album="Super-Sonic Jazz (Expanded Edition)",
                        genre="Jazz; Bop; Avant-garde Jazz",
                    ),
                ],
            )
            self.assertEqual(result.album, "Super-Sonic Jazz (Expanded Edition)")
            self.assertEqual(result.album_artist, "Sun Ra And His Arkestra")
            self.assertEqual(result.genre, "Jazz; Bop; Avant-garde Jazz")
            self.assertEqual(result.tracks_updated, 2)

            connection = connect_database(database, create=False)
            try:
                row = connection.execute(
                    """
                    SELECT
                        provider,
                        entity_type,
                        entity_id,
                        related_entity_type,
                        related_entity_id
                    FROM album_metadata_links
                    WHERE file_album_id = ?
                    """,
                    ("sun-ra-and-his-arkestra::super-sonic-jazz-expanded-edition",),
                ).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(str(row["provider"]), "discogs")
                self.assertEqual(str(row["entity_type"]), "release")
                self.assertEqual(str(row["entity_id"]), "35645122")
                self.assertEqual(str(row["related_entity_type"]), "master")
                self.assertEqual(str(row["related_entity_id"]), "143615")
            finally:
                connection.close()

    def test_edit_library_album_musicbrainz_handles_unicode_artist_for_unknown_album(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            paths = (
                temp_path / "Album" / "01.mp3",
                temp_path / "Album" / "02.mp3",
            )
            self.seed_album(database, paths)
            connection = connect_database(database, create=False)
            try:
                insert_library_album(
                    connection,
                    "unknown::unknown",
                    "<unknown artist>",
                    "<unknown album>",
                    None,
                    2,
                )
                connection.execute(
                    """
                    UPDATE library_tracks
                    SET album_id = ?, artist = '', album_artist = '', album = ''
                    WHERE album_id = ?
                    """,
                    ("unknown::unknown", "old-artist::album"),
                )
                connection.execute(
                    "DELETE FROM library_albums WHERE album_id = ?",
                    ("old-artist::album",),
                )
                connection.commit()
            finally:
                connection.close()

            job = prepare_album_musicbrainz_edit_job(
                database,
                "unknown::unknown",
                {
                    "musicbrainz_url": (
                        "https://musicbrainz.org/release/"
                        "cc3af5d1-caf1-45c9-9fe8-37b07e77f894"
                    ),
                },
            )

            release_payload = {
                "title": "Quiet Forest",
                "artist-credit": [
                    {
                        "artist": {
                            "aliases": [
                                {
                                    "locale": "en",
                                    "name": "Hiroshi Yoshimura",
                                },
                            ],
                        },
                        "name": "吉村弘",
                    },
                ],
                "genres": [],
                "release-group": {"id": "c4bae335-57a4-4698-938f-eaf5da364bbc"},
            }
            release_group_payload = {
                "title": "Quiet Forest",
                "artist-credit": [{"name": "吉村弘"}],
                "genres": [{"name": "ambient", "count": 1}],
            }

            def fake_get_musicbrainz_entity(
                _connection: object,
                _client: object,
                *,
                entity_type: str,
                mbid: str,
            ) -> dict[str, object]:
                if entity_type == "release":
                    self.assertEqual(mbid, "cc3af5d1-caf1-45c9-9fe8-37b07e77f894")
                    return release_payload
                self.assertEqual(entity_type, "release-group")
                self.assertEqual(mbid, "c4bae335-57a4-4698-938f-eaf5da364bbc")
                return release_group_payload

            with (
                patch(
                    "kukicha.use_case.commands.album_edits.get_musicbrainz_entity",
                    side_effect=fake_get_musicbrainz_entity,
                ),
                patch("kukicha.use_case.commands.album_edits.write_album_audio_tags") as write_album_tags,
            ):
                result = edit_library_album_musicbrainz(database, job)

            self.assertEqual(
                write_album_tags.call_args_list,
                [
                    call(
                        paths[0],
                        album_artist="Hiroshi Yoshimura",
                        album="Quiet Forest",
                        genre="Electronic; Ambient",
                    ),
                    call(
                        paths[1],
                        album_artist="Hiroshi Yoshimura",
                        album="Quiet Forest",
                        genre="Electronic; Ambient",
                    ),
                ],
            )
            self.assertEqual(result.album, "Quiet Forest")
            self.assertEqual(result.album_artist, "Hiroshi Yoshimura")
            self.assertEqual(result.genre, "Electronic; Ambient")
            self.assertEqual(result.tracks_updated, 2)

            connection = connect_database(database, create=False)
            try:
                row = connection.execute(
                    """
                    SELECT release_mbid, release_group_mbid
                    FROM album_musicbrainz_links
                    WHERE file_album_id = ?
                    """,
                    ("hiroshi-yoshimura::quiet-forest",),
                ).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(
                    str(row["release_mbid"]),
                    "cc3af5d1-caf1-45c9-9fe8-37b07e77f894",
                )
                self.assertEqual(
                    str(row["release_group_mbid"]),
                    "c4bae335-57a4-4698-938f-eaf5da364bbc",
                )
            finally:
                connection.close()

    def test_musicbrainz_album_artist_tag_value_uses_english_aliases_with_joinphrases(self) -> None:
        from kukicha.use_case.commands.album_edits import (
            MusicBrainzPayload,
            musicbrainz_album_artist_tag_value,
        )

        payload = MusicBrainzPayload(
            entity_type="release",
            mbid="11111111-1111-1111-1111-111111111111",
            payload={
                "artist-credit": [
                    {
                        "artist": {
                            "aliases": [
                                {"locale": "ja", "name": "Yoshimura Hiroshi"},
                                {"locale": "en", "name": "Hiroshi Yoshimura"},
                            ],
                        },
                        "name": "吉村弘",
                        "joinphrase": " & ",
                    },
                    {
                        "artist": {
                            "aliases": [
                                {"locale": "en", "name": "Satoshi Ashikawa"},
                            ],
                        },
                        "name": "芦川聡",
                    },
                ],
            },
        )

        self.assertEqual(
            musicbrainz_album_artist_tag_value(payload),
            "Hiroshi Yoshimura & Satoshi Ashikawa",
        )
        self.assertEqual(
            musicbrainz_album_artist_tag_value(
                payload,
                prefer_english_aliases=False,
            ),
            "吉村弘 & 芦川聡",
        )

    def test_edit_library_album_musicbrainz_writes_each_group_from_its_payload(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            paths = (
                temp_path / "kukicha_test_lib" / "Selected Ambient Works Volume II" / "01.mp3",
                temp_path / "kukicha_test_lib2" / "Selected Ambient Works, Volume II" / "01.mp3",
            )
            self.seed_album(database, paths)

            job = prepare_album_musicbrainz_edit_job(
                database,
                "old-artist::album",
                {
                    "groups": [
                        {
                            "metadata_url": (
                                "https://musicbrainz.org/release/"
                                "11111111-1111-1111-1111-111111111111"
                            ),
                            "track_ids": [1],
                        },
                        {
                            "metadata_url": (
                                "https://musicbrainz.org/release/"
                                "22222222-2222-2222-2222-222222222222"
                            ),
                            "track_ids": [2],
                        },
                    ],
                },
            )

            release_payloads = {
                "11111111-1111-1111-1111-111111111111": {
                    "title": "Selected Ambient Works Volume II",
                    "artist-credit": [{"name": "Aphex Twin"}],
                    "genres": [{"name": "electronic", "count": 3}],
                    "release-group": {"id": "33333333-3333-3333-3333-333333333333"},
                },
                "22222222-2222-2222-2222-222222222222": {
                    "title": "Selected Ambient Works Volume II",
                    "disambiguation": "expanded edition",
                    "artist-credit": [{"name": "Aphex Twin"}],
                    "genres": [{"name": "electronic", "count": 3}],
                    "release-group": {"id": "44444444-4444-4444-4444-444444444444"},
                },
            }
            release_group_payloads = {
                "33333333-3333-3333-3333-333333333333": {
                    "title": "Selected Ambient Works Volume II",
                    "artist-credit": [{"name": "Aphex Twin"}],
                    "genres": [{"name": "ambient", "count": 2}],
                },
                "44444444-4444-4444-4444-444444444444": {
                    "title": "Selected Ambient Works Volume II",
                    "artist-credit": [{"name": "Aphex Twin"}],
                    "genres": [{"name": "ambient", "count": 2}],
                },
            }

            def fake_get_musicbrainz_entity(
                _connection: object,
                _client: object,
                *,
                entity_type: str,
                mbid: str,
            ) -> dict[str, object]:
                if entity_type == "release":
                    return release_payloads[mbid]
                self.assertEqual(entity_type, "release-group")
                return release_group_payloads[mbid]

            with (
                patch(
                    "kukicha.use_case.commands.album_edits.get_musicbrainz_entity",
                    side_effect=fake_get_musicbrainz_entity,
                ),
                patch("kukicha.use_case.commands.album_edits.write_album_audio_tags") as write_album_tags,
            ):
                result = edit_library_album_musicbrainz(database, job)

            self.assertEqual(
                write_album_tags.call_args_list,
                [
                    call(
                        paths[0],
                        album_artist="Aphex Twin",
                        album="Selected Ambient Works Volume II",
                        genre="Electronic; Ambient",
                    ),
                    call(
                        paths[1],
                        album_artist="Aphex Twin",
                        album="Selected Ambient Works Volume II",
                        genre="Electronic; Ambient",
                    ),
                ],
            )
            self.assertEqual(result.tracks_updated, 2)
            self.assertEqual(result.genre_resolution.musicbrainz_album_overrides, 2)

            connection = connect_database(database, create=False)
            try:
                rows = [
                    (
                        str(row["file_album_id"]),
                        str(row["release_mbid"]),
                        str(row["release_group_mbid"]),
                    )
                    for row in connection.execute(
                        """
                        SELECT file_album_id, release_mbid, release_group_mbid
                        FROM album_musicbrainz_links
                        ORDER BY file_album_id, release_mbid
                        """
                    )
                ]
                self.assertEqual(
                    rows,
                    [
                        (
                            "aphex-twin::selected-ambient-works-volume-ii",
                            "11111111-1111-1111-1111-111111111111",
                            "33333333-3333-3333-3333-333333333333",
                        ),
                        (
                            "aphex-twin::selected-ambient-works-volume-ii",
                            "22222222-2222-2222-2222-222222222222",
                            "44444444-4444-4444-4444-444444444444",
                        ),
                    ],
                )
                track_link_rows = [
                    (
                        str(row["path"]),
                        str(row["file_album_id"]),
                        str(row["release_mbid"]),
                        str(row["release_group_mbid"]),
                    )
                    for row in connection.execute(
                        """
                        SELECT path, file_album_id, release_mbid, release_group_mbid
                        FROM album_musicbrainz_track_links
                        ORDER BY path
                        """
                    )
                ]
                self.assertEqual(
                    track_link_rows,
                    [
                        (
                            str(paths[0]),
                            "aphex-twin::selected-ambient-works-volume-ii",
                            "11111111-1111-1111-1111-111111111111",
                            "33333333-3333-3333-3333-333333333333",
                        ),
                        (
                            str(paths[1]),
                            "aphex-twin::selected-ambient-works-volume-ii",
                            "22222222-2222-2222-2222-222222222222",
                            "44444444-4444-4444-4444-444444444444",
                        ),
                    ],
                )
            finally:
                connection.close()

    def test_edit_library_album_musicbrainz_keeps_sibling_release_links(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            base_album_id = "aphex-twin::selected-ambient-works-volume-ii"
            first_release_mbid = "6439fcbe-b404-4cf4-ac58-4816c43cf2e3"
            second_release_mbid = "6d0aaf02-f571-4c03-8677-23018ff628ee"
            release_group_mbid = "0e7a233f-81f8-3e63-ad07-6cdfe2faecc3"
            first_path = temp_path / "amazon" / "Selected Ambient Works Volume II" / "01.mp3"
            second_path = temp_path / "downloaded" / "Selected Ambient Works, Volume II" / "01.flac"

            connection = connect_database(database)
            try:
                connection.execute(
                    "INSERT INTO library_roots (position, root_path) VALUES (?, ?)",
                    (0, str(temp_path)),
                )
                insert_library_album(
                    connection,
                    f"{base_album_id}::09f",
                    "Aphex Twin",
                    "Selected Ambient Works, Volume II",
                    1994,
                    1,
                )
                insert_library_album(
                    connection,
                    f"{base_album_id}::a8b",
                    "Aphex Twin",
                    "Selected Ambient Works, Volume II",
                    2024,
                    1,
                )
                connection.executemany(
                    """
                    INSERT INTO library_tracks (
                        track_id,
                        album_id,
                        root_position,
                        path,
                        file_type,
                        artist,
                        album_artist,
                        album,
                        title,
                        track_number,
                        date,
                        duration_seconds,
                        bitrate
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        (
                            1,
                            f"{base_album_id}::09f",
                            0,
                            str(first_path),
                            "mp3",
                            "Aphex Twin",
                            "Aphex Twin",
                            "Selected Ambient Works, Volume II",
                            "#1",
                            "1",
                            "1994",
                            100.0,
                            128000,
                        ),
                        (
                            2,
                            f"{base_album_id}::a8b",
                            0,
                            str(second_path),
                            "flac",
                            "Aphex Twin",
                            "Aphex Twin",
                            "Selected Ambient Works, Volume II",
                            "[Cliffs]",
                            "1",
                            "2024",
                            100.0,
                            128000,
                        ),
                    ),
                )
                connection.executemany(
                    """
                    INSERT INTO album_musicbrainz_links (
                        file_album_id, release_mbid, release_group_mbid
                    ) VALUES (?, ?, ?)
                    """,
                    (
                        (base_album_id, first_release_mbid, release_group_mbid),
                        (base_album_id, second_release_mbid, release_group_mbid),
                    ),
                )
                connection.executemany(
                    """
                    INSERT INTO album_musicbrainz_track_links (
                        path, file_album_id, release_mbid, release_group_mbid
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        (str(first_path), base_album_id, first_release_mbid, release_group_mbid),
                        (str(second_path), base_album_id, second_release_mbid, release_group_mbid),
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            job = prepare_album_musicbrainz_edit_job(
                database,
                f"{base_album_id}::a8b",
                {"metadata_url": f"https://musicbrainz.org/release/{second_release_mbid}"},
            )
            release_payload = {
                "title": "Selected Ambient Works, Volume II",
                "artist-credit": [{"name": "Aphex Twin"}],
                "genres": [{"name": "electronic", "count": 3}],
                "release-group": {"id": release_group_mbid},
            }
            release_group_payload = {
                "title": "Selected Ambient Works, Volume II",
                "artist-credit": [{"name": "Aphex Twin"}],
                "genres": [{"name": "ambient", "count": 2}],
            }

            def fake_get_musicbrainz_entity(
                _connection: object,
                _client: object,
                *,
                entity_type: str,
                mbid: str,
            ) -> dict[str, object]:
                if entity_type == "release":
                    self.assertEqual(mbid, second_release_mbid)
                    return release_payload
                self.assertEqual(mbid, release_group_mbid)
                return release_group_payload

            with (
                patch(
                    "kukicha.use_case.commands.album_edits.get_musicbrainz_entity",
                    side_effect=fake_get_musicbrainz_entity,
                ),
                patch("kukicha.use_case.commands.album_edits.write_album_audio_tags"),
            ):
                edit_library_album_musicbrainz(database, job)

            connection = connect_database(database, create=False)
            try:
                rows = [
                    (
                        str(row["file_album_id"]),
                        str(row["release_mbid"]),
                        str(row["release_group_mbid"]),
                    )
                    for row in connection.execute(
                        """
                        SELECT file_album_id, release_mbid, release_group_mbid
                        FROM album_musicbrainz_links
                        ORDER BY release_mbid
                        """
                    )
                ]
                self.assertEqual(
                    rows,
                    [
                        (base_album_id, first_release_mbid, release_group_mbid),
                        (base_album_id, second_release_mbid, release_group_mbid),
                    ],
                )
            finally:
                connection.close()

    def test_run_edit_album_musicbrainz_job_uses_tag_edit_completion_message(self) -> None:
        from kukicha.use_case.commands.album_edits import (
            AlbumMusicBrainzEditResult,
            run_edit_album_musicbrainz_job,
        )

        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            self.seed_album(
                database,
                (
                    temp_path / "Album" / "01.mp3",
                    temp_path / "Album" / "02.mp3",
                ),
            )
            job = prepare_album_musicbrainz_edit_job(
                database,
                "old-artist::album",
                {
                    "metadata_url": (
                        "https://musicbrainz.org/release/"
                        "11111111-1111-1111-1111-111111111111"
                    ),
                },
            )
            runtime = Mock()
            runtime.database = database
            runtime.album_artist_split_patterns = DEFAULT_ALBUM_ARTIST_SPLIT_PATTERNS
            runtime.prefer_musicbrainz_english_aliases = False

            with patch(
                "kukicha.use_case.commands.album_edits.edit_library_album_musicbrainz",
                return_value=AlbumMusicBrainzEditResult(
                    album_label="Old Artist - Album",
                    album="Foo",
                    album_artist="Brian Eno & Robert Fripp",
                    genre="Electronic; Ambient",
                    tracks_updated=1,
                    ids_cleared=False,
                    genre_resolution=GenreResolutionStats(),
                ),
            ) as edit_musicbrainz:
                result = run_edit_album_musicbrainz_job(
                    runtime,
                    job,
                    PlayerJobCancelToken(),
                )

            self.assertEqual(
                result.message,
                (
                    "Tags saved for Old Artist - Album. "
                    "Rescan the library to update library filters, artists, and stats."
                ),
            )
            self.assertEqual(result.context["album"], "Foo")
            self.assertEqual(result.context["album_artist"], "Brian Eno & Robert Fripp")
            self.assertEqual(result.context["tracks_updated"], 1)
            self.assertTrue(result.context["rescan_recommended"])
            self.assertEqual(
                edit_musicbrainz.call_args.kwargs["prefer_musicbrainz_english_aliases"],
                False,
            )

    def test_edit_library_album_tags_writes_audio_tags_without_reconciling_database(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            paths = (
                temp_path / "Album" / "01.mp3",
                temp_path / "Album" / "02.mp3",
            )
            self.seed_album(database, paths)
            connection = connect_database(database, create=False)
            try:
                connection.execute(
                    """
                    INSERT INTO album_musicbrainz_links (
                        file_album_id, release_mbid, release_group_mbid
                    ) VALUES (?, ?, ?)
                    """,
                    (
                        "old-artist::album",
                        "11111111-1111-1111-1111-111111111111",
                        "22222222-2222-2222-2222-222222222222",
                    ),
                )
                connection.commit()
            finally:
                connection.close()
            job = prepare_album_tag_edit_job(
                database,
                "old-artist::album",
                {
                    "album": "New Album",
                    "genre": "Electronic; Score",
                    "album_artist": "Various Artists",
                    "tracks": [
                        {
                            "track_id": 1,
                            "artist": "Wendy Carlos & Rachel Elkind",
                            "track_number": "1",
                            "title": "Main Title",
                        },
                        {
                            "track_id": 2,
                            "artist": "The Shining",
                            "track_number": "2",
                            "title": "Rocky Mountains",
                        },
                    ],
                },
            )

            with (
                patch("kukicha.use_case.commands.album_edits.write_track_audio_tags") as write_track_tags,
            ):
                result = edit_library_album_tags(database, job)

            self.assertEqual(
                write_track_tags.call_args_list,
                [
                    call(
                        paths[0],
                        artist="Wendy Carlos & Rachel Elkind",
                        album_artist="Various Artists",
                        album="New Album",
                        track_number="1",
                        title="Main Title",
                        genre="Electronic; Score",
                    ),
                    call(
                        paths[1],
                        artist="The Shining",
                        album_artist="Various Artists",
                        album="New Album",
                        track_number="2",
                        title="Rocky Mountains",
                        genre="Electronic; Score",
                    ),
                ],
            )

            self.assertEqual(result.tracks_updated, 2)
            self.assertEqual(result.albums_scanned, 0)
            self.assertEqual(result.affected_album_ids, ())
            self.assertEqual(result.genre_resolution, GenreResolutionStats())
            self.assertEqual(result.cover_art_resolution, CoverArtResolutionStats())

            connection = connect_database(database, create=False)
            try:
                tracks = list(
                    connection.execute(
                        """
                        SELECT track_id, album_id, artist, album_artist, album, duration_seconds, bitrate
                        FROM library_tracks
                        ORDER BY track_id
                        """
                    )
                )
                self.assertEqual(
                    [
                        (
                            int(row["track_id"]),
                            str(row["album_id"]),
                            str(row["artist"]),
                            str(row["album_artist"]),
                            str(row["album"]),
                            float(row["duration_seconds"]),
                            int(row["bitrate"]),
                        )
                        for row in tracks
                    ],
                    [
                        (1, "old-artist::album", "Artist One", "Old Artist", "Album", 100.0, 128000),
                        (2, "old-artist::album", "Artist Two", "Old Artist", "Album", 120.0, 128000),
                    ],
                )
                self.assertIsNotNone(
                    connection.execute(
                        "SELECT 1 FROM library_albums WHERE album_id = ?",
                        ("old-artist::album",),
                    ).fetchone()
                )
                self.assertIsNone(
                    connection.execute(
                        "SELECT 1 FROM library_albums WHERE album_id = ?",
                        ("various-artists::new-album",),
                    ).fetchone()
                )
                mbid_row = connection.execute(
                    """
                    SELECT release_mbid, release_group_mbid
                    FROM album_musicbrainz_links
                    WHERE file_album_id = ?
                    """,
                    ("old-artist::album",),
                ).fetchone()
                self.assertIsNotNone(mbid_row)
                self.assertEqual(
                    str(mbid_row["release_mbid"]),
                    "11111111-1111-1111-1111-111111111111",
                )
                self.assertEqual(
                    str(mbid_row["release_group_mbid"]),
                    "22222222-2222-2222-2222-222222222222",
                )
            finally:
                connection.close()

    def test_bulk_metadata_edit_rows_prefill_existing_album_url(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            paths = (
                temp_path / "Album" / "01.mp3",
                temp_path / "Album" / "02.mp3",
            )
            self.seed_album(database, paths)
            connection = connect_database(database, create=False)
            try:
                store_album_metadata_link(
                    connection,
                    "old-artist::album",
                    provider="musicbrainz",
                    entity_type="release",
                    entity_id="11111111-1111-1111-1111-111111111111",
                )
                connection.commit()
            finally:
                connection.close()

            api = LibraryQueries(database)
            album = api.get_album("old-artist::album")
            rows = bulk_metadata_edit_rows_for_album(
                SimpleNamespace(database=database),
                album,
                AlbumListQuery(),
                api.library_roots(),
            )

            self.assertEqual(len(rows), 1)
            self.assertEqual(
                rows[0].metadata_url,
                "https://musicbrainz.org/release/11111111-1111-1111-1111-111111111111",
            )
            self.assertFalse(rows[0].metadata_mixed)
            self.assertEqual(rows[0].track_ids, (1, 2))
            self.assertEqual(rows[0].track_count_text, "2 tracks")

    def test_bulk_metadata_edit_rows_show_mixed_track_urls_as_blank(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            paths = (
                temp_path / "Album" / "01.mp3",
                temp_path / "Album" / "02.mp3",
            )
            self.seed_album(database, paths)
            connection = connect_database(database, create=False)
            try:
                store_album_metadata_track_link(
                    connection,
                    str(paths[0]),
                    "old-artist::album",
                    provider="musicbrainz",
                    entity_type="release",
                    entity_id="11111111-1111-1111-1111-111111111111",
                )
                store_album_metadata_track_link(
                    connection,
                    str(paths[1]),
                    "old-artist::album",
                    provider="discogs",
                    entity_type="master",
                    entity_id="12345",
                )
                connection.commit()
            finally:
                connection.close()

            api = LibraryQueries(database)
            album = api.get_album("old-artist::album")
            rows = bulk_metadata_edit_rows_for_album(
                SimpleNamespace(database=database),
                album,
                AlbumListQuery(),
                api.library_roots(),
            )

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].metadata_url, "")
            self.assertTrue(rows[0].metadata_mixed)

    def test_start_album_edit_accepts_tags_only_payload(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            self.seed_album(
                database,
                (
                    temp_path / "Album" / "01.mp3",
                    temp_path / "Album" / "02.mp3",
                ),
            )
            runtime = Mock()
            runtime.database = database
            runtime.enqueue_job.return_value = PlayerJobRecord(
                job_id=11,
                created_at="2026-04-21T10:00:00Z",
                updated_at="2026-04-21T10:00:00Z",
                started_at=None,
                finished_at=None,
                cancel_requested_at=None,
                kind="edit_album",
                status="queued",
                message="Tag edit queued for Old Artist - Album.",
                reason="",
                context={
                    "album": "Album",
                    "album_artist": "Various Artists",
                    "tracks_updated": 2,
                },
            )

            with patch("kukicha.use_case.commands.album_edits.write_track_audio_tags") as write_track_tags:
                result = start_album_edit(
                    runtime,
                    "old-artist::album",
                    {
                        "tags": {
                            "album": "New Album",
                            "genre": "Electronic; Score",
                            "album_artist": "Various Artists",
                            "tracks": [
                                {
                                    "track_id": 1,
                                    "artist": "Wendy Carlos & Rachel Elkind",
                                    "track_number": "1",
                                    "title": "Main Title",
                                },
                                {
                                    "track_id": 2,
                                    "artist": "The Shining",
                                    "track_number": "2",
                                    "title": "Rocky Mountains",
                                },
                            ],
                        },
                    },
                )

            self.assertEqual(result["message"], "Tag edit queued for Old Artist - Album.")
            self.assertEqual(result["job"]["job_id"], 11)
            write_track_tags.assert_not_called()
            runtime.enqueue_job.assert_called_once()
            enqueue_kwargs = runtime.enqueue_job.call_args.kwargs
            self.assertEqual(enqueue_kwargs["kind"], "edit_album")
            self.assertEqual(enqueue_kwargs["queued_message"], "Tag edit queued for Old Artist - Album.")
            self.assertEqual(enqueue_kwargs["running_message"], "Tag edit running for Old Artist - Album.")
            self.assertEqual(enqueue_kwargs["failed_message"], "Tag edit failed for Old Artist - Album.")
            self.assertEqual(enqueue_kwargs["context"]["tracks_updated"], 2)
            self.assertTrue(callable(enqueue_kwargs["runner"]))

    def test_start_album_edit_accepts_musicbrainz_only_payload(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            self.seed_album(
                database,
                (
                    temp_path / "Album" / "01.mp3",
                    temp_path / "Album" / "02.mp3",
                ),
            )
            runtime = Mock()
            runtime.database = database
            runtime.enqueue_job.return_value = PlayerJobRecord(
                job_id=13,
                created_at="2026-04-21T10:00:00Z",
                updated_at="2026-04-21T10:00:00Z",
                started_at=None,
                finished_at=None,
                cancel_requested_at=None,
                kind="edit_album_musicbrainz",
                status="queued",
                message="Tag edit queued for Old Artist - Album.",
                reason="",
                context={
                    "album": "Album",
                    "tracks_updated": 2,
                },
            )

            result = start_album_edit(
                runtime,
                "old-artist::album",
                {
                    "musicbrainz": {
                        "groups": [
                            {
                                "musicbrainz_url": (
                                    "https://musicbrainz.org/release/"
                                    "11111111-1111-1111-1111-111111111111"
                                ),
                                "track_ids": [1, 2],
                            }
                        ],
                    },
                },
            )

            self.assertEqual(result["message"], "Tag edit queued for Old Artist - Album.")
            self.assertEqual(result["job"]["job_id"], 13)
            runtime.enqueue_job.assert_called_once()
            enqueue_kwargs = runtime.enqueue_job.call_args.kwargs
            self.assertEqual(enqueue_kwargs["kind"], "edit_album_musicbrainz")
            self.assertEqual(enqueue_kwargs["queued_message"], "Tag edit queued for Old Artist - Album.")
            self.assertEqual(enqueue_kwargs["context"]["tracks_updated"], 2)
            self.assertTrue(callable(enqueue_kwargs["runner"]))

    def test_start_album_edit_starts_single_background_combined_job(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            self.seed_album(
                database,
                (
                    temp_path / "Album" / "01.mp3",
                    temp_path / "Album" / "02.mp3",
                ),
            )
            runtime = Mock()
            runtime.database = database
            runtime.enqueue_job.return_value = PlayerJobRecord(
                job_id=12,
                created_at="2026-04-21T10:00:00Z",
                updated_at="2026-04-21T10:00:00Z",
                started_at=None,
                finished_at=None,
                cancel_requested_at=None,
                kind="edit_album",
                status="queued",
                message="Tag edit queued for Old Artist - Album.",
                reason="",
                context={
                    "album": "Album",
                    "album_artist": "Various Artists",
                    "tracks_updated": 2,
                },
            )

            result = start_album_edit(
                runtime,
                "old-artist::album",
                {
                    "tags": {
                        "album": "New Album",
                        "genre": "Electronic; Score",
                        "album_artist": "Various Artists",
                        "tracks": [
                            {
                                "track_id": 1,
                                "artist": "Wendy Carlos & Rachel Elkind",
                                "track_number": "1",
                                "title": "Main Title",
                            },
                            {
                                "track_id": 2,
                                "artist": "The Shining",
                                "track_number": "2",
                                "title": "Rocky Mountains",
                            },
                        ],
                    },
                    "musicbrainz": {
                        "groups": [
                            {
                                "musicbrainz_url": (
                                    "https://musicbrainz.org/release/"
                                    "11111111-1111-1111-1111-111111111111"
                                ),
                                "track_ids": [1, 2],
                            }
                        ],
                    },
                },
            )

            self.assertEqual(result["message"], "Tag edit queued for Old Artist - Album.")
            self.assertEqual(result["job"]["job_id"], 12)
            runtime.enqueue_job.assert_called_once()
            enqueue_kwargs = runtime.enqueue_job.call_args.kwargs
            self.assertEqual(enqueue_kwargs["kind"], "edit_album")
            self.assertEqual(enqueue_kwargs["queued_message"], "Tag edit queued for Old Artist - Album.")
            self.assertEqual(enqueue_kwargs["context"]["tracks_updated"], 2)
            self.assertTrue(callable(enqueue_kwargs["runner"]))

    def test_start_bulk_album_metadata_edit_queues_single_background_job(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            runtime = Mock()
            runtime.database = database
            runtime.enqueue_job.return_value = PlayerJobRecord(
                job_id=14,
                created_at="2026-04-21T10:00:00Z",
                updated_at="2026-04-21T10:00:00Z",
                started_at=None,
                finished_at=None,
                cancel_requested_at=None,
                kind="bulk_album_metadata_urls",
                status="queued",
                message="Bulk metadata URL edit queued.",
                reason="",
                context={
                    "rows_changed": 2,
                },
            )

            result = start_bulk_album_metadata_edit(
                runtime,
                {
                    "rows": [
                        {
                            "album_id": "old-artist::album",
                            "metadata_url": "https://www.discogs.com/release/123",
                            "track_ids": [1, 2],
                        },
                        {
                            "album_id": "other-artist::album",
                            "metadata_url": "",
                            "loaded_metadata_url": "https://www.discogs.com/master/456",
                            "track_ids": [3],
                        },
                    ],
                },
            )

            self.assertEqual(result["message"], "Bulk metadata URL edit queued.")
            self.assertEqual(result["job"]["job_id"], 14)
            runtime.enqueue_job.assert_called_once()
            enqueue_kwargs = runtime.enqueue_job.call_args.kwargs
            self.assertEqual(enqueue_kwargs["kind"], "bulk_album_metadata_urls")
            self.assertEqual(enqueue_kwargs["queued_message"], "Bulk metadata URL edit queued.")
            self.assertEqual(enqueue_kwargs["running_message"], "Bulk metadata URL edit running.")
            self.assertEqual(enqueue_kwargs["failed_message"], "Bulk metadata URL edit failed.")
            self.assertEqual(enqueue_kwargs["context"]["rows_changed"], 2)
            self.assertTrue(callable(enqueue_kwargs["runner"]))

    def test_run_edit_album_job_writes_tags_and_publishes_rescan_recommendation(self) -> None:
        from kukicha.use_case.commands.album_edits import run_edit_album_job

        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            paths = (
                temp_path / "Album" / "01.mp3",
                temp_path / "Album" / "02.mp3",
            )
            self.seed_album(database, paths)
            job = prepare_album_tag_edit_job(
                database,
                "old-artist::album",
                {
                    "album": "New Album",
                    "genre": "Electronic; Score",
                    "album_artist": "Various Artists",
                    "tracks": [
                        {
                            "track_id": 1,
                            "artist": "Wendy Carlos & Rachel Elkind",
                            "track_number": "1",
                            "title": "Main Title",
                        },
                        {
                            "track_id": 2,
                            "artist": "The Shining",
                            "track_number": "2",
                            "title": "Rocky Mountains",
                        },
                    ],
                },
            )
            runtime = Mock()
            runtime.database = database

            with patch("kukicha.use_case.commands.album_edits.write_track_audio_tags") as write_track_tags:
                result = run_edit_album_job(runtime, job, PlayerJobCancelToken())

            self.assertEqual(
                write_track_tags.call_args_list,
                [
                    call(
                        paths[0],
                        artist="Wendy Carlos & Rachel Elkind",
                        album_artist="Various Artists",
                        album="New Album",
                        track_number="1",
                        title="Main Title",
                        genre="Electronic; Score",
                    ),
                    call(
                        paths[1],
                        artist="The Shining",
                        album_artist="Various Artists",
                        album="New Album",
                        track_number="2",
                        title="Rocky Mountains",
                        genre="Electronic; Score",
                    ),
                ],
            )
            self.assertEqual(
                result.message,
                (
                    "Tags saved for Old Artist - Album. "
                    "Rescan the library to update library filters, artists, and stats."
                ),
            )
            self.assertEqual(result.context["tracks_updated"], 2)
            self.assertTrue(result.context["rescan_recommended"])

    def test_run_bulk_album_metadata_edit_job_isolates_row_failures(self) -> None:
        from kukicha.use_case.commands.album_edits import (
            BulkAlbumMetadataEditJob,
            BulkAlbumMetadataEditRowRequest,
            run_bulk_album_metadata_edit_job,
        )

        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            paths = (
                temp_path / "Album" / "01.mp3",
                temp_path / "Album" / "02.mp3",
            )
            self.seed_album(database, paths)
            connection = connect_database(database, create=False)
            try:
                store_album_metadata_track_link(
                    connection,
                    str(paths[1]),
                    "old-artist::album",
                    provider="discogs",
                    entity_type="master",
                    entity_id="12345",
                )
                connection.commit()
            finally:
                connection.close()

            runtime = SimpleNamespace(
                database=database,
                album_artist_split_patterns=DEFAULT_ALBUM_ARTIST_SPLIT_PATTERNS,
                prefer_musicbrainz_english_aliases=True,
            )
            job = BulkAlbumMetadataEditJob(
                rows=(
                    BulkAlbumMetadataEditRowRequest(
                        album_id="old-artist::album",
                        track_ids=(1,),
                        metadata_url="https://example.com/not-supported",
                        album_label="Old Artist - Album",
                        group_label="Track 1",
                    ),
                    BulkAlbumMetadataEditRowRequest(
                        album_id="old-artist::album",
                        track_ids=(2,),
                        metadata_url="",
                        loaded_metadata_url="https://www.discogs.com/master/12345",
                        album_label="Old Artist - Album",
                        group_label="Track 2",
                    ),
                )
            )

            result = run_bulk_album_metadata_edit_job(
                runtime,
                job,
                PlayerJobCancelToken(),
            )

            self.assertIn("0 updated, 1 cleared, 0 skipped, 1 failed", result.message)
            self.assertEqual(result.context["rows_cleared"], 1)
            self.assertEqual(result.context["rows_failed"], 1)
            self.assertIn("Old Artist - Album (Track 1)", result.context["failed_rows"])
            self.assertIn("Expected a MusicBrainz or Discogs URL.", result.context["failed_rows"])
            connection = connect_database(database, create=False)
            try:
                remaining = connection.execute(
                    "SELECT COUNT(*) AS count FROM album_metadata_track_links"
                ).fetchone()
                self.assertEqual(int(remaining["count"]), 0)
            finally:
                connection.close()

    def test_edit_library_album_edit_applies_musicbrainz_after_manual_tags(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            paths = (
                temp_path / "Album" / "01.mp3",
                temp_path / "Album" / "02.mp3",
            )
            self.seed_album(database, paths)
            job = prepare_album_edit_job(
                database,
                "old-artist::album",
                {
                    "tags": {
                        "album": "Manual Album",
                        "genre": "Manual Genre",
                        "album_artist": "Manual Artist",
                        "tracks": [
                            {
                                "track_id": 1,
                                "artist": "Manual Track Artist 1",
                                "track_number": "1",
                                "title": "Manual Title 1",
                            },
                            {
                                "track_id": 2,
                                "artist": "Manual Track Artist 2",
                                "track_number": "2",
                                "title": "Manual Title 2",
                            },
                        ],
                    },
                    "musicbrainz": {
                        "groups": [
                            {
                                "musicbrainz_url": (
                                    "https://musicbrainz.org/release/"
                                    "11111111-1111-1111-1111-111111111111"
                                ),
                                "track_ids": [1, 2],
                            }
                        ],
                    },
                },
            )
            release_payload = {
                "title": "MusicBrainz Album",
                "artist-credit": [
                    {"name": "Brian Eno", "joinphrase": " & "},
                    {"name": "Robert Fripp"},
                ],
                "genres": [{"name": "electronic", "count": 3}],
                "release-group": {"id": "22222222-2222-2222-2222-222222222222"},
            }
            release_group_payload = {
                "title": "MusicBrainz Album",
                "artist-credit": [{"name": "Ignored Artist"}],
                "genres": [{"name": "ambient", "count": 2}],
            }

            def fake_get_musicbrainz_entity(
                _connection: object,
                _client: object,
                *,
                entity_type: str,
                mbid: str,
            ) -> dict[str, object]:
                if entity_type == "release":
                    self.assertEqual(mbid, "11111111-1111-1111-1111-111111111111")
                    return release_payload
                self.assertEqual(entity_type, "release-group")
                self.assertEqual(mbid, "22222222-2222-2222-2222-222222222222")
                return release_group_payload

            write_events: list[tuple[str, str, dict[str, object]]] = []

            def record_track_write(path: Path, **kwargs: object) -> None:
                write_events.append(("manual", str(path), kwargs))

            def record_musicbrainz_write(path: Path, **kwargs: object) -> None:
                write_events.append(("musicbrainz", str(path), kwargs))

            with (
                patch(
                    "kukicha.use_case.commands.album_edits.get_musicbrainz_entity",
                    side_effect=fake_get_musicbrainz_entity,
                ),
                patch(
                    "kukicha.use_case.commands.album_edits.write_track_audio_tags",
                    side_effect=record_track_write,
                ),
                patch(
                    "kukicha.use_case.commands.album_edits.write_album_audio_tags",
                    side_effect=record_musicbrainz_write,
                ),
            ):
                result = edit_library_album_edit(database, job)

            self.assertEqual(
                [event[0] for event in write_events],
                ["manual", "manual", "musicbrainz", "musicbrainz"],
            )
            self.assertEqual(write_events[0][1], str(paths[0]))
            self.assertEqual(write_events[2][1], str(paths[0]))
            self.assertEqual(write_events[0][2]["album"], "Manual Album")
            self.assertEqual(write_events[2][2]["album"], "MusicBrainz Album")
            self.assertEqual(
                write_events[2][2]["album_artist"],
                "Brian Eno & Robert Fripp",
            )
            self.assertEqual(write_events[2][2]["genre"], "Electronic; Ambient")
            self.assertEqual(result.album, "MusicBrainz Album")
            self.assertEqual(result.album_artist, "Brian Eno & Robert Fripp")
            self.assertEqual(result.tracks_updated, 2)
            self.assertFalse(result.musicbrainz_ids_cleared)

    def test_edit_library_album_tags_does_not_touch_database_when_tag_write_fails(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            paths = (
                temp_path / "Album" / "01.mp3",
                temp_path / "Album" / "02.mp3",
            )
            self.seed_album(database, paths)
            job = prepare_album_tag_edit_job(
                database,
                "old-artist::album",
                {
                    "album": "New Album",
                    "genre": "Electronic; Score",
                    "album_artist": "Various Artists",
                    "tracks": [
                        {
                            "track_id": 1,
                            "artist": "Wendy Carlos & Rachel Elkind",
                            "track_number": "1",
                            "title": "Main Title",
                        },
                        {
                            "track_id": 2,
                            "artist": "The Shining",
                            "track_number": "2",
                            "title": "Rocky Mountains",
                        },
                    ],
                },
            )

            with patch("kukicha.use_case.commands.album_edits.write_track_audio_tags", side_effect=OSError("boom")):
                with self.assertRaisesRegex(OSError, "boom"):
                    edit_library_album_tags(database, job)

            connection = connect_database(database, create=False)
            try:
                rows = list(
                    connection.execute(
                        """
                        SELECT track_id, album_id, artist, album_artist
                        FROM library_tracks
                        ORDER BY track_id
                        """
                    )
                )
                self.assertEqual(
                    [
                        (
                            int(row["track_id"]),
                            str(row["album_id"]),
                            str(row["artist"]),
                            str(row["album_artist"]),
                        )
                        for row in rows
                    ],
                    [
                        (1, "old-artist::album", "Artist One", "Old Artist"),
                        (2, "old-artist::album", "Artist Two", "Old Artist"),
                    ],
                )
                self.assertIsNotNone(
                    connection.execute(
                        "SELECT 1 FROM library_albums WHERE album_id = ?",
                        ("old-artist::album",),
                    ).fetchone()
                )
                self.assertIsNone(
                    connection.execute(
                        "SELECT 1 FROM library_albums WHERE album_id = ?",
                        ("various-artists::new-album",),
                    ).fetchone()
                )
            finally:
                connection.close()

    def test_prepare_album_tag_edit_job_rejects_blank_album_title(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            paths = (
                temp_path / "Album" / "01.mp3",
                temp_path / "Album" / "02.mp3",
            )
            self.seed_album(database, paths)

            with self.assertRaisesRegex(ValueError, "album title is required"):
                prepare_album_tag_edit_job(
                    database,
                    "old-artist::album",
                    {
                        "album": "",
                        "genre": "Electronic; Score",
                        "album_artist": "Various Artists",
                        "tracks": [
                            {
                                "track_id": 1,
                                "artist": "Wendy Carlos & Rachel Elkind",
                                "track_number": "1",
                                "title": "Main Title",
                            },
                            {
                                "track_id": 2,
                                "artist": "The Shining",
                                "track_number": "2",
                                "title": "Rocky Mountains",
                            },
                        ],
                    },
                )

    def test_prepare_album_tag_edit_job_rejects_blank_track_title(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            paths = (
                temp_path / "Album" / "01.mp3",
                temp_path / "Album" / "02.mp3",
            )
            self.seed_album(database, paths)

            with self.assertRaisesRegex(ValueError, "track requires title: Track One"):
                prepare_album_tag_edit_job(
                    database,
                    "old-artist::album",
                    {
                        "album": "New Album",
                        "genre": "Electronic; Score",
                        "album_artist": "Various Artists",
                        "tracks": [
                            {
                                "track_id": 1,
                                "artist": "Wendy Carlos & Rachel Elkind",
                                "track_number": "1",
                                "title": "",
                            },
                            {
                                "track_id": 2,
                                "artist": "The Shining",
                                "track_number": "2",
                                "title": "Rocky Mountains",
                            },
                        ],
                    },
                )

    def test_edit_library_album_tags_supports_per_track_titles(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            paths = (
                temp_path / "Album" / "01.mp3",
                temp_path / "Album" / "02.mp3",
            )
            self.seed_album(database, paths)
            job = prepare_album_tag_edit_job(
                database,
                "old-artist::album",
                {
                    "album": "New Album",
                    "genre": "Electronic; Score",
                    "album_artist": "Various Artists",
                    "tracks": [
                        {
                            "track_id": 1,
                            "artist": "Artist Alpha",
                            "track_number": "1",
                            "title": "Title Alpha",
                        },
                        {
                            "track_id": 2,
                            "artist": "Artist Beta",
                            "track_number": "2",
                            "title": "Title Beta",
                        },
                    ],
                },
            )

            with patch("kukicha.use_case.commands.album_edits.write_track_audio_tags") as write_track_tags:
                result = edit_library_album_tags(database, job)

            self.assertEqual(
                write_track_tags.call_args_list,
                [
                    call(
                        paths[0],
                        artist="Artist Alpha",
                        album_artist="Various Artists",
                        album="New Album",
                        track_number="1",
                        title="Title Alpha",
                        genre="Electronic; Score",
                    ),
                    call(
                        paths[1],
                        artist="Artist Beta",
                        album_artist="Various Artists",
                        album="New Album",
                        track_number="2",
                        title="Title Beta",
                        genre="Electronic; Score",
                    ),
                ],
            )
            self.assertEqual(result.tracks_updated, 2)
            self.assertEqual(result.albums_scanned, 0)
            self.assertEqual(result.affected_album_ids, ())

            connection = connect_database(database, create=False)
            try:
                track_rows = list(
                    connection.execute(
                        """
                        SELECT track_id, album_id, artist, album_artist, album
                        FROM library_tracks
                        ORDER BY track_id
                        """
                    )
                )
                self.assertEqual(
                    [
                        (
                            int(row["track_id"]),
                            str(row["album_id"]),
                            str(row["artist"]),
                            str(row["album_artist"]),
                            str(row["album"]),
                        )
                        for row in track_rows
                    ],
                    [
                        (
                            1,
                            "old-artist::album",
                            "Artist One",
                            "Old Artist",
                            "Album",
                        ),
                        (
                            2,
                            "old-artist::album",
                            "Artist Two",
                            "Old Artist",
                            "Album",
                        ),
                    ],
                )
                self.assertIsNotNone(
                    connection.execute(
                        "SELECT 1 FROM library_albums WHERE album_id = ?",
                        ("old-artist::album",),
                    ).fetchone()
                )
                self.assertIsNone(
                    connection.execute(
                        "SELECT 1 FROM library_albums WHERE album_id = ?",
                        ("various-artists::album-alpha",),
                    ).fetchone()
                )
                self.assertIsNone(
                    connection.execute(
                        "SELECT 1 FROM library_albums WHERE album_id = ?",
                        ("various-artists::album-beta",),
                    ).fetchone()
                )
            finally:
                connection.close()

    def test_prepare_album_musicbrainz_edit_request_rejects_non_string_metadata_url(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            paths = (
                temp_path / "Album" / "01.mp3",
                temp_path / "Album" / "02.mp3",
            )
            self.seed_album(database, paths)

            with self.assertRaisesRegex(ValueError, "Metadata URL must be a string"):
                prepare_album_musicbrainz_edit_request(
                    database,
                    "old-artist::album",
                    {
                        "metadata_url": 123,
                    },
                )


class AlbumTrackSectionsTest(unittest.TestCase):
    def test_single_root_uses_one_unlabeled_table(self) -> None:
        tracks = [
            make_track_view(
                1,
                root_position=0,
                path="/music/a/Aphex Twin/Selected Ambient Works Volume II/01.mp3",
            ),
            make_track_view(
                2,
                root_position=0,
                path="/music/a/Aphex Twin/Selected Ambient Works Volume II/02.mp3",
            ),
        ]

        sections = album_track_sections(
            tracks,
            (LibraryRootFilterOption(position=0, path="/music/a", label=".../a"),),
        )

        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0].label, "")
        self.assertEqual(sections[0].meta, ("2 tracks",))
        self.assertEqual([row.track.track_id for row in sections[0].table_rows], [1, 2])

    def test_multiple_roots_use_labeled_tables_in_root_order(self) -> None:
        tracks = [
            make_track_view(
                2,
                root_position=1,
                path="/music/rerelease/Aphex Twin/Selected Ambient Works Volume II/01.mp3",
            ),
            make_track_view(
                1,
                root_position=0,
                path="/music/original/Aphex Twin/Selected Ambient Works Volume II/01.mp3",
            ),
            make_track_view(
                3,
                root_position=1,
                path="/music/rerelease/Aphex Twin/Selected Ambient Works Volume II/02.mp3",
            ),
        ]

        sections = album_track_sections(
            tracks,
            (
                LibraryRootFilterOption(position=0, path="/music/original", label=".../original"),
                LibraryRootFilterOption(position=1, path="/music/rerelease", label=".../rerelease"),
            ),
        )

        self.assertEqual(
            [section.label for section in sections],
            [
                ".../original/Aphex Twin/Selected Ambient Works Volume II/",
                ".../rerelease/Aphex Twin/Selected Ambient Works Volume II/",
            ],
        )
        self.assertEqual([section.meta for section in sections], [("1 track",), ("2 tracks",)])
        self.assertEqual([row.track.track_id for row in sections[0].table_rows], [1])
        self.assertEqual([row.track.track_id for row in sections[1].table_rows], [2, 3])

    def test_duplicate_root_labels_fall_back_to_full_paths(self) -> None:
        tracks = [
            make_track_view(
                1,
                root_position=0,
                path="/mnt/a/music/Aphex Twin/Selected Ambient Works Volume II/01.mp3",
            ),
            make_track_view(
                2,
                root_position=1,
                path="/mnt/b/music/Aphex Twin/Selected Ambient Works Volume II/01.mp3",
            ),
        ]

        sections = album_track_sections(
            tracks,
            (
                LibraryRootFilterOption(position=0, path="/mnt/a/music", label=".../music"),
                LibraryRootFilterOption(position=1, path="/mnt/b/music", label=".../music"),
            ),
        )

        self.assertEqual(
            [section.label for section in sections],
            [
                "/mnt/a/music/Aphex Twin/Selected Ambient Works Volume II/",
                "/mnt/b/music/Aphex Twin/Selected Ambient Works Volume II/",
            ],
        )

    def test_same_root_collisions_use_relative_release_paths(self) -> None:
        tracks = [
            make_track_view(
                1,
                root_position=0,
                path="/music/downloaded/Bricolage/01.mp3",
                album_artist="Amon Tobin",
                album="Bricolage",
            ),
            make_track_view(
                2,
                root_position=0,
                path="/music/downloaded/Bricolage/02.mp3",
                album_artist="Amon Tobin",
                album="Bricolage",
            ),
            make_track_view(
                3,
                root_position=0,
                path="/music/downloaded/Amon Tobin/Bricolage/01.mp3",
                album_artist="Amon Tobin",
                album="Bricolage",
            ),
            make_track_view(
                4,
                root_position=0,
                path="/music/downloaded/Amon Tobin/Bricolage/02.mp3",
                album_artist="Amon Tobin",
                album="Bricolage",
            ),
        ]

        sections = album_track_sections(
            tracks,
            (LibraryRootFilterOption(position=0, path="/music/downloaded", label=".../downloaded"),),
        )

        self.assertEqual(
            [section.label for section in sections],
            [
                ".../downloaded/Bricolage/",
                ".../downloaded/Amon Tobin/Bricolage/",
            ],
        )
        self.assertEqual([row.track.track_id for row in sections[0].table_rows], [1, 2])
        self.assertEqual([row.track.track_id for row in sections[1].table_rows], [3, 4])

    def test_disc_subdirectories_stay_grouped_by_album_directory(self) -> None:
        tracks = [
            make_track_view(
                1,
                root_position=0,
                path="/music/a/Aphex Twin/Selected Ambient Works Volume II/Disc 1/01.mp3",
            ),
            make_track_view(
                2,
                root_position=0,
                path="/music/a/Aphex Twin/Selected Ambient Works Volume II/Disc 2/01.mp3",
            ),
        ]

        sections = album_track_sections(
            tracks,
            (LibraryRootFilterOption(position=0, path="/music/a", label=".../a"),),
        )

        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0].label, "")
        self.assertEqual([row.track.track_id for row in sections[0].table_rows], [1, 2])

    def test_album_tag_edit_sections_use_section_level_defaults(self) -> None:
        tracks = [
            make_track_view(
                1,
                root_position=0,
                path="/music/downloads/Unknown/01.mp3",
                album_artist="__Unknown",
                album="__Unknown",
                genres=("__Unknown",),
            ),
            make_track_view(
                2,
                root_position=0,
                path="/music/downloads/Artist/Album/02.mp3",
                album_artist="Artist",
                album="Album",
                genres=("Electronic",),
                styles=("Ambient",),
            ),
            make_track_view(
                3,
                root_position=0,
                path="/music/downloads/Artist/Album/01.mp3",
                album_artist="Artist",
                album="Album",
                track_number="1",
                genres=("Electronic",),
                styles=("Ambient",),
            ),
        ]

        sections = album_tag_edit_sections(
            tracks,
            (LibraryRootFilterOption(position=0, path="/music/downloads", label=".../downloads"),),
        )

        self.assertEqual(
            [section.label for section in sections],
            [".../downloads/Unknown/", ".../downloads/Artist/Album/"],
        )
        self.assertEqual([section.album for section in sections], ["__Unknown", "Album"])
        self.assertEqual([section.album_artist for section in sections], ["__Unknown", "Artist"])
        self.assertEqual([section.genre for section in sections], ["__Unknown", "Electronic; Ambient"])
        self.assertEqual([item.track.track_id for item in sections[0].tracks], [1])
        self.assertEqual([item.track.track_id for item in sections[1].tracks], [2, 3])
        self.assertEqual([item.track_number for item in sections[0].tracks], ["1"])
        self.assertEqual([item.track_number for item in sections[1].tracks], ["2", "1"])

    def test_album_tag_edit_tracks_preserve_alphanumeric_track_numbers(self) -> None:
        tracks = [
            make_track_view(
                1,
                root_position=0,
                path="/music/Artist/Album/B3.flac",
                track_number="B3",
            ),
            make_track_view(
                2,
                root_position=0,
                path="/music/Artist/Album/A1.flac",
                track_number="A1",
            ),
        ]

        section = album_tag_edit_section_for_tracks(tracks)

        self.assertEqual([item.track_number for item in section.tracks], ["B3", "A1"])

    def test_album_tag_edit_tracks_number_untagged_tracks_by_filename_order(self) -> None:
        tracks = [
            make_track_view(
                1,
                root_position=0,
                path="/music/Artist/Album/02.flac",
                track_number="",
            ),
            make_track_view(
                2,
                root_position=0,
                path="/music/Artist/Album/01.flac",
                track_number="",
            ),
        ]

        section = album_tag_edit_section_for_tracks(tracks)

        self.assertEqual([item.track.track_id for item in section.tracks], [1, 2])
        self.assertEqual([item.track_number for item in section.tracks], ["2", "1"])


def make_track_view(
    track_id: int,
    *,
    root_position: int | None,
    path: str,
    album_id: str = "aphex-twin::selected-ambient-works-volume-ii",
    album_artist: str = "Aphex Twin",
    album_artists: tuple[str, ...] | None = None,
    artist: str | None = None,
    album: str = "Selected Ambient Works Volume II",
    track_number: str | None = None,
    genres: tuple[str, ...] = (),
    styles: tuple[str, ...] = (),
    duration_seconds: float | None = None,
    library_track_id: int | None = None,
    playlist_options: tuple[PlaylistMenuOption, ...] | None = None,
) -> TrackView:
    resolved_artist = album_artist if artist is None else artist
    return TrackView(
        track_id=track_id,
        album_id=album_id,
        root_position=root_position,
        path=path,
        audio_url=f"/audio/{track_id}",
        art_url=f"/art/32/{track_id}",
        album_art_url=f"/art/250/{track_id}",
        audio_codec="",
        audio_mime_type="audio/mpeg",
        audio_unsupported_reason="",
        file_type="mp3",
        album_artist=album_artist,
        album_artists=album_artists or ((album_artist,) if album_artist else ()),
        album=album,
        display_album=album,
        artist=resolved_artist,
        title=f"Track {track_id}",
        display_title=f"Track {track_id}",
        table_title=f"Track {track_id}",
        queue_title=f"{album_artist} - Track {track_id}",
        track_number=str(track_id) if track_number is None else track_number,
        disc_number="",
        disc_total="",
        year=None,
        duration="",
        duration_seconds=duration_seconds,
        duration_is_indeterminate=False,
        grouping="",
        genres=genres,
        styles=styles,
        library_track_id=library_track_id,
        playlist_options=playlist_options,
    )


if __name__ == "__main__":
    unittest.main()
