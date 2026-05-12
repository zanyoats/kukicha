from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote

from .use_case import ALBUM_LIST_SORT_RECENTLY_ADDED, AlbumListQuery, LibraryQueries
from .use_case import album_list_query_from_params, album_musicbrainz_link
from .player_runtime import PlayerRuntime


def base_player_context(runtime: PlayerRuntime, **context: Any) -> dict[str, Any]:
    from .player_config import (
        DEFAULT_ACCENT_COLOR,
        DEFAULT_APPEARANCE,
        DEFAULT_TOAST_TIMEOUT_MS,
        player_theme_context,
    )
    from .player_presenters import queue_state_payload

    base = {
        "app_title": "kukicha",
        "queue_state": queue_state_payload(runtime.queue_state_copy()),
        "queue_url": "/queue",
        "toast_timeout_ms": player_option_int(
            runtime,
            "toast_timeout_ms",
            DEFAULT_TOAST_TIMEOUT_MS,
        ),
    }
    base.update(
        player_theme_context(
            player_option_string(runtime, "accent_color", DEFAULT_ACCENT_COLOR),
            player_option_string(runtime, "appearance", DEFAULT_APPEARANCE),
        )
    )
    base.update(context)
    return base


def player_option_int(runtime: PlayerRuntime, name: str, default: int) -> int:
    options = getattr(runtime, "options", None)
    value = getattr(options, name, default) if options is not None else default
    return value if type(value) is int else default


def player_option_string(runtime: PlayerRuntime, name: str, default: str) -> str:
    options = getattr(runtime, "options", None)
    value = getattr(options, name, default) if options is not None else default
    return value if isinstance(value, str) else default


def album_index_query_from_query_string(query_string: str) -> AlbumListQuery:
    parsed = album_list_query_from_params(parse_qs(query_string))
    return AlbumListQuery(
        artists=parsed.artists,
        album=parsed.album,
        genres=parsed.genres,
        styles=parsed.styles,
        genre_filters=parsed.genre_filters,
        page=parsed.page,
        per_page=parsed.per_page,
        search=parsed.search,
        sort=parsed.sort,
        cursor=parsed.cursor,
        is_playlist=False,
    )


def playlist_index_query_from_query_string(_query_string: str) -> AlbumListQuery:
    return AlbumListQuery(
        sort=ALBUM_LIST_SORT_RECENTLY_ADDED,
        is_playlist=True,
    )


def build_index_context(runtime: PlayerRuntime, query_string: str) -> dict[str, Any]:
    from .player_navigation import (
        ALBUM_SORT_OPTIONS,
        DEFAULT_ALBUMS_PER_PAGE,
        album_index_url,
        checked_genre_values,
        player_page_context,
        selected_genre_filter_count,
        selected_genre_values,
        selected_style_values,
    )

    api = LibraryQueries(runtime.database)
    query = api.expand_album_list_query(album_index_query_from_query_string(query_string))
    filters = runtime.library_filter_options()
    album_page = api.list_album_page(query)
    context = base_player_context(
        runtime,
        view_template="player/index.html",
        album_page=album_page,
        albums=album_page.items,
        query=query,
        filters=filters,
        selected_genres=selected_genre_values(filters, query),
        checked_genres=checked_genre_values(filters, query),
        selected_styles=selected_style_values(filters, query),
        selected_genre_filter_count=selected_genre_filter_count(filters, query),
        previous_url=album_index_url(
            query,
            cursor=album_page.previous_cursor,
        )
        if album_page.has_previous
        else "",
        next_url=album_index_url(
            query,
            cursor=album_page.next_cursor,
        )
        if album_page.has_next
        else "",
        clear_url="/albums",
        filter_action_url="/albums",
        show_filter_form=True,
        show_filter_controls=True,
        show_sort_controls=True,
        search_placeholder="Search albums and artists",
        empty_message="No albums matched these filters.",
        pagination_label="Album pages",
        show_pagination_controls=True,
        sort_options=ALBUM_SORT_OPTIONS,
        default_per_page=DEFAULT_ALBUMS_PER_PAGE,
    )
    context.update(player_page_context("library"))
    return context


def build_home_context(runtime: PlayerRuntime) -> dict[str, Any]:
    from .use_case import home_dashboard
    from .player_navigation import player_page_context

    dashboard = home_dashboard(runtime.database)
    context = base_player_context(
        runtime,
        view_template="player/home.html",
        dashboard=dashboard,
        stat_label=home_stat_label,
        added_label=home_added_label,
    )
    queue_state = context.get("queue_state")
    loaded_track_id = (
        queue_state.get("loaded_track_id")
        if isinstance(queue_state, dict)
        else None
    )
    continue_listening = (
        dashboard.now_playing
        if dashboard.now_playing is not None and loaded_track_id is not None
        else None
    )
    context.update(
        continue_listening=continue_listening,
        show_history_empty=not dashboard.has_listening_history and continue_listening is None,
    )
    context.update(player_page_context("home"))
    return context


def build_playlist_index_context(runtime: PlayerRuntime, query_string: str) -> dict[str, Any]:
    from .player_navigation import player_page_context

    api = LibraryQueries(runtime.database)
    query = playlist_index_query_from_query_string(query_string)
    album_page = api.list_album_page(query)
    context = base_player_context(
        runtime,
        view_template="player/index.html",
        album_page=album_page,
        albums=album_page.items,
        query=query,
        previous_url="",
        next_url="",
        empty_message="No playlists found.",
        pagination_label="Playlist pages",
        show_filter_form=False,
        show_pagination_controls=False,
    )
    context.update(player_page_context("playlists"))
    return context


def album_playback_payload(
    database: Path,
    album_id: str,
) -> tuple[dict[str, object], ...]:
    from .player_presenters import track_playback_payloads, track_view

    album = LibraryQueries(database).get_album(album_id)
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


def build_not_found_context(runtime: PlayerRuntime, message: str) -> dict[str, Any]:
    return base_player_context(
        runtime,
        page_name="not-found",
        page_key="not-found",
        page_heading="Not Found",
        count_text="",
        view_template="player/not_found.html",
        not_found_message=message,
        not_found_links=(
            {"label": "Albums", "url": "/albums"},
            {"label": "Artists", "url": "/artists"},
            {"label": "Playlists", "url": "/playlists"},
            {"label": "Queue", "url": "/queue"},
        ),
    )


def home_stat_label(play_count: int, timestamp: str) -> str:
    from .player_common import plural

    parts = [f"{play_count} {plural(play_count, 'play', 'plays')}"]
    date_text = timestamp[:10] if timestamp else ""
    if date_text:
        parts.append(date_text)
    return " - ".join(parts)


def home_added_label(timestamp: str | None) -> str:
    return f"Added {timestamp[:10]}" if timestamp else "Recently Added"


def build_help_page_context(runtime: PlayerRuntime, options: Any) -> dict[str, Any]:
    from .app_metadata import kukicha_version
    from .player_config import player_config_summary
    from .player_navigation import player_page_context

    context = base_player_context(
        runtime,
        view_template="player/help.html",
        app_version=kukicha_version(),
        config_summary=player_config_summary(options=options),
    )
    context.update(player_page_context("help"))
    return context


def build_artists_page_context(runtime: PlayerRuntime) -> dict[str, Any]:
    from .player_common import plural
    from .player_navigation import artist_cloud_links, player_page_context

    stats = LibraryQueries(runtime.database).library_stats()
    artists = artist_cloud_links(stats.album_artists)
    context = base_player_context(
        runtime,
        view_template="player/artists.html",
        artists=artists,
        count_text=f"{len(artists)} {plural(len(artists), 'artist', 'artists')}",
    )
    context.update(player_page_context("artists"))
    return context


def build_roots_page_context(runtime: PlayerRuntime) -> dict[str, Any]:
    from .player_common import plural
    from .player_navigation import player_page_context

    api = LibraryQueries(runtime.database)
    roots = api.library_roots()
    root_stats_by_position = {
        stat.root_position: stat
        for stat in api.library_root_stats()
    }
    context = base_player_context(
        runtime,
        view_template="player/roots.html",
        roots=roots,
        root_stats_by_position=root_stats_by_position,
        count_text=f"{len(roots)} {plural(len(roots), 'root', 'roots')}",
    )
    context.update(player_page_context("roots"))
    return context


def build_artist_split_rules_page_context(runtime: PlayerRuntime) -> dict[str, Any]:
    from .player_common import plural
    from .player_navigation import player_page_context

    album_artist_mappings = LibraryQueries(runtime.database).album_artist_split_mappings()
    context = base_player_context(
        runtime,
        view_template="player/artist_split_rules.html",
        album_artist_mappings=album_artist_mappings,
        count_text=(
            f"{len(album_artist_mappings)} "
            f"{plural(len(album_artist_mappings), 'mapping', 'mappings')}"
        ),
    )
    context.update(player_page_context("artist-split-rules"))
    return context


def build_musicbrainz_overrides_page_context(runtime: PlayerRuntime) -> dict[str, Any]:
    from .player_common import plural
    from .player_navigation import player_page_context

    overrides = LibraryQueries(runtime.database).album_musicbrainz_overrides()
    context = base_player_context(
        runtime,
        view_template="player/musicbrainz_overrides.html",
        musicbrainz_overrides=tuple(
            {
                "album_id": override.album_id,
                "album": override.album,
                "artist": override.artist,
                "year": override.year,
                "release_mbid": override.release_mbid,
                "release_group_mbid": override.release_group_mbid,
                "is_current_album": override.is_current_album,
                "album_url": f"/albums/{quote(override.album_id, safe=':')}"
                if override.is_current_album
                else "",
                "album_edit_url": f"/albums/{quote(override.album_id, safe=':')}/edit"
                if override.is_current_album
                else "",
                "delete_url": (
                    f"/api/musicbrainz-overrides/{quote(override.album_id, safe=':')}/delete"
                ),
            }
            for override in overrides
        ),
        count_text=f"{len(overrides)} {plural(len(overrides), 'override', 'overrides')}",
    )
    context.update(player_page_context("musicbrainz-overrides"))
    return context


def build_cache_page_context(runtime: PlayerRuntime) -> dict[str, Any]:
    from .player_common import plural
    from .player_navigation import player_page_context

    cache_stats = LibraryQueries(runtime.database).cache_stats()
    total_entries = sum(stat.count for stat in cache_stats)
    context = base_player_context(
        runtime,
        view_template="player/cache.html",
        cache_stats=cache_stats,
        count_text=f"{total_entries} {plural(total_entries, 'entry', 'entries')}",
    )
    context.update(player_page_context("cache"))
    return context


def build_jobs_page_context(runtime: PlayerRuntime) -> dict[str, Any]:
    from .player_jobs import group_job_payloads_by_day
    from .player_common import plural
    from .player_navigation import player_page_context

    jobs = runtime.job_payloads()
    job_groups = group_job_payloads_by_day(jobs)
    context = base_player_context(
        runtime,
        view_template="player/jobs.html",
        job_groups=job_groups,
        count_text=f"{len(jobs)} {plural(len(jobs), 'job', 'jobs')}",
    )
    context.update(player_page_context("jobs"))
    return context


def build_album_context(
    runtime: PlayerRuntime,
    album_id: str,
    _query_string: str,
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
    query = AlbumListQuery()
    album = api.get_album(album_id)
    track_views = track_views_with_playlist_options(
        runtime.database,
        [track_view(track) for track in album.tracks],
    )
    roots = api.library_roots()
    track_sections = album_track_sections(track_views, roots)
    filters = runtime.library_filter_options()
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
        playlist_cover_url,
        playlist_index_url,
    )
    from .player_presenters import (
        playlist_item_view,
        playlist_track_meta,
        track_table_rows,
        track_views_with_playlist_options,
    )

    api = LibraryQueries(runtime.database)
    query = playlist_index_query_from_query_string(query_string)
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
        playlist_back_url=playlist_index_url(query),
        playlist_index_url=playlist_index_url(query),
        playlist_path_text=playlist.path,
        playlist_track_meta=playlist_track_meta(playlist, track_views),
        playlist_cover_data_url=playlist_cover_url(playlist.cover_svg, playlist.name),
    )


def build_album_edit_context(
    runtime: PlayerRuntime,
    album_id: str,
    _query_string: str,
) -> dict[str, Any]:
    from .player_navigation import (
        album_artist_parts,
        album_genre_parts,
        album_style_parts,
        album_url,
    )
    from .player_presenters import (
        album_tag_edit_section_for_tracks,
        album_tag_edit_sections,
        track_view,
    )

    api = LibraryQueries(runtime.database)
    query = AlbumListQuery()
    album = api.get_album(album_id)
    track_views = [track_view(track) for track in album.tracks]
    musicbrainz_link = album_musicbrainz_link(runtime.database, album.album_id)
    roots = api.library_roots()
    musicbrainz_sections = album_musicbrainz_edit_sections(
        runtime.database,
        album.album_id,
        track_views,
        roots,
    )
    return base_player_context(
        runtime,
        page_name="album-edit",
        view_template="player/album_edit.html",
        album=album,
        query=query,
        tracks=track_views,
        album_musicbrainz_sections=musicbrainz_sections,
        album_tag_edit_section=album_tag_edit_section_for_tracks(track_views),
        album_artist_parts=album_artist_parts(album),
        album_year_text=str(album.year) if album.year else "",
        album_genre_parts=album_genre_parts(album),
        album_style_parts=album_style_parts(album),
        album_back_url=album_url(album, query),
        album_edit_action_url=f"/api/albums/{quote(album.album_id, safe=':')}/edit",
        album_musicbrainz_release_mbid=(
            musicbrainz_link.release_mbid if musicbrainz_link is not None else ""
        ),
        album_musicbrainz_release_group_mbid=(
            musicbrainz_link.release_group_mbid if musicbrainz_link is not None else ""
        ),
    )


def album_musicbrainz_edit_sections(
    database: Path,
    album_id: str,
    track_views: list[Any],
    roots: tuple[Any, ...],
) -> list[Any]:
    from .player_presenters import album_tag_edit_sections
    from .use_case.database import connect_database
    from .use_case.musicbrainz import load_album_musicbrainz_track_links

    sections = album_tag_edit_sections(track_views, roots)
    paths = tuple(
        item.track.path
        for section in sections
        for item in section.tracks
        if item.track.path
    )
    with connect_database(database, create=False) as connection:
        track_links = load_album_musicbrainz_track_links(connection, paths)

    fallback_url = ""
    if len(sections) == 1:
        fallback_url = musicbrainz_url_for_album_link(album_musicbrainz_link(database, album_id))

    resolved_sections = []
    for section in sections:
        link_values = tuple(
            dict.fromkeys(
                (
                    link.release_mbid or "",
                    link.release_group_mbid or "",
                )
                for item in section.tracks
                for link in (track_links.get(item.track.path),)
                if link is not None and (link.release_mbid or link.release_group_mbid)
            )
        )
        section_url = (
            musicbrainz_url_for_link(*link_values[0])
            if len(link_values) == 1
            else ""
        )
        resolved_sections.append(
            replace(section, musicbrainz_url=section_url or fallback_url)
        )
    return resolved_sections


def musicbrainz_url_for_album_link(link: Any) -> str:
    if link is None:
        return ""
    return musicbrainz_url_for_link(link.release_mbid, link.release_group_mbid)


def musicbrainz_url_for_link(
    release_mbid: str | None,
    release_group_mbid: str | None,
) -> str:
    if release_mbid:
        return f"https://musicbrainz.org/release/{release_mbid}"
    if release_group_mbid:
        return f"https://musicbrainz.org/release-group/{release_group_mbid}"
    return ""


def build_queue_context(runtime: PlayerRuntime) -> dict[str, Any]:
    from .player_presenters import (
        QueueRow,
        queue_meta_text,
        queue_status,
        queue_track_views_for_state,
        total_duration_text,
        track_table_rows,
    )

    state = runtime.queue_state_copy()
    api = LibraryQueries(runtime.database)
    track_views = queue_track_views_for_state(api, state)
    rows = [
        QueueRow(
            track=track,
            position=position,
            status=queue_status(state, track.track_id, position),
            unavailable=track.track_id in state.unavailable_track_ids,
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
