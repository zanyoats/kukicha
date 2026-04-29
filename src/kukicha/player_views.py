from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote

from .use_case import LibraryQueries
from .use_case import album_list_query_from_params, album_musicbrainz_link
from .player_runtime import PlayerRuntime


def base_player_context(runtime: PlayerRuntime, **context: Any) -> dict[str, Any]:
    from .player_presenters import queue_state_payload

    base = {
        "app_title": "kukicha player",
        "queue_state": queue_state_payload(runtime.queue_state_copy()),
        "queue_url": "/queue",
    }
    base.update(context)
    return base


def build_index_context(runtime: PlayerRuntime, query_string: str) -> dict[str, Any]:
    from .player_navigation import (
        ALBUM_SORT_OPTIONS,
        DEFAULT_ALBUMS_PER_PAGE,
        album_index_url,
        checked_genre_values,
        player_page_context,
        property_filter_count,
        selected_genre_filter_count,
        selected_genre_values,
        selected_style_values,
    )

    api = LibraryQueries(runtime.database)
    query = api.expand_album_list_query(
        album_list_query_from_params(parse_qs(query_string))
    )
    filters = api.filter_options()
    album_page = api.list_album_page(query, include_track_ids=False)
    context = base_player_context(
        runtime,
        view_template="player/index.html",
        album_page=album_page,
        albums=album_page.items,
        query=query,
        filters=filters,
        selected_roots=set(query.root_positions),
        selected_artists=set(query.artists),
        selected_genres=selected_genre_values(filters, query),
        checked_genres=checked_genre_values(filters, query),
        selected_styles=selected_style_values(filters, query),
        selected_genre_filter_count=selected_genre_filter_count(filters, query),
        selected_property_count=property_filter_count(query),
        previous_url=album_index_url(query, page=album_page.page - 1)
        if album_page.has_previous
        else "",
        next_url=album_index_url(query, page=album_page.page + 1)
        if album_page.has_next
        else "",
        clear_url="/",
        sort_options=ALBUM_SORT_OPTIONS,
        default_per_page=DEFAULT_ALBUMS_PER_PAGE,
    )
    context.update(player_page_context("library"))
    return context


def album_playback_payload(
    database: Path,
    album_id: str,
    query_string: str,
) -> tuple[dict[str, object], ...]:
    from .player_presenters import track_playback_payloads, track_view

    api = LibraryQueries(database)
    query = api.expand_album_list_query(
        album_list_query_from_params(parse_qs(query_string))
    )
    album = api.get_album(
        album_id,
        root_positions=query.root_positions,
    )
    return track_playback_payloads(track_view(track) for track in album.tracks)


def playlist_playback_payload(
    database: Path,
    playlist_id: int,
) -> tuple[dict[str, object], ...]:
    from .player_presenters import playlist_item_view, track_playback_payloads

    playlist = LibraryQueries(database).get_playlist(playlist_id)
    return track_playback_payloads(
        playlist_item_view(item, playlist) for item in playlist.items
    )


def build_simple_page_context(runtime: PlayerRuntime, page_key: str) -> dict[str, Any]:
    from .player_navigation import player_page_context

    context = base_player_context(
        runtime,
        view_template="player/simple_page.html",
    )
    context.update(player_page_context(page_key))
    return context


def build_roots_page_context(runtime: PlayerRuntime) -> dict[str, Any]:
    from .player_common import plural
    from .player_navigation import player_page_context
    from .player_platform import root_picker_supported

    roots = LibraryQueries(runtime.database).library_roots()
    context = base_player_context(
        runtime,
        view_template="player/roots.html",
        roots=roots,
        count_text=f"{len(roots)} {plural(len(roots), 'root', 'roots')}",
        root_picker_supported=root_picker_supported(),
    )
    context.update(player_page_context("roots"))
    return context


def build_notifications_page_context(runtime: PlayerRuntime) -> dict[str, Any]:
    from .player_actions import group_notification_payloads_by_day
    from .player_common import plural
    from .player_navigation import player_page_context

    notifications = runtime.notification_payloads()
    notification_groups = group_notification_payloads_by_day(notifications)
    context = base_player_context(
        runtime,
        view_template="player/notifications.html",
        notification_groups=notification_groups,
        count_text=f"{len(notifications)} {plural(len(notifications), 'notification', 'notifications')}",
    )
    context.update(player_page_context("notifications"))
    return context


def build_album_context(
    runtime: PlayerRuntime,
    album_id: str,
    query_string: str,
) -> dict[str, Any]:
    from .player_navigation import (
        album_artist_links,
        album_edit_url,
        album_genre_links,
        album_index_url,
        album_style_links,
    )
    from .player_presenters import (
        album_track_meta,
        album_track_sections,
        track_view,
        track_views_with_playlist_options,
    )

    api = LibraryQueries(runtime.database)
    query = api.expand_album_list_query(
        album_list_query_from_params(parse_qs(query_string))
    )
    album = api.get_album(
        album_id,
        root_positions=query.root_positions,
    )
    track_views = track_views_with_playlist_options(
        runtime.database,
        [track_view(track) for track in album.tracks],
    )
    track_sections = album_track_sections(track_views, api.library_roots())
    filters = api.filter_options()
    return base_player_context(
        runtime,
        page_name="album",
        view_template="player/album.html",
        album=album,
        query=query,
        tracks=track_views,
        track_sections=track_sections,
        album_artist_links=album_artist_links(album, query),
        album_genre_links=album_genre_links(album, query, filters),
        album_year_text=str(album.year) if album.year else "",
        album_style_links=album_style_links(album, query, filters),
        album_track_meta=album_track_meta(album, track_views),
        album_back_url=album_index_url(query),
        album_edit_page_url=album_edit_url(album, query),
    )


def build_playlist_context(
    runtime: PlayerRuntime,
    playlist_id: int,
    query_string: str,
) -> dict[str, Any]:
    from .player_navigation import (
        album_index_url,
        playlist_cover_url,
    )
    from .player_presenters import (
        playlist_item_view,
        playlist_track_meta,
        track_table_rows,
        track_views_with_playlist_options,
    )

    api = LibraryQueries(runtime.database)
    query = api.expand_album_list_query(
        album_list_query_from_params(parse_qs(query_string))
    )
    playlist = api.get_playlist(playlist_id)
    track_views = track_views_with_playlist_options(
        runtime.database,
        [
            playlist_item_view(item, playlist, display_position=index)
            for index, item in enumerate(playlist.items)
        ],
    )
    return base_player_context(
        runtime,
        page_name="playlist",
        view_template="player/playlist.html",
        playlist=playlist,
        query=query,
        tracks=track_views,
        table_rows=track_table_rows(track_views),
        playlist_back_url=album_index_url(query),
        playlist_path_text=playlist.path,
        playlist_track_meta=playlist_track_meta(playlist, track_views),
        playlist_cover_data_url=playlist_cover_url(playlist.cover_svg, playlist.name),
    )


def build_album_edit_context(
    runtime: PlayerRuntime,
    album_id: str,
    query_string: str,
) -> dict[str, Any]:
    from .player_navigation import (
        album_edit_album_artist_value,
        album_edit_genre_value,
        album_genre_year_parts,
        album_style_parts,
        album_artist_parts,
        album_url,
    )
    from .player_presenters import (
        track_view,
    )

    api = LibraryQueries(runtime.database)
    query = api.expand_album_list_query(
        album_list_query_from_params(parse_qs(query_string))
    )
    album = api.get_album(
        album_id,
        root_positions=query.root_positions,
    )
    track_views = [track_view(track) for track in album.tracks]
    musicbrainz_link = album_musicbrainz_link(runtime.database, album.album_id)
    return base_player_context(
        runtime,
        page_name="album-edit",
        view_template="player/album_edit.html",
        album=album,
        query=query,
        tracks=track_views,
        album_artist_parts=album_artist_parts(album),
        album_genre_year_parts=album_genre_year_parts(album),
        album_style_parts=album_style_parts(album),
        album_back_url=album_url(album, query),
        album_musicbrainz_action_url=f"/api/albums/{quote(album.album_id, safe=':')}/musicbrainz",
        album_tag_edit_action_url=f"/api/albums/{quote(album.album_id, safe=':')}/tags",
        album_tag_edit_album_artist=album_edit_album_artist_value(album),
        album_tag_edit_genre=album_edit_genre_value(album),
        album_musicbrainz_release_mbid=(
            musicbrainz_link.release_mbid if musicbrainz_link is not None else ""
        ),
        album_musicbrainz_release_group_mbid=(
            musicbrainz_link.release_group_mbid if musicbrainz_link is not None else ""
        ),
    )


def build_queue_context(runtime: PlayerRuntime) -> dict[str, Any]:
    from .player_presenters import (
        QueueRow,
        queue_meta_text,
        queue_status,
        total_duration_text,
        track_table_rows,
        track_views_for_playback_ids,
    )

    state = runtime.queue_state_copy()
    api = LibraryQueries(runtime.database)
    track_views = track_views_for_playback_ids(api, state.track_ids)
    rows = [
        QueueRow(
            track=track,
            position=position,
            status=queue_status(state, track.track_id, position),
        )
        for position, track in enumerate(track_views)
    ]
    return base_player_context(
        runtime,
        page_name="queue",
        view_template="player/queue.html",
        queue_rows=rows,
        table_rows=track_table_rows(
            [row.track for row in rows],
            queue_rows=rows,
        ),
        queue_meta=queue_meta_text(state, track_views),
        queue_duration_text=total_duration_text(track_views),
        queue_back_url="/",
    )
