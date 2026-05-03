from __future__ import annotations

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
        derived_control_accent,
        player_accent_theme,
        player_appearance_theme,
    )
    from .player_presenters import queue_state_payload

    accent_theme = player_accent_theme(
        player_option_string(runtime, "accent_color", DEFAULT_ACCENT_COLOR)
    )
    appearance_theme = player_appearance_theme(
        player_option_string(runtime, "appearance", DEFAULT_APPEARANCE)
    )
    base = {
        "app_title": "kukicha",
        "queue_state": queue_state_payload(runtime.queue_state_copy()),
        "queue_url": "/queue",
        "accent_color": accent_theme.accent,
        "accent_theme": accent_theme,
        "appearance_theme": appearance_theme,
        "control_accent": derived_control_accent(accent_theme.accent, appearance_theme),
        "toast_timeout_ms": player_option_int(
            runtime,
            "toast_timeout_ms",
            DEFAULT_TOAST_TIMEOUT_MS,
        ),
    }
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
        root_positions=parsed.root_positions,
        genres=parsed.genres,
        styles=parsed.styles,
        genre_filters=parsed.genre_filters,
        has_cover=parsed.has_cover,
        is_compilation=parsed.is_compilation,
        is_work=parsed.is_work,
        page=parsed.page,
        per_page=parsed.per_page,
        search=parsed.search,
        sort=parsed.sort,
        cursor=parsed.cursor,
        is_playlist=False,
    )


def playlist_index_query_from_query_string(query_string: str) -> AlbumListQuery:
    parsed = album_list_query_from_params(parse_qs(query_string))
    return AlbumListQuery(
        search=parsed.search,
        page=parsed.page,
        per_page=parsed.per_page,
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
        property_filter_count,
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
        roots=filters.roots,
        selected_roots=set(query.root_positions),
        selected_genres=selected_genre_values(filters, query),
        checked_genres=checked_genre_values(filters, query),
        selected_styles=selected_style_values(filters, query),
        selected_genre_filter_count=selected_genre_filter_count(filters, query),
        selected_property_count=property_filter_count(query),
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
        clear_url="/",
        filter_action_url="/",
        show_filter_controls=True,
        show_sort_controls=True,
        search_placeholder="Search albums, artists, tracks",
        empty_message="No albums matched these filters.",
        pagination_label="Album pages",
        show_pagination_controls=True,
        sort_options=ALBUM_SORT_OPTIONS,
        default_per_page=DEFAULT_ALBUMS_PER_PAGE,
    )
    context.update(player_page_context("library"))
    return context


def build_playlist_index_context(runtime: PlayerRuntime, query_string: str) -> dict[str, Any]:
    from .player_navigation import (
        ALBUM_SORT_OPTIONS,
        DEFAULT_ALBUMS_PER_PAGE,
        playlist_index_url,
        player_page_context,
    )

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
        clear_url="/playlists",
        filter_action_url="/playlists",
        show_filter_controls=False,
        show_sort_controls=False,
        search_placeholder="Search playlists",
        empty_message="No playlists matched this search.",
        pagination_label="Playlist pages",
        show_pagination_controls=False,
        sort_options=ALBUM_SORT_OPTIONS,
        default_per_page=DEFAULT_ALBUMS_PER_PAGE,
    )
    context.update(player_page_context("playlists"))
    return context


def album_playback_payload(
    database: Path,
    album_id: str,
    query_string: str,
) -> tuple[dict[str, object], ...]:
    from .player_presenters import track_playback_payloads, track_view

    api = LibraryQueries(database)
    query = api.expand_album_list_query(album_index_query_from_query_string(query_string))
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
            {"label": "Albums", "url": "/"},
            {"label": "Artists", "url": "/artists"},
            {"label": "Playlists", "url": "/playlists"},
            {"label": "Queue", "url": "/queue"},
        ),
    )


def build_help_page_context(runtime: PlayerRuntime, options: Any) -> dict[str, Any]:
    from .player_config import player_config_summary
    from .player_navigation import player_page_context

    context = base_player_context(
        runtime,
        view_template="player/help.html",
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
                    if not override.is_current_album
                    else ""
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
    query_string: str,
) -> dict[str, Any]:
    from .player_navigation import (
        album_artist_links,
        album_edit_url,
        album_genre_links,
        album_index_url,
        album_root_links,
        album_style_links,
    )
    from .player_presenters import (
        album_track_meta,
        album_track_sections,
        track_view,
        track_views_with_playlist_options,
    )

    api = LibraryQueries(runtime.database)
    query = api.expand_album_list_query(album_index_query_from_query_string(query_string))
    album = api.get_album(
        album_id,
        root_positions=query.root_positions,
    )
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
        album_root_links=album_root_links(album, roots),
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
    query_string: str,
) -> dict[str, Any]:
    from .player_navigation import (
        album_artist_parts,
        album_root_links,
        album_genre_parts,
        album_style_parts,
        album_url,
    )
    from .player_presenters import (
        album_tag_edit_sections,
        track_view,
    )

    api = LibraryQueries(runtime.database)
    query = api.expand_album_list_query(album_index_query_from_query_string(query_string))
    album = api.get_album(
        album_id,
        root_positions=query.root_positions,
    )
    track_views = [track_view(track) for track in album.tracks]
    musicbrainz_link = album_musicbrainz_link(runtime.database, album.album_id)
    roots = api.library_roots()
    return base_player_context(
        runtime,
        page_name="album-edit",
        view_template="player/album_edit.html",
        album=album,
        query=query,
        tracks=track_views,
        album_tag_edit_sections=album_tag_edit_sections(track_views, roots),
        album_root_links=album_root_links(album, roots),
        album_artist_parts=album_artist_parts(album),
        album_year_text=str(album.year) if album.year else "",
        album_genre_parts=album_genre_parts(album),
        album_style_parts=album_style_parts(album),
        album_back_url=album_url(album, query),
        album_musicbrainz_action_url=f"/api/albums/{quote(album.album_id, safe=':')}/musicbrainz",
        album_tag_edit_action_url=f"/api/albums/{quote(album.album_id, safe=':')}/tags",
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
