from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
import mimetypes
from pathlib import Path
import re
from urllib.parse import urlsplit

from .discogs import most_common_value
from .use_case import (
    AlbumDetails,
    LibraryQueries,
    LibraryRootFilterOption,
    PlaylistDetails,
    PlaylistItem,
    PlaylistMenuOption,
    PlaylistTrack,
    TrackNotFoundError,
)
from .display import display_album_title
from .models import ALBUM_ARTWORK_HEIGHT, TRACK_ARTWORK_HEIGHT
from .player_common import clamp_int, optional_int, plural
from .player_media import (
    audio_mime_type,
    audio_unsupported_reason_for_path,
    mpeg4_audio_codec_for_path,
)
from .player_navigation import playlist_cover_url
from .player_runtime import PlayerQueueState
from .scanner import is_url_resource
from .text import normalize_text

YEAR_RE = re.compile(r"\d{4}")

@dataclass(frozen=True, slots=True)
class TrackView:
    track_id: int
    album_id: str
    root_position: int | None
    path: str
    audio_url: str
    art_url: str
    album_art_url: str
    audio_codec: str
    audio_mime_type: str
    audio_unsupported_reason: str
    file_type: str
    album_artist: str
    album_artists: tuple[str, ...]
    album: str
    display_album: str
    artist: str
    title: str
    display_title: str
    table_title: str
    queue_title: str
    track_number: str
    disc_number: str
    disc_total: str
    year: int | None
    duration: str
    duration_seconds: float | None
    grouping: str
    genres: tuple[str, ...]
    styles: tuple[str, ...]
    library_track_id: int | None = None
    uses_playlist_cover: bool = False
    has_playlist_membership: bool = False
    playlist_options: tuple[PlaylistMenuOption, ...] | None = None
    duration_is_indeterminate: bool = False

    @property
    def is_playlist_item(self) -> bool:
        return self.album_id.startswith("playlist:")

@dataclass(frozen=True, slots=True)
class QueueRow:
    track: TrackView
    position: int
    status: str
    unavailable: bool = False

@dataclass(frozen=True, slots=True)
class TrackTableRow:
    track: TrackView
    group_label: str = ""
    queue_position: int | None = None
    queue_status: str = ""
    queue_unavailable: bool = False

@dataclass(frozen=True, slots=True)
class AlbumTrackSection:
    label: str
    meta: tuple[str, ...]
    table_rows: list[TrackTableRow]

@dataclass(frozen=True, slots=True)
class AlbumTagEditTrack:
    track: TrackView
    track_number: str

@dataclass(frozen=True, slots=True)
class AlbumTagEditSection:
    label: str
    meta: tuple[str, ...]
    tracks: tuple[AlbumTagEditTrack, ...]
    album: str
    album_artist: str
    genre: str
    musicbrainz_url: str = ""

def album_track_meta(album: AlbumDetails, tracks: list[TrackView]) -> tuple[str, ...]:
    parts = [f"{album.track_count} {plural(album.track_count, 'track', 'tracks')}"]
    duration = total_duration_text(tracks)
    if duration:
        parts.append(duration)
    return tuple(parts)

def playlist_track_meta(
    playlist: PlaylistDetails,
    tracks: list[TrackView],
) -> tuple[str, ...]:
    parts = [f"{playlist.track_count} {plural(playlist.track_count, 'item', 'items')}"]
    duration = total_duration_text(tracks)
    if duration:
        parts.append(duration)
    return tuple(parts)

def album_playback_track_payloads(
    api: LibraryQueries,
    albums: tuple[AlbumDetails, ...],
) -> dict[str, tuple[dict[str, object], ...]]:
    requested_track_ids = list(
        dict.fromkeys(
            track_id
            for album in albums
            for track_id in album.track_ids
        )
    )
    tracks_by_id = {
        track.track_id: track_view(track)
        for track in api.get_tracks_by_ids(requested_track_ids)
        if track.track_id is not None
    }
    return {
        album.album_id: track_playback_payloads(
            track
            for track_id in album.track_ids
            if (track := tracks_by_id.get(track_id)) is not None
        )
        for album in albums
    }

def track_playback_payloads(tracks: Iterable[TrackView]) -> tuple[dict[str, object], ...]:
    return tuple(track_playback_payload(track) for track in tracks)

def track_playback_payload(track: TrackView) -> dict[str, object]:
    return {
        "trackId": track.track_id,
        "albumId": track.album_id,
        "audioUrl": track.audio_url,
        "artUrl": track.art_url,
        "title": track.display_title,
        "albumArtist": track.album_artist,
        "albumArtists": track.album_artists,
        "album": track.display_album,
        "durationSeconds": track.duration_seconds,
        "durationIsIndeterminate": track.duration_is_indeterminate,
        "fileType": track.file_type,
        "audioMimeType": track.audio_mime_type,
        "audioCodec": track.audio_codec,
        "unsupported": track.audio_unsupported_reason,
    }

def queue_track_snapshot(track: TrackView) -> dict[str, object]:
    return {
        **track_playback_payload(track),
        "albumArtUrl": track.album_art_url,
        "artist": track.artist,
        "tableTitle": track.table_title,
        "queueTitle": track.queue_title,
        "trackNumber": track.track_number,
        "duration": track.duration,
        "libraryTrackId": track.library_track_id,
        "usesPlaylistCover": track.uses_playlist_cover,
    }

def track_views_for_playback_ids(
    api: LibraryQueries,
    playback_ids: Iterable[int],
) -> list[TrackView]:
    requested_ids = [int(playback_id) for playback_id in playback_ids]
    tracks_by_id = {
        track.track_id: track_view(track)
        for track in api.get_tracks_by_ids(
            playback_id for playback_id in requested_ids if playback_id > 0
        )
        if track.track_id is not None
    }
    playlist_items_by_playback_id = {
        item.playback_id: playlist_item_view(item)
        for item in api.get_playlist_items_by_ids(
            -playback_id for playback_id in requested_ids if playback_id < 0
        )
    }
    return [
        track
        for playback_id in requested_ids
        if (
            track := (
                tracks_by_id.get(playback_id)
                if playback_id > 0
                else playlist_items_by_playback_id.get(playback_id)
            )
        )
        is not None
    ]

def queue_track_views_for_state(
    api: LibraryQueries,
    state: PlayerQueueState,
) -> list[TrackView]:
    live_views_by_id = {
        track.track_id: track
        for track in track_views_for_playback_ids(
            api,
            (
                playback_id
                for playback_id in state.track_ids
                if playback_id not in state.unavailable_track_ids
            ),
        )
    }
    return [
        live_views_by_id.get(playback_id)
        or track_view_from_queue_snapshot(
            playback_id,
            state.snapshots[position] if position < len(state.snapshots) else {},
        )
        for position, playback_id in enumerate(state.track_ids)
    ]

def track_view_from_queue_snapshot(
    playback_id: int,
    snapshot: dict[str, object],
) -> TrackView:
    title = snapshot_string(snapshot, "title") or f"Unavailable track {playback_id}"
    album_artist = snapshot_string(snapshot, "albumArtist")
    album_artists = snapshot_string_tuple(snapshot, "albumArtists")
    if not album_artists and album_artist:
        album_artists = (album_artist,)
    album = snapshot_string(snapshot, "album")
    return TrackView(
        track_id=playback_id,
        album_id=snapshot_string(snapshot, "albumId"),
        root_position=None,
        path="",
        audio_url=snapshot_string(snapshot, "audioUrl"),
        art_url=snapshot_string(snapshot, "artUrl"),
        album_art_url=snapshot_string(snapshot, "albumArtUrl"),
        audio_codec=snapshot_string(snapshot, "audioCodec"),
        audio_mime_type=snapshot_string(snapshot, "audioMimeType"),
        audio_unsupported_reason="Unavailable",
        file_type=snapshot_string(snapshot, "fileType"),
        album_artist=album_artist,
        album_artists=album_artists,
        album=album,
        display_album=album,
        artist=snapshot_string(snapshot, "artist") or album_artist,
        title=title,
        display_title=title,
        table_title=snapshot_string(snapshot, "tableTitle") or title,
        queue_title=snapshot_string(snapshot, "queueTitle")
        or queue_track_title(album_artist, title),
        track_number=snapshot_string(snapshot, "trackNumber"),
        disc_number="",
        disc_total="",
        year=None,
        duration=snapshot_string(snapshot, "duration"),
        duration_seconds=snapshot_float(snapshot, "durationSeconds"),
        duration_is_indeterminate=snapshot_bool(snapshot, "durationIsIndeterminate"),
        grouping="",
        genres=(),
        styles=(),
        library_track_id=snapshot_optional_int(snapshot, "libraryTrackId"),
        uses_playlist_cover=snapshot_bool(snapshot, "usesPlaylistCover"),
    )

def snapshot_string(snapshot: dict[str, object], key: str) -> str:
    value = snapshot.get(key)
    return value if isinstance(value, str) else ""

def snapshot_string_tuple(snapshot: dict[str, object], key: str) -> tuple[str, ...]:
    value = snapshot.get(key)
    if not isinstance(value, Iterable) or isinstance(value, str):
        return ()
    return tuple(
        text
        for item in value
        if (text := str(item or "").strip())
    )

def snapshot_float(snapshot: dict[str, object], key: str) -> float | None:
    value = snapshot.get(key)
    try:
        number = float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
    return number if number is not None else None

def snapshot_optional_int(snapshot: dict[str, object], key: str) -> int | None:
    return optional_int(snapshot.get(key))

def snapshot_bool(snapshot: dict[str, object], key: str) -> bool:
    return bool(snapshot.get(key))

def valid_playback_ids(api: LibraryQueries, playback_ids: Iterable[int]) -> list[int]:
    requested_ids = [int(playback_id) for playback_id in playback_ids]
    valid_track_ids = {
        track.track_id
        for track in api.get_tracks_by_ids(
            playback_id for playback_id in requested_ids if playback_id > 0
        )
        if track.track_id is not None
    }
    valid_playlist_item_ids = {
        item.playback_id
        for item in api.get_playlist_items_by_ids(
            -playback_id for playback_id in requested_ids if playback_id < 0
        )
    }
    return [
        playback_id
        for playback_id in requested_ids
        if (
            playback_id in valid_track_ids
            if playback_id > 0
            else playback_id in valid_playlist_item_ids
        )
    ]

def track_views_with_playlist_options(
    database: Path,
    track_views: Iterable[TrackView],
) -> list[TrackView]:
    views = list(track_views)
    track_ids = [
        view.library_track_id
        for view in views
        if view.library_track_id is not None
    ]
    from .use_case import playlist_menu_options_by_track_id

    options_by_track_id = playlist_menu_options_by_track_id(database, track_ids)
    return [
        replace(
            view,
            playlist_options=options_by_track_id.get(view.library_track_id, ()),
        )
        if view.library_track_id is not None
        else view
        for view in views
    ]

def track_view(track: PlaylistTrack) -> TrackView:
    if track.track_id is None:
        raise TrackNotFoundError(0)
    path = Path(track.path)
    file_type = (track.file_type or path.suffix.removeprefix(".")).casefold()
    album_name = track.album or "<unknown album>"
    album_artist = track.album_artist or track.artist or "<unknown artist>"
    title = track.title or path.name
    display_title = display_track_title(track)
    return TrackView(
        track_id=track.track_id,
        library_track_id=track.track_id,
        album_id=track.album_id or "",
        root_position=track.root_position,
        path=track.path,
        audio_url=f"/audio/{track.track_id}",
        art_url=f"/art/{TRACK_ARTWORK_HEIGHT}/{track.track_id}",
        album_art_url=f"/art/{ALBUM_ARTWORK_HEIGHT}/{track.track_id}",
        audio_codec=mpeg4_audio_codec_for_path(path),
        audio_mime_type=audio_mime_type(path),
        audio_unsupported_reason=audio_unsupported_reason_for_path(path),
        file_type=file_type,
        album_artist=album_artist,
        album_artists=track.album_artists or ((album_artist,) if album_artist else ()),
        album=album_name,
        display_album=display_album_title(album_name),
        artist=track.artist or "",
        title=title,
        display_title=display_title,
        table_title=display_title,
        queue_title=queue_track_title(album_artist, display_title),
        track_number=format_track_number(track.track_number),
        disc_number=format_track_number(track.disc_number),
        disc_total=format_disc_total(track.disc_number),
        year=track_year(track.date),
        duration=format_track_duration(track.duration_seconds),
        duration_seconds=track.duration_seconds,
        duration_is_indeterminate=False,
        grouping=display_track_grouping(track),
        genres=track.genres,
        styles=track.styles,
        has_playlist_membership=track.has_playlist_membership,
    )

def playlist_item_view(
    item: PlaylistItem,
    playlist: PlaylistDetails | None = None,
    *,
    display_position: int | None = None,
) -> TrackView:
    track_number = str((display_position if display_position is not None else item.position) + 1)
    if item.track is not None:
        view = track_view(item.track)
        album_name = playlist_item_album_name(item, playlist)
        playlist_id = playlist_item_playlist_id(item, playlist)
        return replace(
            view,
            track_id=item.playback_id,
            library_track_id=item.track_id,
            album_id=f"playlist:{playlist_id}" if playlist_id else "",
            audio_url=playlist_item_audio_url(item),
            album_artist="",
            album_artists=(),
            album=album_name,
            display_album=display_album_title(album_name),
            table_title=tracked_playlist_item_table_title(view),
            queue_title=tracked_playlist_item_table_title(view),
            track_number=track_number,
        )

    item_path = item.path
    url_path = Path(urlsplit(item_path).path)
    local_path = Path(item_path)
    path_for_type = url_path if is_url_resource(item_path) else local_path
    file_type = path_for_type.suffix.removeprefix(".").casefold()
    title = item.title or item_path
    album_name = playlist_item_album_name(item, playlist)
    playlist_id = playlist_item_playlist_id(item, playlist)
    fallback_cover_url = playlist_item_cover_url(item, playlist, album_name)
    duration_seconds = None if item.duration_is_indeterminate else item.duration_seconds
    return TrackView(
        track_id=item.playback_id,
        library_track_id=None,
        album_id=f"playlist:{playlist_id}" if playlist_id else "",
        root_position=None,
        path=item_path,
        audio_url=playlist_item_audio_url(item),
        art_url=item.cover_url or fallback_cover_url,
        album_art_url=item.cover_url or fallback_cover_url,
        audio_codec="",
        audio_mime_type=playlist_item_audio_mime_type(item_path),
        audio_unsupported_reason=playlist_item_unsupported_reason(item_path),
        file_type=file_type,
        album_artist="",
        album_artists=(),
        album=album_name,
        display_album=display_album_title(album_name),
        artist="",
        title=title,
        display_title=title,
        table_title=title,
        queue_title=title,
        track_number=track_number,
        disc_number="",
        disc_total="",
        year=None,
        duration=format_track_duration(duration_seconds),
        duration_seconds=duration_seconds,
        duration_is_indeterminate=item.duration_is_indeterminate,
        grouping="",
        genres=(item.genre,) if item.genre else (),
        styles=(),
        uses_playlist_cover=item.cover_url is None,
    )

def playlist_item_album_name(
    item: PlaylistItem,
    playlist: PlaylistDetails | None,
) -> str:
    return playlist.name if playlist is not None else item.playlist_name or "Playlist"

def playlist_item_playlist_id(
    item: PlaylistItem,
    playlist: PlaylistDetails | None,
) -> int:
    return playlist.playlist_id if playlist is not None else item.playlist_id

def playlist_item_cover_url(
    item: PlaylistItem,
    playlist: PlaylistDetails | None,
    playlist_name: str,
) -> str:
    cover_svg = playlist.cover_svg if playlist is not None else item.playlist_cover_svg
    return playlist_cover_url(cover_svg, playlist_name)

def tracked_playlist_item_table_title(track: TrackView) -> str:
    artist = track.album_artist or track.artist
    title = track.display_title
    return f"{artist} - {title}" if artist else title

def queue_track_title(prefix: str, title: str) -> str:
    return f"{prefix} - {title}" if prefix else title

def playlist_item_audio_url(item: PlaylistItem) -> str:
    if is_url_resource(item.path):
        return item.path
    return f"/playlist-audio/{item.playlist_item_id}"

def playlist_item_audio_mime_type(path: str) -> str:
    if is_url_resource(path):
        suffix_path = Path(urlsplit(path).path)
        return mimetypes.guess_type(suffix_path.name)[0] or ""
    return audio_mime_type(Path(path))

def playlist_item_unsupported_reason(path: str) -> str:
    if is_url_resource(path):
        return ""
    return audio_unsupported_reason_for_path(Path(path))

def queue_status(state: PlayerQueueState, track_id: int, position: int) -> str:
    if track_id in state.unavailable_track_ids:
        return "Unavailable"
    if track_id in state.errored_track_ids:
        return "Error"
    if position < state.position:
        return "Played"
    if position == state.position:
        if state.loaded_track_id == track_id and not state.paused:
            return "Now"
        if state.loaded_track_id == track_id:
            return "Paused"
    return "Next"

def queue_meta_text(state: PlayerQueueState, tracks: list[TrackView]) -> str:
    played_count = min(state.position, len(tracks))
    parts = [
        f"{len(tracks)} {plural(len(tracks), 'track', 'tracks')}",
        f"{played_count} played",
    ]
    duration = total_duration_text(tracks)
    if duration:
        parts.append(duration)
    return " - ".join(parts)

def total_duration_text(tracks: list[TrackView]) -> str:
    total = sum(float(track.duration_seconds or 0) for track in tracks)
    total_minutes = round(total / 60)
    if total_minutes <= 0:
        return ""
    hours, minutes = divmod(total_minutes, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours} {plural(hours, 'hour', 'hours')}")
    if minutes:
        parts.append(f"{minutes} {plural(minutes, 'minute', 'minutes')}")
    return ", ".join(parts)

def track_table_rows(
    tracks: list[TrackView],
    *,
    queue_rows: list[QueueRow] | None = None,
) -> list[TrackTableRow]:
    multi_disc_keys = multi_disc_album_keys(tracks)
    previous_header_key = ""
    rows: list[TrackTableRow] = []
    for index, track in enumerate(tracks):
        queue_row = queue_rows[index] if queue_rows is not None else None
        group_label, header_key = track_group_header(track, multi_disc_keys)
        if header_key == previous_header_key:
            group_label = ""
        previous_header_key = header_key
        rows.append(
            TrackTableRow(
                track=track,
                group_label=group_label,
                queue_position=queue_row.position if queue_row is not None else None,
                queue_status=queue_row.status if queue_row is not None else "",
                queue_unavailable=queue_row.unavailable if queue_row is not None else False,
            )
        )
    return rows

def album_track_sections(
    tracks: list[TrackView],
    roots: tuple[LibraryRootFilterOption, ...],
) -> list[AlbumTrackSection]:
    root_positions = {track.root_position for track in tracks}
    root_labels = album_track_root_labels(root_positions, roots)
    roots_by_position = {root.position: root for root in roots}
    grouped_tracks: dict[tuple[int | None, str], list[TrackView]] = {}
    for track in tracks:
        release_path = album_track_section_release_path(track, roots_by_position)
        grouped_tracks.setdefault((track.root_position, release_path), []).append(track)

    if len(grouped_tracks) <= 1:
        return [
            AlbumTrackSection(
                label="",
                meta=album_track_section_meta(tracks),
                table_rows=track_table_rows(tracks),
            )
        ]

    sections: list[AlbumTrackSection] = []
    for (root_position, release_path), section_tracks in sorted(
        grouped_tracks.items(),
        key=lambda item: album_track_section_sort_key(item[0][0], item[0][1], root_labels),
    ):
        sections.append(
            AlbumTrackSection(
                label=album_track_section_label(root_position, release_path, root_labels),
                meta=album_track_section_meta(section_tracks),
                table_rows=track_table_rows(section_tracks),
            )
        )
    return sections

def album_tag_edit_sections(
    tracks: list[TrackView],
    roots: tuple[LibraryRootFilterOption, ...],
) -> list[AlbumTagEditSection]:
    return [
        AlbumTagEditSection(
            label=section.label,
            meta=section.meta,
            tracks=album_tag_edit_tracks(row.track for row in section.table_rows),
            album=album_tag_edit_section_album(row.track for row in section.table_rows),
            album_artist=album_tag_edit_section_album_artist(
                row.track for row in section.table_rows
            ),
            genre=album_tag_edit_section_genre(row.track for row in section.table_rows),
        )
        for section in album_track_sections(tracks, roots)
    ]


def album_tag_edit_section_for_tracks(
    tracks: list[TrackView],
) -> AlbumTagEditSection:
    return AlbumTagEditSection(
        label="",
        meta=album_track_section_meta(tracks),
        tracks=album_tag_edit_tracks(tracks),
        album=album_tag_edit_section_album(tracks),
        album_artist=album_tag_edit_section_album_artist(tracks),
        genre=album_tag_edit_section_genre(tracks),
    )


def album_tag_edit_tracks(tracks: Iterable[TrackView]) -> tuple[AlbumTagEditTrack, ...]:
    section_tracks = tuple(tracks)
    if any(track.track_number.strip() for track in section_tracks):
        track_numbers_by_id = {
            track.track_id: track.track_number
            for track in section_tracks
        }
    else:
        track_numbers_by_id = {
            track.track_id: str(position)
            for position, track in enumerate(
                sorted(section_tracks, key=album_tag_edit_track_number_sort_key),
                start=1,
            )
        }
    return tuple(
        AlbumTagEditTrack(
            track=track,
            track_number=track_numbers_by_id.get(track.track_id, ""),
        )
        for track in section_tracks
    )

def album_tag_edit_track_number_sort_key(track: TrackView) -> tuple[str, int]:
    return (track.path.casefold(), track.track_id)

def album_tag_edit_section_album(tracks: Iterable[TrackView]) -> str:
    return most_common_value(track.album for track in tracks) or ""

def album_tag_edit_section_album_artist(tracks: Iterable[TrackView]) -> str:
    return most_common_value(track.album_artist for track in tracks) or ""

def album_tag_edit_section_genre(tracks: Iterable[TrackView]) -> str:
    values: list[str] = []
    seen: set[str] = set()
    for track in tracks:
        for value in (*track.genres, *track.styles):
            text = value.strip()
            if not text:
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            values.append(text)
    return "; ".join(values)

def album_track_section_meta(tracks: list[TrackView]) -> tuple[str, ...]:
    parts = [f"{len(tracks)} {plural(len(tracks), 'track', 'tracks')}"]
    duration = total_duration_text(tracks)
    if duration:
        parts.append(duration)
    return tuple(parts)

def album_track_root_labels(
    root_positions: set[int | None],
    roots: tuple[LibraryRootFilterOption, ...],
) -> dict[int, str]:
    used_positions = {
        root_position
        for root_position in root_positions
        if root_position is not None
    }
    label_counts: dict[str, int] = {}
    for root in roots:
        if root.position in used_positions:
            label_counts[root.label] = label_counts.get(root.label, 0) + 1

    labels: dict[int, str] = {}
    for root in roots:
        if root.position not in used_positions:
            continue
        labels[root.position] = root.path if label_counts[root.label] > 1 else root.label
    return labels

def album_track_section_sort_key(
    root_position: int | None,
    release_path: str,
    root_labels: dict[int, str],
) -> tuple[int, int, str, int, str]:
    depth = len(Path(release_path).parts) if release_path else 0
    if root_position is None:
        return (1, 0, "", depth, release_path.casefold())
    return (
        0,
        root_position,
        root_labels.get(root_position, ""),
        depth,
        release_path.casefold(),
    )

def album_track_section_label(
    root_position: int | None,
    release_path: str,
    root_labels: dict[int, str],
) -> str:
    if root_position is None:
        if not release_path:
            return "Unknown root"
        return f"{release_path.rstrip('/')}/"

    base_label = root_labels.get(root_position, f"Root {root_position + 1}").rstrip("/")
    relative_path = release_path.strip("/")
    if not relative_path:
        return f"{base_label}/"
    return f"{base_label}/{relative_path}/"

def album_track_section_release_path(
    track: TrackView,
    roots_by_position: dict[int, LibraryRootFilterOption],
) -> str:
    parent = Path(track.path).parent
    if track.root_position is None:
        return str(parent)

    root = roots_by_position.get(track.root_position)
    if root is None:
        return str(parent)

    try:
        relative_parent = parent.relative_to(Path(root.path))
    except ValueError:
        return str(parent)

    if not relative_parent.parts:
        return ""

    album_keys = tuple(
        key
        for key in dict.fromkeys(normalize_text(value) for value in (track.album, track.display_album))
        if key
    )
    if not album_keys:
        return relative_parent.as_posix()

    parts = relative_parent.parts
    for length in range(len(parts), 0, -1):
        segment_key = normalize_text(parts[length - 1])
        if any(album_key == segment_key or album_key in segment_key for album_key in album_keys):
            return Path(*parts[:length]).as_posix()
    return relative_parent.as_posix()

def track_group_header(
    track: TrackView,
    multi_disc_keys: set[str],
) -> tuple[str, str]:
    album_key = track_album_key(track)
    disc_number = track.disc_number.strip()
    grouping = track.grouping.strip()
    show_disc = bool(not grouping and disc_number and album_key in multi_disc_keys)
    parts: list[str] = []
    if show_disc:
        parts.append(f"Disc {disc_number}")
    if grouping:
        parts.append(grouping)
    if not parts:
        return "", ""
    header_key = f"{album_key}\0{disc_number if show_disc else ''}\0{grouping}"
    return " - ".join(parts), header_key

def multi_disc_album_keys(tracks: list[TrackView]) -> set[str]:
    albums: dict[str, dict[str, object]] = {}
    for track in tracks:
        key = track_album_key(track)
        album = albums.setdefault(
            key,
            {
                "disc_numbers": set(),
                "has_multiple_disc_total": False,
            },
        )
        disc_number = track.disc_number.strip()
        if disc_number and isinstance(album["disc_numbers"], set):
            album["disc_numbers"].add(disc_number)
        if parse_number(track.disc_total) > 1:
            album["has_multiple_disc_total"] = True
    return {
        key
        for key, album in albums.items()
        if album["has_multiple_disc_total"] or len(album["disc_numbers"]) > 1
    }

def track_album_key(track: TrackView) -> str:
    return f"{track.album_artist}\0{track.album}"

def parse_number(value: str | None) -> int:
    if not value:
        return 0
    value = value.strip()
    return int(value) if value.isdigit() else 0

def queue_state_payload(state: PlayerQueueState) -> dict[str, object]:
    return {
        "track_ids": list(state.track_ids),
        "position": state.position,
        "loaded_track_id": state.loaded_track_id,
        "paused": state.paused,
        "errored_track_ids": list(state.errored_track_ids),
        "unavailable_track_ids": list(state.unavailable_track_ids),
        "track_snapshots": [dict(snapshot) for snapshot in state.snapshots],
    }

def normalized_queue_state(
    track_ids: Iterable[int],
    *,
    position: object = 0,
    loaded_track_id: object = None,
    paused: object = True,
    errored_track_ids: object = (),
    unavailable_track_ids: object = (),
    snapshots: object = (),
) -> PlayerQueueState:
    normalized_track_ids = [int(track_id) for track_id in track_ids]
    if not normalized_track_ids:
        return PlayerQueueState(track_ids=[], position=0, loaded_track_id=None, paused=True)

    normalized_unavailable_track_ids = normalized_queue_error_ids(
        unavailable_track_ids,
        normalized_track_ids,
    )
    unavailable_ids = set(normalized_unavailable_track_ids)
    normalized_position = clamp_int(position, 0, len(normalized_track_ids))
    if (
        normalized_position < len(normalized_track_ids)
        and normalized_track_ids[normalized_position] in unavailable_ids
    ):
        normalized_position = next_available_queue_position(
            normalized_track_ids,
            unavailable_ids,
            normalized_position,
        )
    normalized_loaded_track_id = optional_int(loaded_track_id)
    if (
        normalized_loaded_track_id not in normalized_track_ids
        or normalized_loaded_track_id in unavailable_ids
    ):
        normalized_loaded_track_id = (
            normalized_track_ids[normalized_position]
            if (
                normalized_position < len(normalized_track_ids)
                and normalized_track_ids[normalized_position] not in unavailable_ids
            )
            else None
        )

    normalized_errored_track_ids = normalized_queue_error_ids(
        errored_track_ids,
        normalized_track_ids,
    )

    return PlayerQueueState(
        track_ids=normalized_track_ids,
        position=normalized_position,
        loaded_track_id=normalized_loaded_track_id,
        paused=bool(paused) if normalized_loaded_track_id is not None else True,
        errored_track_ids=normalized_errored_track_ids,
        unavailable_track_ids=normalized_unavailable_track_ids,
        snapshots=normalized_queue_snapshots(snapshots, len(normalized_track_ids)),
    )

def next_available_queue_position(
    track_ids: list[int],
    unavailable_ids: set[int],
    position: int,
) -> int:
    for index in range(position + 1, len(track_ids)):
        if track_ids[index] not in unavailable_ids:
            return index
    return len(track_ids)

def reset_queue_state(state: PlayerQueueState) -> None:
    state.track_ids = []
    state.position = 0
    state.loaded_track_id = None
    state.paused = True
    state.errored_track_ids = []
    state.unavailable_track_ids = []
    state.snapshots = []

def normalized_queue_snapshots(value: object, count: int) -> list[dict[str, object]]:
    snapshots: list[dict[str, object]] = []
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes)):
        value = ()
    for item in value:
        snapshots.append(dict(item) if isinstance(item, dict) else {})
        if len(snapshots) >= count:
            break
    while len(snapshots) < count:
        snapshots.append({})
    return snapshots

def normalized_queue_error_ids(
    errored_track_ids: object,
    valid_track_ids: list[int],
) -> list[int]:
    if (
        isinstance(errored_track_ids, str)
        or not isinstance(errored_track_ids, Iterable)
    ):
        return []

    valid_ids = set(valid_track_ids)
    seen: set[int] = set()
    normalized_ids: list[int] = []
    for track_id in errored_track_ids:
        try:
            normalized_track_id = int(track_id)
        except (TypeError, ValueError):
            continue
        if normalized_track_id not in valid_ids or normalized_track_id in seen:
            continue
        seen.add(normalized_track_id)
        normalized_ids.append(normalized_track_id)
    return normalized_ids

def format_track_number(value: str | None) -> str:
    if not value:
        return ""
    return value.split("/", maxsplit=1)[0].strip()

def format_disc_total(value: str | None) -> str:
    if not value or "/" not in value:
        return ""
    return value.split("/", maxsplit=1)[1].strip()

def track_year(value: str | None) -> int | None:
    if not value:
        return None
    match = YEAR_RE.search(value)
    return int(match.group(0)) if match else None

def format_track_duration(value: float | None) -> str:
    if value is None or value <= 0:
        return ""

    total_seconds = int(value)
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"

def display_track_title(track: PlaylistTrack) -> str:
    if track.movement_name:
        return track.movement_name
    title = track.title or Path(track.path).name
    return strip_grouping_prefix(title, display_track_grouping(track))

def display_track_grouping(track: PlaylistTrack) -> str:
    return track.work or track.grouping or ""

def strip_grouping_prefix(title: str, grouping: str | None) -> str:
    grouping = grouping.strip() if grouping else ""
    if not grouping:
        return title

    match = re.match(
        rf"{re.escape(grouping)}\s*:\s*(?P<title>.+)",
        title.strip(),
        re.IGNORECASE,
    )
    if not match:
        return title
    return match.group("title").strip() or title
