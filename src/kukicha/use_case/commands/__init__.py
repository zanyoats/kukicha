from __future__ import annotations

from .actions import (
    list_player_actions,
    record_player_action,
)
from .album_edits import (
    edit_library_album_musicbrainz,
    edit_library_album_tags,
    prepare_album_musicbrainz_edit_job,
    prepare_album_musicbrainz_edit_request,
    prepare_album_tag_edit_job,
)
from .player import (
    playlist_audio_path,
    start_add_root,
    start_album_musicbrainz_edit,
    start_album_tag_edit,
    start_delete_root,
    start_rescan_root,
    track_artwork,
    track_audio_path,
    update_playback,
    update_queue,
    update_track_playlist_membership,
)
from .playlists import (
    PlaylistFileUpdateJob,
    PlaylistMenuOption,
    playlist_menu_options_by_track_id,
    set_track_playlist_membership,
    set_track_playlist_membership_database,
)
from .roots import (
    create_library_root,
    delete_library_root,
    library_job_detail_lines,
    library_job_summary_text,
    library_scan_progress_text,
    rescan_library_root,
    scan_library_with_new_root,
)
from .startup import prepare_player_database

__all__ = [
    "create_library_root",
    "delete_library_root",
    "edit_library_album_musicbrainz",
    "edit_library_album_tags",
    "library_job_detail_lines",
    "library_job_summary_text",
    "library_scan_progress_text",
    "playlist_audio_path",
    "PlaylistFileUpdateJob",
    "PlaylistMenuOption",
    "playlist_menu_options_by_track_id",
    "list_player_actions",
    "record_player_action",
    "prepare_album_musicbrainz_edit_job",
    "prepare_album_musicbrainz_edit_request",
    "prepare_album_tag_edit_job",
    "prepare_player_database",
    "rescan_library_root",
    "scan_library_with_new_root",
    "set_track_playlist_membership",
    "set_track_playlist_membership_database",
    "start_add_root",
    "start_album_musicbrainz_edit",
    "start_album_tag_edit",
    "start_delete_root",
    "start_rescan_root",
    "track_artwork",
    "track_audio_path",
    "update_playback",
    "update_queue",
    "update_track_playlist_membership",
]
