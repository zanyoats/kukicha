from __future__ import annotations

import io
import os
from pathlib import Path
from queue import Queue
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, call, patch
from urllib.parse import parse_qs

from kukicha.album_artists import DEFAULT_ALBUM_ARTIST_SPLIT_PATTERNS
from kukicha.use_case import (
    ALBUM_LIST_SORT_ARTIST,
    ALBUM_LIST_SORT_RECENTLY_ADDED,
    AlbumDetails,
    AlbumListQuery,
    GenreFilterGroup,
    GenreStyleFilter,
    LibraryAlbumArtistStats,
    LibraryFilterOptions,
    LibraryQueries,
    LibraryRootFilterOption,
    PlaylistDetails,
    PlaylistItem,
    PlaylistTrack,
)
from kukicha.cli import build_parser
from kukicha.use_case import connect_database
from kukicha.use_case import CoverArtResolutionStats, GenreResolutionStats, save_library
from kukicha.models import MusicLibrary, PlaylistItemRecord, PlaylistRecord, TrackArtwork, TrackRecord
from kukicha.player_jobs import (
    job_payload,
)

from kukicha.player_config import (
    DEFAULT_LINKED_TOAST_TIMEOUT_MS,
    DEFAULT_PLAYER_HOST,
    DEFAULT_PLAYER_LOG_LEVEL,
    DEFAULT_PLAYER_PORT,
    DEFAULT_TOAST_TIMEOUT_MS,
    PlayerServerOptions,
    build_template_environment,
    load_player_options,
    player_config_help_text,
    validate_player_startup,
)
from kukicha.use_case import (
    create_library_root,
    edit_library_album_musicbrainz,
    edit_library_album_tags,
    delete_library_root,
    library_job_detail_lines,
    library_job_summary_text,
    library_scan_progress_text,
    create_player_job,
    get_player_job,
    list_player_jobs,
    mark_stale_player_jobs_canceled,
    playlist_menu_options_by_track_id,
    prepare_album_musicbrainz_edit_job,
    prepare_album_musicbrainz_edit_request,
    prepare_album_tag_edit_job,
    rescan_library_root,
    scan_library_with_new_root,
    set_track_playlist_membership,
    set_track_playlist_membership_database,
    start_album_tag_edit,
    update_player_job,
    update_queue as update_queue_command,
)
from kukicha.player_errors import PlayerConfigError, PlayerConflictError
from kukicha.player_media import audio_mime_type
from kukicha.player_navigation import (
    album_artist_links,
    album_genre_links,
    album_index_url,
    album_meta_query,
    album_root_links,
    album_style_links,
    artist_cloud_links,
    player_page_heading,
    player_page_menu_items,
)
from kukicha.use_case import album_list_query_from_params
from kukicha.player_platform import choose_directory_path
from kukicha.player_playlists import (
    update_playlist_file_for_membership,
)
from kukicha.player_presenters import (
    PlaylistMenuOption,
    TrackView,
    album_playback_track_payloads,
    album_track_sections,
    normalized_queue_state,
    playlist_item_view,
    queue_meta_text,
    reset_queue_state,
    track_playback_payload,
    track_view,
    valid_playback_ids,
)

from kukicha.player_runtime import (
    PlayerJobCancelToken,
    PlayerJobRecord,
    PlayerJobResult,
    PlayerQueueState,
    PlayerRuntime,
)
from kukicha.player_web_adapter import create_player_app
from kukicha.playlist_art import playlist_cover_data_url, playlist_cover_svg


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


class PlayerQueueStateTest(unittest.TestCase):
    def test_reset_queue_state_clears_queue_and_unloads_track(self) -> None:
        state = PlayerQueueState(
            track_ids=[101, 102, 103],
            position=1,
            loaded_track_id=102,
            paused=False,
        )

        reset_queue_state(state)

        self.assertEqual(state.track_ids, [])
        self.assertEqual(state.position, 0)
        self.assertIsNone(state.loaded_track_id)
        self.assertTrue(state.paused)

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
        runtime = PlayerRuntime(Path("/tmp/kukicha-test.sqlite"))
        api = Mock()
        api.get_tracks_by_ids.return_value = [
            PlaylistTrack(
                track_id=101,
                album_id="artist::album",
                path="/music/Artist/Album/01.flac",
                artist="Artist",
                album_artist="Artist",
                album="Album",
                title="One",
            ),
        ]
        api.get_playlist_items_by_ids.return_value = []

        with patch("kukicha.use_case.commands.player.LibraryQueries", return_value=api):
            payload = update_queue_command(
                runtime,
                {
                    "track_ids": [101, 999, "not-an-int"],
                    "position": 0,
                    "loaded_track_id": 101,
                    "paused": False,
                }
            )

        self.assertEqual(payload["track_ids"], [101])
        self.assertEqual(payload["loaded_track_id"], 101)
        self.assertFalse(payload["paused"])

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
                kind="rescan_root",
                status="queued",
                message="Rescan queued for Music.",
                reason="",
                context={"path": "/Volumes/Music", "root_position": 0},
            )
        )

        payload = subscriber.get_nowait()
        self.assertEqual(payload["job_id"], 7)
        self.assertEqual(payload["kind"], "rescan_root")

    def test_enqueued_job_runs_and_updates_status(self) -> None:
        with TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "kukicha.sqlite"
            connection = connect_database(database)
            connection.close()
            runtime = PlayerRuntime(database)

            with patch.object(runtime, "ensure_job_worker_locked"):
                record = runtime.enqueue_job(
                    kind="rescan_root",
                    queued_message="Rescan queued for Music.",
                    running_message="Rescan running for Music.",
                    canceled_message="Rescan canceled for Music.",
                    failed_message="Rescan failed for Music.",
                    context={"path": "/Volumes/Music", "root_position": 0},
                    runner=lambda _cancel_token: PlayerJobResult(
                        "Rescan completed for Music.",
                        {"path": "/Volumes/Music", "root_position": 0, "tracks_scanned": 1},
                    ),
                )
                queued = runtime.job_queue.popleft()

            runtime.run_queued_job(queued)
            finished = get_player_job(database, record.job_id)

            self.assertEqual(finished.status, "succeeded")
            self.assertEqual(finished.message, "Rescan completed for Music.")
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
                    kind="rescan_root",
                    queued_message="Rescan queued for Music.",
                    running_message="Rescan running for Music.",
                    canceled_message="Rescan canceled for Music.",
                    failed_message="Rescan failed for Music.",
                    context={"path": "/Volumes/Music", "root_position": 0},
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
            queued = create_player_job(
                database,
                kind="add_root",
                message="Add and scan queued.",
                context={},
            )
            running = create_player_job(
                database,
                kind="rescan_root",
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
    def test_opus_uses_ogg_audio_mime_type(self) -> None:
        self.assertEqual(audio_mime_type(Path("track.opus")), "audio/ogg")


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
            genre="Ambient",
        )
        playlist = PlaylistDetails(
            playlist_id=3,
            path="/music/streams.m3u8",
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
        self.assertEqual(payload["albumId"], "playlist:3")
        self.assertEqual(payload["artUrl"], playlist_cover_data_url(playlist_cover_svg("Streams")))

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

    def test_set_track_playlist_membership_updates_db_before_playlist_file_job(self) -> None:
        with TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            database = root / "kukicha.sqlite"
            playlist_path = root / "mix.m3u8"
            original_playlist_text = "#EXTM3U\n#PLAYLIST:Mix\n"
            playlist_path.write_text(original_playlist_text, encoding="utf-8")
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
                            path=str(playlist_path),
                            root_position=0,
                            name="Mix",
                        )
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-04-25T00:00:00+00:00",
                ),
                database,
            )

            initial_options = playlist_menu_options_by_track_id(database, [1])[1]
            added, add_job = set_track_playlist_membership_database(database, 1, 1, True)
            after_add_db_text = playlist_path.read_text(encoding="utf-8")
            with connect_database(database, create=False) as connection:
                rows_after_add_db = list(connection.execute("SELECT * FROM library_playlist_items"))
            self.assertIsNotNone(add_job)
            update_playlist_file_for_membership(add_job)
            after_add_job_text = playlist_path.read_text(encoding="utf-8")
            removed, remove_job = set_track_playlist_membership_database(database, 1, 1, False)
            after_remove_db_text = playlist_path.read_text(encoding="utf-8")
            self.assertIsNotNone(remove_job)
            update_playlist_file_for_membership(remove_job)
            after_remove_job_text = playlist_path.read_text(encoding="utf-8")
            with connect_database(database, create=False) as connection:
                rows = list(connection.execute("SELECT * FROM library_playlist_items"))

        self.assertFalse(initial_options[0].checked)
        self.assertTrue(added["checked"])
        self.assertEqual(after_add_db_text, original_playlist_text)
        self.assertEqual(len(rows_after_add_db), 1)
        self.assertIn(
            f"#EXTINF:283,Amon Tobin - Nova (Permutation)\n{track_path}\n",
            after_add_job_text,
        )
        self.assertFalse(removed["checked"])
        self.assertIn(str(track_path), after_remove_db_text)
        self.assertEqual(after_remove_job_text, original_playlist_text)
        self.assertEqual(rows, [])

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
                    path="/music/morning.m3u8",
                    checked=True,
                ),
                PlaylistMenuOption(
                    playlist_id=4,
                    name="Night",
                    path="/music/night.m3u8",
                    checked=False,
                ),
            ),
        )

        html = build_template_environment().get_template("player/_track_table.html").render(
            table_rows=[{"track": view, "group_label": ""}],
            is_queue=False,
            queue_state=PlayerQueueState(track_ids=[]),
        )

        self.assertIn('title="/music/morning.m3u8"', html)
        self.assertIn('data-track-id="7"', html)
        self.assertIn('data-playlist-id="3" checked', html)
        self.assertIn('data-playlist-id="4" ', html)
        self.assertIn("<span>Morning</span>", html)
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
        self.assertNotIn("Loading playlists...", html)

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
    def test_load_player_options_reads_toml_config(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            config_path = temp_path / "kukicha.toml"
            config_path.write_text(
                "\n".join(
                    (
                        "LogLevel = 'info'",
                        "DatabasePath = 'library.sqlite'",
                        "FFmpegPath = 'bin/ffmpeg'",
                        "Host = '0.0.0.0'",
                        "Port = 43210",
                        "ToastTimeoutMs = 12000",
                        "LinkedToastTimeoutMs = 30000",
                        "AlbumArtistSplitPatterns = ['&', '/']",
                    )
                ),
                encoding="utf-8",
            )

            options = load_player_options(config_path)

            self.assertEqual(options.config_path, config_path.resolve())
            self.assertEqual(options.database, (temp_path / "library.sqlite").resolve())
            self.assertEqual(options.ffmpeg_path, (temp_path / "bin" / "ffmpeg").resolve())
            self.assertEqual(options.host, "0.0.0.0")
            self.assertEqual(options.port, 43210)
            self.assertEqual(options.log_level, "INFO")
            self.assertEqual(options.toast_timeout_ms, 12000)
            self.assertEqual(options.linked_toast_timeout_ms, 30000)
            self.assertEqual(options.album_artist_split_patterns, ("&", "/"))

    def test_load_player_options_uses_default_paths_when_default_config_is_missing(self) -> None:
        with TemporaryDirectory() as tempdir:
            config_home = Path(tempdir)
            with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(config_home)}, clear=False):
                options = load_player_options()

            self.assertEqual(options.config_path, (config_home / "kukicha" / "kukicha.toml").resolve())
            self.assertEqual(options.database, (config_home / "kukicha" / "kukicha.sqlite").resolve())
            self.assertIsNone(options.ffmpeg_path)
            self.assertEqual(options.host, DEFAULT_PLAYER_HOST)
            self.assertEqual(options.port, DEFAULT_PLAYER_PORT)
            self.assertEqual(options.log_level, DEFAULT_PLAYER_LOG_LEVEL)
            self.assertEqual(options.toast_timeout_ms, DEFAULT_TOAST_TIMEOUT_MS)
            self.assertEqual(options.linked_toast_timeout_ms, DEFAULT_LINKED_TOAST_TIMEOUT_MS)
            self.assertEqual(
                options.album_artist_split_patterns,
                DEFAULT_ALBUM_ARTIST_SPLIT_PATTERNS,
            )

    def test_load_player_options_accepts_empty_album_artist_split_patterns(self) -> None:
        with TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "kukicha.toml"
            config_path.write_text(
                "AlbumArtistSplitPatterns = []\n",
                encoding="utf-8",
            )

            options = load_player_options(config_path)

        self.assertEqual(options.album_artist_split_patterns, ())

    def test_load_player_options_rejects_non_string_album_artist_split_patterns(self) -> None:
        with TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "kukicha.toml"
            config_path.write_text(
                "AlbumArtistSplitPatterns = ['&', 1]\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                PlayerConfigError,
                "AlbumArtistSplitPatterns must be an array of strings",
            ):
                load_player_options(config_path)

    def test_load_player_options_rejects_invalid_toast_timeouts(self) -> None:
        with TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "kukicha.toml"
            config_path.write_text(
                "ToastTimeoutMs = 0\nLinkedToastTimeoutMs = 'slow'\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(PlayerConfigError, "ToastTimeoutMs must be greater than 0"):
                load_player_options(config_path)

            config_path.write_text(
                "ToastTimeoutMs = 10000\nLinkedToastTimeoutMs = 'slow'\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(PlayerConfigError, "LinkedToastTimeoutMs must be an integer"):
                load_player_options(config_path)

    def test_load_player_options_rejects_unknown_config_keys(self) -> None:
        with TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "kukicha.toml"
            config_path.write_text("Bogus = 'value'\n", encoding="utf-8")

            with self.assertRaisesRegex(PlayerConfigError, "unsupported config key"):
                load_player_options(config_path)

    def test_player_config_help_text_shows_defaults_when_config_is_missing(self) -> None:
        with TemporaryDirectory() as tempdir:
            config_home = Path(tempdir)
            with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(config_home)}, clear=False):
                help_text = player_config_help_text()

            self.assertIn("status: missing (defaults in effect)", help_text)
            self.assertIn(f"path: {(config_home / 'kukicha' / 'kukicha.toml').resolve()}", help_text)
            self.assertIn(f"LogLevel: {DEFAULT_PLAYER_LOG_LEVEL} (default)", help_text)
            self.assertIn(f"DatabasePath: {(config_home / 'kukicha' / 'kukicha.sqlite').resolve()} (default)", help_text)
            self.assertIn(f"Host: {DEFAULT_PLAYER_HOST} (default)", help_text)
            self.assertIn(f"Port: {DEFAULT_PLAYER_PORT} (default)", help_text)
            self.assertIn(f"ToastTimeoutMs: {DEFAULT_TOAST_TIMEOUT_MS} (default)", help_text)
            self.assertIn(
                f"LinkedToastTimeoutMs: {DEFAULT_LINKED_TOAST_TIMEOUT_MS} (default)",
                help_text,
            )
            self.assertIn("FFmpegPath: <unset> (default)", help_text)
            self.assertIn(
                "AlbumArtistSplitPatterns: ['with', 'and', '&', ',', '/'] (default)",
                help_text,
            )
            self.assertIn(
                "Supported keys:\n  LogLevel\n  DatabasePath\n  FFmpegPath\n  Host\n  Port\n"
                "  ToastTimeoutMs\n  LinkedToastTimeoutMs\n  AlbumArtistSplitPatterns",
                help_text,
            )


class CliPlayerCommandTest(unittest.TestCase):
    def test_root_command_accepts_config_flag(self) -> None:
        parser = build_parser()

        args = parser.parse_args(["-c", "/tmp/kukicha.toml"])

        self.assertEqual(args.config, Path("/tmp/kukicha.toml"))

    def test_player_subcommand_is_not_available(self) -> None:
        parser = build_parser()

        with (
            patch("sys.stderr", new=io.StringIO()),
            self.assertRaises(SystemExit),
        ):
            parser.parse_args(["player", "-c", "/tmp/kukicha.toml"])

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
            config_path.write_text(
                "\n".join(
                    (
                        "LogLevel = 'info'",
                        "DatabasePath = 'custom.sqlite'",
                        "Host = '0.0.0.0'",
                        "Port = 43210",
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
            self.assertIn("LogLevel: INFO (configured)", help_text)
            self.assertIn(f"DatabasePath: {(temp_path / 'custom.sqlite').resolve()} (configured)", help_text)
            self.assertIn("Host: 0.0.0.0 (configured)", help_text)
            self.assertIn("Port: 43210 (configured)", help_text)
            self.assertIn("FFmpegPath: <unset> (default)", help_text)


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


class PlayerPageMenuTest(unittest.TestCase):
    def test_player_page_menu_items_include_all_pages_and_mark_current(self) -> None:
        items = player_page_menu_items("jobs")

        self.assertEqual(
            [(item.kind, item.title, item.url) for item in items],
            [
                ("heading", "LIBRARY", ""),
                ("link", "Albums", "/"),
                ("link", "Artists", "/artists"),
                ("divider", "", ""),
                ("heading", "SETTINGS", ""),
                ("link", "Roots", "/roots"),
                ("link", "Artists Split Rules", "/artist-split-rules"),
                ("link", "Cache", "/cache"),
                ("divider", "", ""),
                ("link", "Jobs", "/jobs"),
                ("link", "Help", "/help"),
            ],
        )
        self.assertEqual(
            [item.title for item in items if item.current],
            ["Jobs"],
        )

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
        self.assertLess(html.index("SETTINGS"), html.index("Roots"))
        self.assertLess(html.index("Roots"), html.index("Artists Split Rules"))
        self.assertLess(html.index("Artists Split Rules"), html.index("Cache"))

    def test_player_page_heading_rejects_unknown_page(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown player page"):
            player_page_heading("missing")


class PlayerArtistCloudLinksTest(unittest.TestCase):
    def test_artist_cloud_links_build_album_filter_urls_and_skip_blank_artists(self) -> None:
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
        self.assertEqual(links[0].url, "/?artist=Ahmad+Jamal")
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
            "/?genre[0][p]=Electronic&genre[0][c][]=Ambient"
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

        self.assertEqual(url, "/?genre[0][p]=Electronic")

    def test_parses_sort_param_and_defaults_to_recently_added(self) -> None:
        default_query = album_list_query_from_params(parse_qs(""))
        artist_query = album_list_query_from_params(parse_qs("sort=artist"))
        invalid_query = album_list_query_from_params(parse_qs("sort=unknown"))

        self.assertEqual(default_query.sort, ALBUM_LIST_SORT_RECENTLY_ADDED)
        self.assertEqual(artist_query.sort, ALBUM_LIST_SORT_ARTIST)
        self.assertEqual(invalid_query.sort, ALBUM_LIST_SORT_RECENTLY_ADDED)

    def test_album_index_url_includes_only_non_default_sort_param(self) -> None:
        self.assertEqual(
            album_index_url(AlbumListQuery(sort=ALBUM_LIST_SORT_RECENTLY_ADDED)),
            "/",
        )
        self.assertEqual(
            album_index_url(AlbumListQuery(sort=ALBUM_LIST_SORT_ARTIST)),
            "/?sort=artist",
        )


class PlayerAlbumDetailLinksTest(unittest.TestCase):
    def test_album_meta_query_replaces_content_filters_and_preserves_root_and_property_filters(self) -> None:
        query = AlbumListQuery(
            artists=("Current Artist",),
            album="Selected Ambient Works Volume II",
            root_positions=(0, 2),
            genre_filters=(GenreStyleFilter(genre="Ambient", styles=("IDM",)),),
            has_cover=True,
            is_compilation=False,
            is_work=True,
            page=4,
            per_page=80,
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
        self.assertEqual(linked.root_positions, (0, 2))
        self.assertTrue(linked.has_cover)
        self.assertFalse(linked.is_compilation)
        self.assertTrue(linked.is_work)
        self.assertEqual(linked.page, 1)
        self.assertEqual(linked.per_page, 80)
        self.assertEqual(linked.sort, ALBUM_LIST_SORT_ARTIST)
        self.assertIsNone(linked.album)
        self.assertIsNone(linked.search)

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
            has_cover=True,
            per_page=80,
            search="ignored",
        )
        filters = LibraryFilterOptions(
            genre_groups=(
                GenreFilterGroup(genre="Electronic", styles=("IDM", "Ambient")),
                GenreFilterGroup(genre="Jazz", styles=("Bebop",)),
                GenreFilterGroup(genre="Field Recording"),
            )
        )
        roots = (
            LibraryRootFilterOption(position=1, path="/music/a", label=".../a"),
            LibraryRootFilterOption(position=2, path="/music/b", label=".../b"),
        )

        artist_links = album_artist_links(album, query)
        root_links = album_root_links(album, roots)
        genre_links = album_genre_links(album, query, filters)
        style_links = album_style_links(album, query, filters)

        self.assertEqual(
            [(item.label, item.url) for item in artist_links],
            [("Aphex Twin", "/?artist=Aphex+Twin&root=1&has_cover=1&per_page=80")],
        )
        self.assertEqual(
            [(item.label, item.url) for item in root_links],
            [
                (".../b", "/?root=2"),
                (".../a", "/?root=1"),
            ],
        )
        self.assertEqual(
            [(item.label, item.url) for item in genre_links],
            [
                (
                    "Electronic",
                    "/?root=1&genre[0][p]=Electronic&has_cover=1&per_page=80",
                ),
                (
                    "Jazz",
                    "/?root=1&genre[0][p]=Jazz&has_cover=1&per_page=80",
                ),
                (
                    "Field Recording",
                    "/?root=1&genre[0][p]=Field+Recording&has_cover=1&per_page=80",
                ),
            ],
        )
        self.assertEqual(
            [(item.label, item.url) for item in style_links],
            [
                (
                    "IDM",
                    "/?root=1&genre[0][p]=Electronic&genre[0][c][]=IDM"
                    "&has_cover=1&per_page=80",
                ),
                (
                    "Bebop",
                    "/?root=1&genre[0][p]=Jazz&genre[0][c][]=Bebop"
                    "&has_cover=1&per_page=80",
                ),
            ],
        )


    def test_album_template_renders_individual_album_artist_links_with_commas(self) -> None:
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

        self.assertIn(
            (
                '<a class="album-detail-meta-link" href="/?artist=Brian+Eno" '
                'data-nav>Brian Eno</a>,'
            ),
            html,
        )

    def test_album_template_renders_year_with_album_artist_separator(self) -> None:
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

        self.assertIn('<ul class="meta-list album-artist-meta">', html)
        self.assertIn('<li class="album-year-meta">1978</li>', html)
        self.assertLess(
            html.index("album-artist-meta"),
            html.index('<li class="album-year-meta">1978</li>'),
        )
        self.assertLess(
            html.index('<li class="album-year-meta">1978</li>'),
            html.index("album-genre-meta"),
        )

    def test_album_templates_render_comma_separated_root_filter_links_in_title(self) -> None:
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
        roots = (
            LibraryRootFilterOption(position=0, path="/music/a", label=".../a"),
            LibraryRootFilterOption(position=2, path="/music/b", label=".../b"),
        )
        root_links = album_root_links(album, roots)
        album_template = build_template_environment().get_template("player/album.html")
        edit_template = build_template_environment().get_template("player/album_edit.html")

        album_html = album_template.render(
            album=album,
            album_back_url="/",
            album_edit_page_url="/albums/brian-eno-robert-fripp::no-pussyfooting/edit",
            album_root_links=root_links,
            album_artist_links=album_artist_links(album, AlbumListQuery()),
            album_genre_links=(),
            album_year_text="",
            album_style_links=(),
            track_sections=(),
        )
        edit_html = edit_template.render(
            album=album,
            album_back_url="/albums/brian-eno-robert-fripp::no-pussyfooting",
            album_root_links=root_links,
            album_artist_parts=("Brian Eno", "Robert Fripp"),
            album_genre_year_parts=(),
            album_style_parts=(),
            album_musicbrainz_action_url="/api/albums/brian-eno-robert-fripp::no-pussyfooting/musicbrainz",
            album_tag_edit_action_url="/api/albums/brian-eno-robert-fripp::no-pussyfooting/tags",
            album_tag_edit_album_artist="Brian Eno & Robert Fripp",
            album_tag_edit_genre="",
            album_musicbrainz_release_mbid="",
            album_musicbrainz_release_group_mbid="",
            tracks=(),
        )

        expected = (
            '<span class="album-title-root-links">(<a class="album-detail-meta-link" '
            'href="/?root=0" data-nav>.../a</a>, '
            '<a class="album-detail-meta-link" href="/?root=2" data-nav>.../b</a>)</span>'
        )
        self.assertIn(expected, album_html)
        self.assertIn(expected, edit_html)


class PlayerDirectoryPickerTest(unittest.TestCase):
    def test_choose_directory_path_returns_selected_folder_on_macos(self) -> None:
        with (
            patch("kukicha.player_platform.sys.platform", "darwin"),
            patch(
                "kukicha.player_platform.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=["osascript"],
                    returncode=0,
                    stdout="/Volumes/Music\n",
                    stderr="",
                ),
            ),
        ):
            self.assertEqual(choose_directory_path(), "/Volumes/Music")

    def test_choose_directory_path_returns_none_when_picker_is_canceled(self) -> None:
        with (
            patch("kukicha.player_platform.sys.platform", "darwin"),
            patch(
                "kukicha.player_platform.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=["osascript"],
                    returncode=1,
                    stdout="",
                    stderr="execution error: User canceled. (-128)",
                ),
            ),
        ):
            self.assertIsNone(choose_directory_path())


class PlayerWebAdapterTest(unittest.TestCase):
    def make_options(self, temp_path: Path) -> PlayerServerOptions:
        return PlayerServerOptions(
            config_path=temp_path / "kukicha.toml",
            database=temp_path / "kukicha.sqlite",
            ffmpeg_path=None,
        )

    def make_runtime(self, database: Path) -> Mock:
        runtime = Mock()
        runtime.database = database
        runtime.queue_state_copy.return_value = PlayerQueueState(track_ids=[])
        return runtime

    def test_healthz_returns_no_content(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            runtime = self.make_runtime(temp_path / "kukicha.sqlite")
            with patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime):
                app = create_player_app(self.make_options(temp_path))

            response = app.test_client().get("/healthz")

            self.assertEqual(response.status_code, 204)

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
                kind="rescan_root",
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

    def test_page_rendering_can_return_full_document_or_fragment(self) -> None:
        context = {
            "app_title": "kukicha player",
            "queue_state": {},
            "queue_url": "/queue",
            "page_name": "library",
            "page_key": "library",
            "page_heading": "Albums",
            "page_menu_items": (),
            "count_text": "",
            "view_template": "player/simple_page.html",
            "toast_timeout_ms": 12000,
            "linked_toast_timeout_ms": 30000,
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
                full_response = client.get("/")
                fragment_response = client.get("/", headers={"X-Kukicha-Fragment": "1"})

            self.assertEqual(full_response.status_code, 200)
            self.assertIn(b"<!doctype html>", full_response.data)
            self.assertIn(b"<h1>Albums</h1>", full_response.data)
            self.assertIn(b'data-toast-timeout-ms="12000"', full_response.data)
            self.assertIn(b'data-linked-toast-timeout-ms="30000"', full_response.data)
            self.assertEqual(fragment_response.status_code, 200)
            self.assertNotIn(b"<!doctype html>", fragment_response.data)
            self.assertIn(b"<h1>Albums</h1>", fragment_response.data)

    def test_artists_page_renders_tag_cloud_without_album_filters(self) -> None:
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
            self.assertIn(b'href="/?artist=Ahmad+Jamal"', full_response.data)
            self.assertNotIn(b"data-filter-form", full_response.data)
            self.assertNotIn(b'type="search"', full_response.data)
            self.assertEqual(fragment_response.status_code, 200)
            self.assertNotIn(b"<!doctype html>", fragment_response.data)
            self.assertIn(b"<h1>Artists</h1>", fragment_response.data)

    def test_help_page_renders_config_values_and_sources(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            config_path = temp_path / "kukicha.toml"
            config_path.write_text(
                "\n".join(
                    (
                        "LogLevel = 'info'",
                        "Host = '0.0.0.0'",
                        "Port = 43210",
                    )
                ),
                encoding="utf-8",
            )
            options = load_player_options(config_path)
            app = create_player_app(options)

            response = app.test_client().get("/help")

            self.assertEqual(response.status_code, 200)
            self.assertIn(b"<h1>Help</h1>", response.data)
            self.assertIn(b"<h2>Config</h2>", response.data)
            self.assertIn(f"<code>{config_path.resolve()}</code>".encode(), response.data)
            self.assertIn(b"<code>LogLevel</code>", response.data)
            self.assertIn(b"<code>INFO</code>", response.data)
            self.assertIn(b"<code>Host</code>", response.data)
            self.assertIn(b"<code>0.0.0.0</code>", response.data)
            self.assertIn(b"<code>Port</code>", response.data)
            self.assertIn(b"<code>43210</code>", response.data)
            self.assertIn(b'<span class="config-source configured">configured</span>', response.data)
            self.assertIn(b"<code>FFmpegPath</code>", response.data)
            self.assertIn(b"<code>&lt;unset&gt;</code>", response.data)
            self.assertIn(b'<span class="config-source default">default</span>', response.data)

    def test_settings_pages_render_roots_artist_split_rules_and_cache(self) -> None:
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
                connection.commit()
            finally:
                connection.close()

            runtime = self.make_runtime(database)
            with patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime):
                app = create_player_app(self.make_options(temp_path))
                client = app.test_client()
                roots_response = client.get("/roots")
                split_rules_response = client.get("/artist-split-rules")
                cache_response = client.get("/cache")

            self.assertEqual(roots_response.status_code, 200)
            self.assertIn(b"<h1>Roots</h1>", roots_response.data)
            self.assertIn(b"<h2>Add Root</h2>", roots_response.data)
            self.assertIn(b"<h2>Current Roots</h2>", roots_response.data)
            self.assertIn(b"/music/a", roots_response.data)
            self.assertIn(b"Tracks scanned</dt>\n                <dd>3</dd>", roots_response.data)
            self.assertIn(b"Albums scanned</dt>\n                <dd>2</dd>", roots_response.data)
            self.assertIn(b"Playlists scanned</dt>\n                <dd>1</dd>", roots_response.data)
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

            self.assertEqual(cache_response.status_code, 200)
            self.assertIn(b"<h1>Cache</h1>", cache_response.data)
            self.assertIn(b"<h2>Cache</h2>", cache_response.data)
            self.assertIn(b"MusicBrainz", cache_response.data)
            self.assertIn(b"iTunes Artwork", cache_response.data)
            self.assertNotIn(b"<h2>Add Root</h2>", cache_response.data)
            self.assertNotIn(b"data-edit-album-artist-mapping", cache_response.data)

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
                        "Rescan affected roots to update library filters, artists, and stats."
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

    def test_static_file_and_favicon_are_served(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            runtime = self.make_runtime(temp_path / "kukicha.sqlite")
            with patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime):
                app = create_player_app(self.make_options(temp_path))
                client = app.test_client()
                static_response = client.get("/static/player.css")
                favicon_response = client.get("/favicon.ico")

            self.assertEqual(static_response.status_code, 200)
            self.assertEqual(static_response.content_type, "text/css; charset=utf-8")
            self.assertEqual(static_response.headers["Cache-Control"], "private, max-age=60")
            self.assertEqual(favicon_response.status_code, 200)
            self.assertEqual(favicon_response.content_type, "image/svg+xml")

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

    def test_job_events_stream_retries_and_unsubscribes_on_close(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            runtime = self.make_runtime(temp_path / "kukicha.sqlite")
            with patch("kukicha.player_web_adapter.PlayerRuntime", return_value=runtime):
                app = create_player_app(self.make_options(temp_path))

            response = app.test_client().get("/api/jobs/events", buffered=False)
            first_chunk = next(iter(response.response))
            response.close()

            self.assertEqual(response.status_code, 200)
            self.assertEqual(first_chunk, b"retry: 1000\n\n")
            runtime.subscribe_jobs.assert_called_once()
            runtime.unsubscribe_jobs.assert_called_once()


class PlayerRootMutationTest(unittest.TestCase):
    def test_create_library_root_inserts_next_root_and_returns_option(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            first_root = temp_path / "music-a"
            second_root = temp_path / "music-b"
            first_root.mkdir()
            second_root.mkdir()

            created_first = create_library_root(database, str(first_root))
            created_second = create_library_root(database, str(second_root))
            connection = connect_database(database)
            try:
                roots = list(
                    connection.execute(
                        "SELECT position, root_path FROM library_roots ORDER BY position"
                    )
                )
            finally:
                connection.close()

            self.assertEqual(created_first.position, 0)
            self.assertEqual(created_first.path, str(first_root.resolve()))
            self.assertEqual(created_second.position, 1)
            self.assertEqual(created_second.path, str(second_root.resolve()))
            self.assertEqual([int(row["position"]) for row in roots], [0, 1])
            self.assertEqual(
                [str(row["root_path"]) for row in roots],
                [str(first_root.resolve()), str(second_root.resolve())],
            )

    def test_create_library_root_rejects_duplicates(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            root = temp_path / "music"
            root.mkdir()

            create_library_root(database, str(root))

            with self.assertRaisesRegex(ValueError, "root already exists"):
                create_library_root(database, str(root))

    def test_scan_library_with_new_root_scans_atomically_and_preserves_root_positions(self) -> None:
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
                insert_library_album(
                    connection,
                    "artist-a::album-a",
                    "Artist A",
                    "Album A",
                    2000,
                    1,
                )
                insert_library_album(
                    connection,
                    "artist-b::album-b",
                    "Artist B",
                    "Album B",
                    2001,
                    1,
                )
                preserved_track_a = int(
                    connection.execute(
                        """
                        INSERT INTO library_tracks (
                            album_id, root_position, path, album_artist, artist, album, title, date
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            "artist-a::album-a",
                            0,
                            str(root_a / "Artist A" / "Album A" / "01.flac"),
                            "Artist A",
                            "Artist A",
                            "Album A",
                            "Track A",
                            "2000",
                        ),
                    ).lastrowid
                )
                preserved_track_b = int(
                    connection.execute(
                        """
                        INSERT INTO library_tracks (
                            album_id, root_position, path, album_artist, artist, album, title, date
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            "artist-b::album-b",
                            2,
                            str(root_b / "Artist B" / "Album B" / "01.flac"),
                            "Artist B",
                            "Artist B",
                            "Album B",
                            "Track B",
                            "2001",
                        ),
                    ).lastrowid
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
                        date="2000",
                    ),
                    TrackRecord(
                        path=str(root_b / "Artist B" / "Album B" / "01.flac"),
                        root_position=1,
                        file_type="flac",
                        artist="Artist B",
                        album_artist="Artist B",
                        album="Album B",
                        title="Track B",
                        date="2001",
                    ),
                    TrackRecord(
                        path=str(root_c / "Artist C" / "Album C" / "01.flac"),
                        root_position=2,
                        file_type="flac",
                        artist="Artist C",
                        album_artist="Artist C",
                        album="Album C",
                        title="Track C",
                        date="2002",
                    ),
                ],
                playlists=[
                    PlaylistRecord(
                        path=str(root_c / "mix.m3u"),
                        name="Root C Mix",
                        root_position=2,
                    ),
                ],
                supported_extensions=[".flac"],
                generated_at="2026-04-21T12:00:00+00:00",
            )

            with (
                patch("kukicha.use_case.commands.roots.build_library", return_value=scanned_library),
                patch("kukicha.use_case.commands.roots.resolve_library_genres", return_value=None),
                patch("kukicha.use_case.commands.roots.resolve_library_cover_art", return_value=None),
            ):
                result = scan_library_with_new_root(database, str(root_c))

            self.assertEqual(result.root.position, 3)
            self.assertEqual(result.root.path, str(root_c))
            self.assertEqual(result.tracks_scanned, 3)
            self.assertEqual(result.albums_scanned, 3)
            self.assertEqual(result.playlists_scanned, 1)
            self.assertEqual(result.files_missing_required_tags, 0)

            connection = connect_database(database, create=False)
            try:
                roots = list(connection.execute("SELECT position, root_path FROM library_roots ORDER BY position"))
                self.assertEqual(
                    [(int(row["position"]), str(row["root_path"])) for row in roots],
                    [(0, str(root_a)), (2, str(root_b)), (3, str(root_c))],
                )
                tracks = list(
                    connection.execute(
                        "SELECT track_id, root_position, path FROM library_tracks ORDER BY root_position, path"
                    )
                )
                self.assertEqual(
                    [
                        (int(row["track_id"]), int(row["root_position"]), str(row["path"]))
                        for row in tracks
                    ],
                    [
                        (
                            preserved_track_a,
                            0,
                            str(root_a / "Artist A" / "Album A" / "01.flac"),
                        ),
                        (
                            preserved_track_b,
                            2,
                            str(root_b / "Artist B" / "Album B" / "01.flac"),
                        ),
                        (
                            max(preserved_track_a, preserved_track_b) + 1,
                            3,
                            str(root_c / "Artist C" / "Album C" / "01.flac"),
                        ),
                    ],
                )
                stats = list(
                    connection.execute(
                        """
                        SELECT
                            root_position,
                            tracks_scanned,
                            albums_scanned,
                            playlists_scanned
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
                            int(row["playlists_scanned"]),
                        )
                        for row in stats
                    ],
                    [
                        (0, 1, 1, 0),
                        (2, 1, 1, 0),
                        (3, 1, 1, 1),
                    ],
                )
                total_stats = connection.execute(
                    """
                    SELECT tracks_scanned, albums_scanned, playlists_scanned
                    FROM library_stats
                    WHERE stats_id = 1
                    """
                ).fetchone()
                self.assertIsNotNone(total_stats)
                self.assertEqual(
                    (
                        int(total_stats["tracks_scanned"]),
                        int(total_stats["albums_scanned"]),
                        int(total_stats["playlists_scanned"]),
                    ),
                    (3, 3, 1),
                )
            finally:
                connection.close()

    def test_scan_library_with_new_root_logs_scanner_progress(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            root = (temp_path / "music").resolve()
            root.mkdir()

            scanned_library = MusicLibrary(
                roots=[str(root)],
                tracks=[],
                supported_extensions=[".flac"],
                generated_at="2026-04-21T12:00:00+00:00",
            )

            def fake_build_library(*_args: object, **kwargs: object) -> MusicLibrary:
                self.assertEqual(kwargs["progress_every"], 500)
                progress = kwargs.get("progress")
                self.assertIsNotNone(progress)
                progress("scanned 500 music files")
                return scanned_library

            with (
                patch("kukicha.use_case.commands.roots.build_library", side_effect=fake_build_library),
                patch("kukicha.use_case.commands.roots.resolve_library_genres", return_value=None),
                patch("kukicha.use_case.commands.roots.resolve_library_cover_art", return_value=None),
                patch("kukicha.use_case.commands.roots.LOGGER.info") as logger_info,
            ):
                scan_library_with_new_root(database, str(root))

            logged_messages = [
                str(call.args[1])
                for call in logger_info.call_args_list
                if len(call.args) >= 2 and call.args[0] == "%s"
            ]
            self.assertIn(
                library_scan_progress_text("add and scan", "scanned 500 music files"),
                logged_messages,
            )

    def test_scan_library_with_new_root_rolls_back_all_changes_on_failure(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            existing_root = (temp_path / "music-a").resolve()
            new_root = (temp_path / "music-b").resolve()
            existing_root.mkdir()
            new_root.mkdir()

            connection = connect_database(database)
            try:
                connection.execute(
                    "INSERT INTO library_roots (position, root_path) VALUES (?, ?)",
                    (0, str(existing_root)),
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
                        str(existing_root / "Artist" / "Album" / "01.flac"),
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

            scanned_library = MusicLibrary(
                roots=[str(existing_root), str(new_root)],
                tracks=[
                    TrackRecord(
                        path=str(existing_root / "Artist" / "Album" / "02.flac"),
                        root_position=0,
                        file_type="flac",
                        artist="Artist",
                        album_artist="Artist",
                        album="Album",
                        title="Replacement",
                        date="2001",
                    ),
                    TrackRecord(
                        path=str(new_root / "Artist" / "Album" / "01.flac"),
                        root_position=1,
                        file_type="flac",
                        artist="Artist",
                        album_artist="Artist",
                        album="Album",
                        title="New Root Track",
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
                patch("kukicha.use_case.commands.roots.build_library", return_value=scanned_library),
                patch("kukicha.use_case.commands.roots.resolve_library_genres", side_effect=failing_resolve),
            ):
                with self.assertRaisesRegex(RuntimeError, "boom"):
                    scan_library_with_new_root(database, str(new_root))

            connection = connect_database(database, create=False)
            try:
                roots = list(connection.execute("SELECT position, root_path FROM library_roots ORDER BY position"))
                self.assertEqual(
                    [(int(row["position"]), str(row["root_path"])) for row in roots],
                    [(0, str(existing_root))],
                )
                tracks = list(connection.execute("SELECT root_position, path FROM library_tracks"))
                self.assertEqual(len(tracks), 1)
                self.assertEqual(int(tracks[0]["root_position"]), 0)
                self.assertEqual(str(tracks[0]["path"]), str(existing_root / "Artist" / "Album" / "01.flac"))
                self.assertEqual(
                    int(connection.execute("SELECT COUNT(*) AS count FROM musicbrainz_entity_cache").fetchone()["count"]),
                    0,
                )
                stats = list(
                    connection.execute(
                        """
                        SELECT root_position, tracks_scanned, albums_scanned, playlists_scanned
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
                            int(row["playlists_scanned"]),
                        )
                        for row in stats
                    ],
                    [(0, 1, 1, 0)],
                )
                total_stats = connection.execute(
                    """
                    SELECT tracks_scanned, albums_scanned, playlists_scanned
                    FROM library_stats
                    WHERE stats_id = 1
                    """
                ).fetchone()
                self.assertIsNotNone(total_stats)
                self.assertEqual(
                    (
                        int(total_stats["tracks_scanned"]),
                        int(total_stats["albums_scanned"]),
                        int(total_stats["playlists_scanned"]),
                    ),
                    (1, 1, 0),
                )
            finally:
                connection.close()

    def test_delete_library_root_removes_only_that_root_data(self) -> None:
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
                    (1, "/music/b"),
                )
                insert_library_album(
                    connection,
                    "artist::shared",
                    "Artist",
                    "Shared",
                    2000,
                    2,
                )
                insert_library_album(
                    connection,
                    "artist::only-a",
                    "Artist",
                    "Only A",
                    1999,
                    1,
                )
                connection.execute(
                    """
                    INSERT INTO album_musicbrainz_links (
                        album_id, release_mbid, release_group_mbid
                    ) VALUES (?, ?, ?)
                    """,
                    ("artist::shared", "release-1", "group-1"),
                )
                shared_a = int(
                    connection.execute(
                        """
                        INSERT INTO library_tracks (
                            album_id, root_position, path, album_artist, artist, album, title, date
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            "artist::shared",
                            0,
                            "/music/a/Artist/Shared/01.flac",
                            "Artist",
                            "Artist",
                            "Shared",
                            "One",
                            "2000",
                        ),
                    ).lastrowid
                )
                shared_b = int(
                    connection.execute(
                        """
                        INSERT INTO library_tracks (
                            album_id, root_position, path, album_artist, artist, album, title, date
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            "artist::shared",
                            1,
                            "/music/b/Artist/Shared/01.flac",
                            "Artist",
                            "Artist",
                            "Shared",
                            "Two",
                            "2001",
                        ),
                    ).lastrowid
                )
                only_a = int(
                    connection.execute(
                        """
                        INSERT INTO library_tracks (
                            album_id, root_position, path, album_artist, artist, album, title, date
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            "artist::only-a",
                            0,
                            "/music/a/Artist/Only A/01.flac",
                            "Artist",
                            "Artist",
                            "Only A",
                            "Solo",
                            "1999",
                        ),
                    ).lastrowid
                )
                connection.execute(
                    "INSERT INTO library_track_artwork (track_id, height_px, mime_type, data) VALUES (?, ?, ?, ?)",
                    (shared_a, 32, "image/png", b"art-a"),
                )
                connection.execute(
                    "INSERT INTO library_track_artwork (track_id, height_px, mime_type, data) VALUES (?, ?, ?, ?)",
                    (shared_b, 32, "image/png", b"art-b"),
                )
                connection.execute(
                    "INSERT INTO library_track_genres (track_id, position, genre) VALUES (?, ?, ?)",
                    (only_a, 0, "Electronic"),
                )
                connection.execute(
                    """
                    INSERT INTO musicbrainz_entity_cache (
                        entity_type, mbid, fetched_at, endpoint_url, response_json
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    ("release", "release-1", "2024-01-01", "https://example.test/release-1", "{}"),
                )
                connection.commit()
            finally:
                connection.close()

            deleted = delete_library_root(database, 0)

            self.assertEqual(deleted.position, 0)
            self.assertEqual(deleted.path, "/music/a")

            connection = connect_database(database)
            try:
                remaining_roots = list(
                    connection.execute("SELECT position, root_path FROM library_roots ORDER BY position")
                )
                self.assertEqual(len(remaining_roots), 1)
                self.assertEqual(int(remaining_roots[0]["position"]), 1)
                self.assertEqual(str(remaining_roots[0]["root_path"]), "/music/b")
                remaining_tracks = list(
                    connection.execute("SELECT album_id, root_position, path FROM library_tracks ORDER BY track_id")
                )
                self.assertEqual(len(remaining_tracks), 1)
                self.assertEqual(str(remaining_tracks[0]["album_id"]), "artist::shared")
                self.assertEqual(int(remaining_tracks[0]["root_position"]), 1)
                self.assertEqual(str(remaining_tracks[0]["path"]), "/music/b/Artist/Shared/01.flac")

                shared_album = connection.execute(
                    "SELECT album, year, track_count FROM library_albums WHERE album_id = ?",
                    ("artist::shared",),
                ).fetchone()
                self.assertIsNotNone(shared_album)
                self.assertEqual(int(shared_album["year"]), 2001)
                self.assertEqual(int(shared_album["track_count"]), 1)
                self.assertIsNone(
                    connection.execute(
                        "SELECT 1 FROM library_albums WHERE album_id = ?",
                        ("artist::only-a",),
                    ).fetchone()
                )

                self.assertEqual(
                    int(connection.execute("SELECT COUNT(*) AS count FROM library_track_artwork").fetchone()["count"]),
                    1,
                )
                self.assertEqual(
                    int(connection.execute("SELECT COUNT(*) AS count FROM library_album_search").fetchone()["count"]),
                    1,
                )
                self.assertEqual(
                    int(connection.execute("SELECT COUNT(*) AS count FROM musicbrainz_entity_cache").fetchone()["count"]),
                    1,
                )
                self.assertGreater(
                    int(connection.execute("SELECT COUNT(*) AS count FROM taxonomy_genres").fetchone()["count"]),
                    0,
                )
            finally:
                connection.close()

    def test_rescan_library_root_replaces_only_that_root_and_preserves_other_artwork(self) -> None:
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
                    "artist::shared",
                    "Artist",
                    "Shared",
                    2001,
                    2,
                )
                insert_library_album(
                    connection,
                    "artist::only-a",
                    "Artist",
                    "Only A",
                    2000,
                    1,
                )
                insert_library_album(
                    connection,
                    "artist::only-b",
                    "Artist",
                    "Only B",
                    2002,
                    1,
                )
                shared_a = int(
                    connection.execute(
                        """
                        INSERT INTO library_tracks (
                            album_id, root_position, path, album_artist, artist, album, title, date
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            "artist::shared",
                            0,
                            "/music/a/Artist/Shared/01.flac",
                            "Artist",
                            "Artist",
                            "Shared",
                            "One",
                            "2001",
                        ),
                    ).lastrowid
                )
                only_a = int(
                    connection.execute(
                        """
                        INSERT INTO library_tracks (
                            album_id, root_position, path, album_artist, artist, album, title, date
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            "artist::only-a",
                            0,
                            "/music/a/Artist/Only A/01.flac",
                            "Artist",
                            "Artist",
                            "Only A",
                            "Solo",
                            "2000",
                        ),
                    ).lastrowid
                )
                shared_b = int(
                    connection.execute(
                        """
                        INSERT INTO library_tracks (
                            album_id, root_position, path, album_artist, artist, album, title, date
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            "artist::shared",
                            2,
                            "/music/b/Artist/Shared/01.flac",
                            "Artist",
                            "Artist",
                            "Shared",
                            "Two",
                            "2002",
                        ),
                    ).lastrowid
                )
                only_b = int(
                    connection.execute(
                        """
                        INSERT INTO library_tracks (
                            album_id, root_position, path, album_artist, artist, album, title, date
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            "artist::only-b",
                            2,
                            "/music/b/Artist/Only B/01.flac",
                            "Artist",
                            "Artist",
                            "Only B",
                            "Elsewhere",
                            "2002",
                        ),
                    ).lastrowid
                )
                connection.executemany(
                    "INSERT INTO library_track_artwork (track_id, height_px, mime_type, data) VALUES (?, ?, ?, ?)",
                    [
                        (shared_a, 32, "image/png", b"old-a"),
                        (only_a, 32, "image/png", b"old-only-a"),
                        (shared_b, 32, "image/png", b"keep-shared-b"),
                        (only_b, 250, "image/png", b"keep-only-b"),
                    ],
                )
                connection.execute(
                    """
                    INSERT INTO library_playlists (
                        root_position, path, name, cover_svg
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (2, "/music/b/root-b.m3u", "Root B Mix", ""),
                )
                connection.commit()
            finally:
                connection.close()

            rescanned_library = MusicLibrary(
                roots=["/music/a"],
                tracks=[
                    TrackRecord(
                        path="/music/a/Artist/Shared/02.flac",
                        root_position=0,
                        file_type="flac",
                        artist="Artist",
                        album_artist="Artist",
                        album="Shared",
                        title="Replacement",
                        date="2003",
                        genres=["Electronic"],
                        artwork=TrackArtwork(mime_type="image/png", data=b"new-shared"),
                    ),
                    TrackRecord(
                        path="/music/a/Artist/New Album/01.flac",
                        root_position=0,
                        file_type="flac",
                        artist="Artist",
                        album_artist="Artist",
                        album="New Album",
                        title="Fresh",
                        date="2004",
                        genres=["Electronic"],
                    ),
                ],
                supported_extensions=[".flac"],
                generated_at="2026-04-21T12:00:00+00:00",
            )

            with (
                patch("kukicha.use_case.commands.roots.build_library", return_value=rescanned_library),
                patch(
                    "kukicha.use_case.commands.roots.parse_playlists",
                    return_value=[
                        PlaylistRecord(
                            path="/music/a/root-a.m3u",
                            name="Root A Mix",
                            root_position=0,
                        )
                    ],
                ),
                patch("kukicha.use_case.commands.roots.resolve_library_genres", return_value=None),
                patch("kukicha.use_case.commands.roots.resolve_library_cover_art", return_value=None),
            ):
                result = rescan_library_root(database, 0)

            self.assertEqual(result.root.position, 0)
            self.assertEqual(result.tracks_scanned, 2)
            self.assertEqual(result.albums_scanned, 2)
            self.assertEqual(result.playlists_scanned, 1)
            self.assertEqual(result.files_missing_required_tags, 0)

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
                        (0, "/music/a/Artist/New Album/01.flac"),
                        (0, "/music/a/Artist/Shared/02.flac"),
                        (2, "/music/b/Artist/Only B/01.flac"),
                        (2, "/music/b/Artist/Shared/01.flac"),
                    ],
                )
                art_paths = {
                    str(row["path"])
                    for row in connection.execute(
                        """
                        SELECT library_tracks.path
                        FROM library_track_artwork
                        JOIN library_tracks
                            ON library_tracks.track_id = library_track_artwork.track_id
                        """
                    )
                }
                track_ids_by_path = {
                    str(row["path"]): int(row["track_id"])
                    for row in connection.execute(
                        "SELECT track_id, path FROM library_tracks"
                    )
                }
                self.assertEqual(track_ids_by_path["/music/b/Artist/Shared/01.flac"], shared_b)
                self.assertEqual(track_ids_by_path["/music/b/Artist/Only B/01.flac"], only_b)
                self.assertIn("/music/a/Artist/Shared/02.flac", art_paths)
                self.assertIn("/music/b/Artist/Shared/01.flac", art_paths)
                self.assertIn("/music/b/Artist/Only B/01.flac", art_paths)
                self.assertNotIn("/music/a/Artist/Only A/01.flac", art_paths)

                shared_album = connection.execute(
                    "SELECT track_count FROM library_albums WHERE album_id = ?",
                    ("artist::shared",),
                ).fetchone()
                self.assertIsNotNone(shared_album)
                self.assertEqual(int(shared_album["track_count"]), 2)
                self.assertIsNotNone(
                    connection.execute(
                        "SELECT 1 FROM library_albums WHERE album = ?",
                        ("New Album",),
                    ).fetchone()
                )
                self.assertIsNone(
                    connection.execute(
                        "SELECT 1 FROM library_albums WHERE album_id = ?",
                        ("artist::only-a",),
                    ).fetchone()
                )
                stats = list(
                    connection.execute(
                        """
                        SELECT root_position, tracks_scanned, albums_scanned, playlists_scanned
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
                            int(row["playlists_scanned"]),
                        )
                        for row in stats
                    ],
                    [
                        (0, 2, 2, 1),
                        (2, 2, 2, 1),
                    ],
                )
                total_stats = connection.execute(
                    """
                    SELECT tracks_scanned, albums_scanned, playlists_scanned
                    FROM library_stats
                    WHERE stats_id = 1
                    """
                ).fetchone()
                self.assertIsNotNone(total_stats)
                self.assertEqual(
                    (
                        int(total_stats["tracks_scanned"]),
                        int(total_stats["albums_scanned"]),
                        int(total_stats["playlists_scanned"]),
                    ),
                    (4, 4, 2),
                )
            finally:
                connection.close()

    def test_rescan_library_root_rolls_back_all_changes_on_failure(self) -> None:
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
                patch("kukicha.use_case.commands.roots.build_library", return_value=rescanned_library),
                patch("kukicha.use_case.commands.roots.resolve_library_genres", side_effect=failing_resolve),
            ):
                with self.assertRaisesRegex(RuntimeError, "boom"):
                    rescan_library_root(database, 0)

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
                        SELECT root_position, tracks_scanned, albums_scanned, playlists_scanned
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
                            int(row["playlists_scanned"]),
                        )
                        for row in stats
                    ],
                    [(0, 1, 1, 0)],
                )
                total_stats = connection.execute(
                    """
                    SELECT tracks_scanned, albums_scanned, playlists_scanned
                    FROM library_stats
                    WHERE stats_id = 1
                    """
                ).fetchone()
                self.assertIsNotNone(total_stats)
                self.assertEqual(
                    (
                        int(total_stats["tracks_scanned"]),
                        int(total_stats["albums_scanned"]),
                        int(total_stats["playlists_scanned"]),
                    ),
                    (1, 1, 0),
                )
            finally:
                connection.close()

    def test_delete_library_root_rolls_back_on_failure(self) -> None:
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

            with patch("kukicha.use_case.commands.roots.reconcile_deleted_root_albums", side_effect=RuntimeError("boom")):
                with self.assertRaisesRegex(RuntimeError, "boom"):
                    delete_library_root(database, 0)

            connection = connect_database(database)
            try:
                self.assertIsNotNone(
                    connection.execute(
                        "SELECT 1 FROM library_roots WHERE position = ?",
                        (0,),
                    ).fetchone()
                )
                self.assertEqual(
                    int(connection.execute("SELECT COUNT(*) AS count FROM library_tracks").fetchone()["count"]),
                    1,
                )
                self.assertIsNotNone(
                    connection.execute(
                        "SELECT 1 FROM library_albums WHERE album_id = ?",
                        ("artist::album",),
                    ).fetchone()
                )
            finally:
                connection.close()


class PlayerJobLogTest(unittest.TestCase):
    def test_library_job_summary_text_formats_info_log_message(self) -> None:
        self.assertEqual(
            library_job_summary_text(
                "add and scan",
                "/music/a",
                tracks_scanned=12,
                albums_scanned=3,
                playlists_scanned=2,
                files_missing_required_tags=1,
                duration_seconds=4.125,
            ),
            "add and scan completed for /music/a (tracks=12, albums=3, playlists=2, missing_required_tags=1, duration=4.12s)",
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
                playlists_scanned=2,
                files_missing_required_tags=1,
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
                "tracks scanned: 12",
                "albums scanned: 3",
                "playlists scanned: 2",
                "files missing required tags: 1",
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

    def test_scan_job_payload_formats_playlist_count(self) -> None:
        payload = job_payload(
            PlayerJobRecord(
                job_id=1,
                created_at="2026-04-21T10:00:00Z",
                updated_at="2026-04-21T10:00:00Z",
                started_at=None,
                finished_at=None,
                cancel_requested_at=None,
                kind="rescan_root",
                status="succeeded",
                message="Rescan completed for /music/a.",
                reason="",
                context={
                    "path": "/music/a",
                    "root_position": 0,
                    "tracks_scanned": 12,
                    "albums_scanned": 3,
                    "playlists_scanned": 2,
                    "files_missing_required_tags": 1,
                    "duration_seconds": 4.125,
                },
            )
        )

        self.assertEqual(
            payload["context_items"],
            [
                {"label": "Root", "value": ".../a"},
                {"label": "Tracks", "value": "12"},
                {"label": "Albums", "value": "3"},
                {"label": "Playlists", "value": "2"},
                {"label": "Missing Tags", "value": "1"},
                {"label": "Duration", "value": "4.12 seconds"},
            ],
        )


class PlayerAlbumTagEditTest(unittest.TestCase):
    def seed_album(self, database: Path, paths: tuple[Path, Path]) -> None:
        connection = connect_database(database)
        try:
            connection.execute(
                "INSERT INTO library_roots (position, root_path) VALUES (?, ?)",
                (0, str(paths[0].parent.parent)),
            )
            insert_library_album(
                connection,
                "old-artist::album",
                "Old Artist",
                "Album",
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
                    "old-artist::album",
                    0,
                    str(paths[0]),
                    "mp3",
                    "Artist One",
                    "Old Artist",
                    "Album",
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
                    "old-artist::album",
                    0,
                    str(paths[1]),
                    "mp3",
                    "Artist Two",
                    "Old Artist",
                    "Album",
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

    def test_edit_library_album_musicbrainz_rescans_tracks_and_resolves_metadata(self) -> None:
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
                    "musicbrainz_release_mbid": "11111111-1111-1111-1111-111111111111",
                    "musicbrainz_release_group_mbid": "22222222-2222-2222-2222-222222222222",
                },
            )

            def fake_scan_track(path: Path) -> TrackRecord:
                artist = "Artist One" if path.name == "01.mp3" else "Artist Two"
                return TrackRecord(
                    path=str(path),
                    file_type="mp3",
                    artist=artist,
                    album_artist="Old Artist",
                    album="Album",
                    title="Track One" if path.name == "01.mp3" else "Track Two",
                    track_number="1" if path.name == "01.mp3" else "2",
                    date="1980",
                    duration_seconds=111.0,
                    bitrate=192000,
                )

            def fake_resolve_library_genres(
                library: MusicLibrary,
                _source: Path,
                *,
                connection: object | None = None,
                album_artist_split_patterns: object = (),
            ) -> GenreResolutionStats:
                self.assertIsNotNone(connection)
                mbid_row = connection.execute(
                    """
                    SELECT release_mbid, release_group_mbid
                    FROM album_musicbrainz_links
                    WHERE album_id = ?
                    """,
                    ("old-artist::album",),
                ).fetchone()
                self.assertIsNotNone(mbid_row)
                self.assertEqual(str(mbid_row["release_mbid"]), "11111111-1111-1111-1111-111111111111")
                self.assertEqual(str(mbid_row["release_group_mbid"]), "22222222-2222-2222-2222-222222222222")
                for track in library.tracks:
                    track.genres = ["Electronic"]
                    track.styles = ["Ambient"]
                return GenreResolutionStats(musicbrainz_api_calls=1)

            def fake_resolve_library_cover_art(
                library: MusicLibrary,
                _source: Path,
                *,
                connection: object | None = None,
                album_artist_split_patterns: object = (),
            ) -> CoverArtResolutionStats:
                self.assertIsNotNone(connection)
                for track in library.tracks:
                    track.artwork = TrackArtwork("image/png", b"new-track-art")
                    track.album_artwork = TrackArtwork("image/png", b"new-album-art")
                return CoverArtResolutionStats(metadata_api_calls=1, tracks_updated=2)

            with (
                patch("kukicha.use_case.commands.album_edits.scan_track", side_effect=fake_scan_track),
                patch("kukicha.use_case.commands.album_edits.resolve_library_genres", side_effect=fake_resolve_library_genres),
                patch("kukicha.use_case.commands.album_edits.resolve_library_cover_art", side_effect=fake_resolve_library_cover_art),
            ):
                result = edit_library_album_musicbrainz(database, job)

            self.assertEqual(result.tracks_scanned, 2)
            self.assertEqual(result.albums_scanned, 1)
            self.assertEqual(result.affected_album_ids, ("old-artist::album",))
            self.assertEqual(result.genre_resolution.musicbrainz_api_calls, 1)
            self.assertEqual(result.cover_art_resolution.metadata_api_calls, 1)

            connection = connect_database(database, create=False)
            try:
                row = connection.execute(
                    """
                    SELECT release_mbid, release_group_mbid
                    FROM album_musicbrainz_links
                    WHERE album_id = ?
                    """,
                    ("old-artist::album",),
                ).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(str(row["release_mbid"]), "11111111-1111-1111-1111-111111111111")
                self.assertEqual(str(row["release_group_mbid"]), "22222222-2222-2222-2222-222222222222")
                self.assertEqual(
                    [
                        str(row["genre"])
                        for row in connection.execute(
                            "SELECT genre FROM library_track_genres WHERE track_id = ? ORDER BY position",
                            (1,),
                        )
                    ],
                    ["Electronic"],
                )
                self.assertEqual(
                    [
                        str(row["style"])
                        for row in connection.execute(
                            "SELECT style FROM library_track_styles WHERE track_id = ? ORDER BY position",
                            (1,),
                        )
                    ],
                    ["Ambient"],
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
                self.assertEqual(str(artwork_row["mime_type"]), "image/png")
                self.assertEqual(bytes(artwork_row["data"]), b"new-track-art")
                album_art_row = connection.execute(
                    """
                    SELECT mime_type, data
                    FROM library_track_artwork
                    WHERE track_id = ? AND height_px = ?
                    """,
                    (1, 250),
                ).fetchone()
                self.assertIsNotNone(album_art_row)
                self.assertEqual(str(album_art_row["mime_type"]), "image/png")
                self.assertEqual(bytes(album_art_row["data"]), b"new-album-art")
            finally:
                connection.close()

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
                        album_id, release_mbid, release_group_mbid
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
                    "genre": "Electronic; Score",
                    "album_artist": "Various Artists",
                    "tracks": [
                        {
                            "track_id": 1,
                            "artist": "Wendy Carlos & Rachel Elkind",
                            "album": "New Album",
                        },
                        {
                            "track_id": 2,
                            "artist": "The Shining",
                            "album": "New Album",
                        },
                    ],
                },
            )

            with (
                patch("kukicha.use_case.commands.album_edits.write_track_audio_tags") as write_track_tags,
                patch("kukicha.use_case.commands.album_edits.scan_track") as scan_track,
                patch("kukicha.use_case.commands.album_edits.resolve_library_genres") as resolve_genres,
                patch("kukicha.use_case.commands.album_edits.resolve_library_cover_art") as resolve_cover_art,
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
                        genre="Electronic; Score",
                    ),
                    call(
                        paths[1],
                        artist="The Shining",
                        album_artist="Various Artists",
                        album="New Album",
                        genre="Electronic; Score",
                    ),
                ],
            )

            self.assertEqual(result.tracks_updated, 2)
            self.assertEqual(result.albums_scanned, 0)
            self.assertEqual(result.affected_album_ids, ())
            self.assertEqual(result.genre_resolution, GenreResolutionStats())
            self.assertEqual(result.cover_art_resolution, CoverArtResolutionStats())
            scan_track.assert_not_called()
            resolve_genres.assert_not_called()
            resolve_cover_art.assert_not_called()

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
                    WHERE album_id = ?
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

    def test_start_album_tag_edit_starts_background_tag_write_job(self) -> None:
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
                result = start_album_tag_edit(
                    runtime,
                    "old-artist::album",
                    {
                        "genre": "Electronic; Score",
                        "album_artist": "Various Artists",
                        "tracks": [
                            {
                                "track_id": 1,
                                "artist": "Wendy Carlos & Rachel Elkind",
                                "album": "New Album",
                            },
                            {
                                "track_id": 2,
                                "artist": "The Shining",
                                "album": "New Album",
                            },
                        ],
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
                    "genre": "Electronic; Score",
                    "album_artist": "Various Artists",
                    "tracks": [
                        {
                            "track_id": 1,
                            "artist": "Wendy Carlos & Rachel Elkind",
                            "album": "New Album",
                        },
                        {
                            "track_id": 2,
                            "artist": "The Shining",
                            "album": "New Album",
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
                        genre="Electronic; Score",
                    ),
                    call(
                        paths[1],
                        artist="The Shining",
                        album_artist="Various Artists",
                        album="New Album",
                        genre="Electronic; Score",
                    ),
                ],
            )
            self.assertEqual(
                result.message,
                (
                    "Tags saved for Old Artist - Album. "
                    "Rescan the affected root to update library filters, artists, and stats."
                ),
            )
            self.assertEqual(result.context["tracks_updated"], 2)
            self.assertTrue(result.context["rescan_recommended"])

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
                    "genre": "Electronic; Score",
                    "album_artist": "Various Artists",
                    "tracks": [
                        {
                            "track_id": 1,
                            "artist": "Wendy Carlos & Rachel Elkind",
                            "album": "New Album",
                        },
                        {
                            "track_id": 2,
                            "artist": "The Shining",
                            "album": "New Album",
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

            with self.assertRaisesRegex(ValueError, "track requires album title: Track One"):
                prepare_album_tag_edit_job(
                    database,
                    "old-artist::album",
                    {
                        "genre": "Electronic; Score",
                        "album_artist": "Various Artists",
                        "tracks": [
                            {
                                "track_id": 1,
                                "artist": "Wendy Carlos & Rachel Elkind",
                                "album": "",
                            },
                            {
                                "track_id": 2,
                                "artist": "The Shining",
                                "album": "New Album",
                            },
                        ],
                    },
                )

    def test_edit_library_album_tags_supports_per_track_album_titles(self) -> None:
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
                    "genre": "Electronic; Score",
                    "album_artist": "Various Artists",
                    "tracks": [
                        {
                            "track_id": 1,
                            "artist": "Artist Alpha",
                            "album": "Album Alpha",
                        },
                        {
                            "track_id": 2,
                            "artist": "Artist Beta",
                            "album": "Album Beta",
                        },
                    ],
                },
            )

            with (
                patch("kukicha.use_case.commands.album_edits.write_track_audio_tags") as write_track_tags,
                patch("kukicha.use_case.commands.album_edits.scan_track") as scan_track,
            ):
                result = edit_library_album_tags(database, job)

            self.assertEqual(
                write_track_tags.call_args_list,
                [
                    call(
                        paths[0],
                        artist="Artist Alpha",
                        album_artist="Various Artists",
                        album="Album Alpha",
                        genre="Electronic; Score",
                    ),
                    call(
                        paths[1],
                        artist="Artist Beta",
                        album_artist="Various Artists",
                        album="Album Beta",
                        genre="Electronic; Score",
                    ),
                ],
            )
            self.assertEqual(result.tracks_updated, 2)
            self.assertEqual(result.albums_scanned, 0)
            self.assertEqual(result.affected_album_ids, ())
            scan_track.assert_not_called()

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

    def test_prepare_album_musicbrainz_edit_request_rejects_non_string_ids(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            paths = (
                temp_path / "Album" / "01.mp3",
                temp_path / "Album" / "02.mp3",
            )
            self.seed_album(database, paths)

            with self.assertRaisesRegex(ValueError, "MusicBrainz release ID must be a string"):
                prepare_album_musicbrainz_edit_request(
                    database,
                    "old-artist::album",
                    {
                        "musicbrainz_release_mbid": 123,
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


def make_track_view(
    track_id: int,
    *,
    root_position: int | None,
    path: str,
    album_id: str = "aphex-twin::selected-ambient-works-volume-ii",
    album_artist: str = "Aphex Twin",
    artist: str | None = None,
    album: str = "Selected Ambient Works Volume II",
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
        album=album,
        display_album=album,
        artist=resolved_artist,
        title=f"Track {track_id}",
        display_title=f"Track {track_id}",
        table_title=f"Track {track_id}",
        queue_title=f"{album_artist} - Track {track_id}",
        track_number=str(track_id),
        disc_number="",
        disc_total="",
        year=None,
        duration="",
        duration_seconds=duration_seconds,
        grouping="",
        genres=(),
        styles=(),
        library_track_id=library_track_id,
        playlist_options=playlist_options,
    )


if __name__ == "__main__":
    unittest.main()
