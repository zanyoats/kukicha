from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote

from ._compat import UTC
from .display import display_album_title
from .use_case import ALBUM_LIST_SORT_RECENTLY_ADDED, AlbumListQuery, LibraryQueries
from .use_case import (
    album_list_query_from_params,
    album_metadata_link,
    library_search_query_from_params,
)
from .player_common import format_compact_count, format_count_label
from .player_runtime import PlayerRuntime


@dataclass(frozen=True, slots=True)
class BulkMetadataEditRow:
    album_id: str
    album: str
    artist: str
    album_url: str
    art_url: str
    album_label: str
    group_label: str
    group_meta: tuple[str, ...]
    track_ids: tuple[int, ...]
    track_count_text: str
    metadata_url: str
    metadata_mixed: bool = False


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
        size=parsed.size,
        offset=parsed.offset,
        search=parsed.search,
        sort=parsed.sort,
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
        album_bulk_metadata_edit_url,
        album_bulk_star_action_url,
        album_index_url,
        checked_genre_values,
        player_page_context,
        selected_genre_filter_count,
        selected_genre_values,
        selected_style_values,
        recommendation_artist_radio_url,
    )
    from .use_case import DEFAULT_ALBUMS_SIZE

    api = LibraryQueries(runtime.database)
    query = api.expand_album_list_query(album_index_query_from_query_string(query_string))
    filters = runtime.library_filter_options()
    album_page = api.list_album_page(query)
    genre_filter_count = selected_genre_filter_count(filters, query)
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
        selected_genre_filter_count=genre_filter_count,
        selected_genre_filter_count_label=format_compact_count(genre_filter_count),
        previous_url=album_index_url(
            query,
            offset=max(0, album_page.offset - album_page.size),
        )
        if album_page.has_previous
        else "",
        next_url=album_index_url(
            query,
            offset=album_page.offset + album_page.size,
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
        default_size=DEFAULT_ALBUMS_SIZE,
        bulk_metadata_edit_page_url=album_bulk_metadata_edit_url(query),
        bulk_album_star_action_url=album_bulk_star_action_url(query),
        artist_radio_url=(
            recommendation_artist_radio_url(query.artists[0])
            if len(query.artists) == 1
            else ""
        ),
    )
    context.update(player_page_context("library"))
    return context


def build_bulk_metadata_edit_context(
    runtime: PlayerRuntime,
    query_string: str,
) -> dict[str, Any]:
    from .player_navigation import (
        album_bulk_metadata_edit_url,
        album_index_url,
        player_page_context,
    )

    api = LibraryQueries(runtime.database)
    query = api.expand_album_list_query(
        replace(album_index_query_from_query_string(query_string), offset=0)
    )
    albums = api.list_album_summaries(query)
    roots = api.library_roots()
    rows: list[BulkMetadataEditRow] = []
    for album_summary in albums:
        album = api.get_album(album_summary.album_id)
        rows.extend(bulk_metadata_edit_rows_for_album(runtime, album, query, roots))

    context = base_player_context(
        runtime,
        page_name="bulk-metadata-edit",
        page_key="bulk-metadata-edit",
        page_heading="Bulk Metadata URLs",
        view_template="player/bulk_metadata_edit.html",
        query=query,
        rows=tuple(rows),
        row_count_text=format_count_label(len(rows), "row", "rows"),
        album_count_text=format_count_label(len(albums), "album", "albums"),
        album_index_url=album_index_url(query, offset=0),
        bulk_metadata_edit_page_url=album_bulk_metadata_edit_url(query),
        bulk_metadata_edit_action_url="/api/albums/metadata-urls/edit",
    )
    context.update(player_page_context("library"))
    context["page_name"] = "bulk-metadata-edit"
    context["page_key"] = "bulk-metadata-edit"
    context["page_heading"] = "Bulk Metadata URLs"
    return context


def bulk_metadata_edit_rows_for_album(
    runtime: PlayerRuntime,
    album: Any,
    query: AlbumListQuery,
    roots: tuple[Any, ...],
) -> list[BulkMetadataEditRow]:
    from .player_navigation import album_art_url, album_url
    from .player_presenters import album_tag_edit_sections, track_view
    from .use_case.database import connect_existing_database
    from .use_case.metadata import album_metadata_link_for_album_id

    track_views = [track_view(track) for track in album.tracks]
    sections = album_tag_edit_sections(track_views, roots)
    paths = tuple(
        item.track.path
        for section in sections
        for item in section.tracks
        if item.track.path
    )
    with connect_existing_database(runtime.database) as connection:
        from .use_case.metadata import load_album_metadata_track_links

        track_links = load_album_metadata_track_links(connection, paths)
        fallback_url = ""
        if len(sections) == 1:
            fallback_url = metadata_url_for_link(
                album_metadata_link_for_album_id(connection, album.album_id)
            )

    rows: list[BulkMetadataEditRow] = []
    for section in sections:
        link_values = tuple(
            dict.fromkeys(
                (
                    link.provider,
                    link.entity_type,
                    link.entity_id,
                )
                for item in section.tracks
                for link in (track_links.get(item.track.path),)
                if link is not None and link.provider and link.entity_id
            )
        )
        metadata_mixed = len(link_values) > 1
        metadata_url = ""
        if len(link_values) == 1:
            metadata_url = metadata_url_for_entity(*link_values[0])
        elif not metadata_mixed:
            metadata_url = fallback_url

        track_ids = tuple(item.track.track_id for item in section.tracks)
        group_count = len(track_ids)
        track_count_text = format_count_label(group_count, "track", "tracks")
        if group_count != album.track_count:
            track_count_text = f"{track_count_text} of {format_count_label(album.track_count, 'track', 'tracks')}"
        rows.append(
            BulkMetadataEditRow(
                album_id=album.album_id,
                album=album.album,
                artist=album.artist,
                album_url=album_url(album, query),
                art_url=album_art_url(album),
                album_label=f"{album.artist} - {display_album_title(album.album)}",
                group_label=section.label,
                group_meta=section.meta,
                track_ids=track_ids,
                track_count_text=track_count_text,
                metadata_url=metadata_url,
                metadata_mixed=metadata_mixed,
            )
        )
    return rows


def build_home_context(runtime: PlayerRuntime) -> dict[str, Any]:
    from .use_case import home_dashboard
    from .player_navigation import player_page_context

    dashboard = home_dashboard(runtime.database)
    context = base_player_context(
        runtime,
        view_template="player/home.html",
        dashboard=dashboard,
        stat_label=home_stat_label,
        played_label=home_played_label,
        added_label=home_added_label,
        favorited_label=home_favorited_label,
        recently_added_heading=home_recently_added_heading(
            dashboard.recently_added_since
        ),
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
        count_text="Listening history, recent favorites, and new additions.",
        continue_listening=continue_listening,
        home_radio_choices=home_radio_choices(runtime),
        show_history_empty=not dashboard.has_listening_history and continue_listening is None,
    )
    context.update(player_page_context("home"))
    return context


def home_radio_choices(runtime: PlayerRuntime) -> tuple[dict[str, Any], ...]:
    from .player_navigation import (
        HOME_RADIO_RECOMMENDATION_MODES,
        recommendation_genre_radio_url,
        recommendation_random_radio_url,
    )
    from .use_case import UNKNOWN_GENRE_TAG

    genre_choices: list[dict[str, Any]] = []
    seen_genres: set[str] = set()
    filters = runtime.library_filter_options()
    for group in sorted(
        filters.genre_groups,
        key=lambda item: item.genre.casefold(),
    ):
        genre = group.genre.strip()
        genre_key = genre.casefold()
        if (
            not genre
            or genre_key == UNKNOWN_GENRE_TAG.casefold()
            or genre_key in seen_genres
        ):
            continue
        seen_genres.add(genre_key)
        genre_choices.append(
            {
                "label": genre,
                "url": recommendation_genre_radio_url(genre),
                "action_label": f"{genre} Radio",
                "modes": HOME_RADIO_RECOMMENDATION_MODES,
            }
        )
    genre_choices.append(
        {
            "label": "Random",
            "url": recommendation_random_radio_url(),
            "action_label": "Random Playlist",
            "modes": HOME_RADIO_RECOMMENDATION_MODES,
        }
    )
    return tuple(genre_choices)


def build_search_context(runtime: PlayerRuntime, query_string: str) -> dict[str, Any]:
    from .player_navigation import album_index_url, player_page_context, search_url
    from .player_presenters import (
        track_view,
        track_views_with_playlist_options,
    )

    query = library_search_query_from_params(parse_qs(query_string, keep_blank_values=True))
    results = LibraryQueries(runtime.database).search(query)
    track_views = track_views_with_playlist_options(
        runtime.database,
        (track_view(track) for track in results.songs.items),
    )
    context = base_player_context(
        runtime,
        view_template="player/search.html",
        search_results=results,
        query=query,
        artist_previous_url=(
            search_url(
                query,
                artist_offset=max(0, query.artist_offset - query.artist_count),
            )
            if results.artists.has_previous and query.artist_count
            else ""
        ),
        artist_next_url=(
            search_url(query, artist_offset=query.artist_offset + query.artist_count)
            if results.artists.has_next and query.artist_count
            else ""
        ),
        album_previous_url=(
            search_url(
                query,
                album_offset=max(0, query.album_offset - query.album_count),
            )
            if results.albums.has_previous and query.album_count
            else ""
        ),
        album_next_url=(
            search_url(query, album_offset=query.album_offset + query.album_count)
            if results.albums.has_next and query.album_count
            else ""
        ),
        song_previous_url=(
            search_url(
                query,
                song_offset=max(0, query.song_offset - query.song_count),
            )
            if results.songs.has_previous and query.song_count
            else ""
        ),
        song_next_url=(
            search_url(query, song_offset=query.song_offset + query.song_count)
            if results.songs.has_next and query.song_count
            else ""
        ),
        artist_results=tuple(
            {
                "artist": artist,
                "url": album_index_url(AlbumListQuery(artists=(artist.artist,))),
            }
            for artist in results.artists.items
        ),
        track_views=track_views,
        count_text=search_count_text(results),
        search_back_url="/",
    )
    context.update(player_page_context("search"))
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
        show_playlist_create_controls=True,
        playlist_create_action_url="/api/playlists",
        playlist_import_action_url="/api/playlists/import",
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
    parts = [format_count_label(play_count, "play", "plays")]
    date_text = timestamp[:10] if timestamp else ""
    if date_text:
        parts.append(date_text)
    return " - ".join(parts)


def home_played_label(timestamp: str) -> str:
    return f"Played {timestamp[:10]}" if timestamp else "Played recently"


def home_added_label(timestamp: str | None) -> str:
    return f"Added {timestamp[:10]}" if timestamp else "Recently Added"


def home_favorited_label(timestamp: str | None) -> str:
    return f"Favorited {timestamp[:10]}" if timestamp else "Recently Favorited"


def home_recently_added_heading(since: str) -> str:
    if since:
        return f"Most Recently Added Since {since[:10]}"
    return "Added in the Last Month"


def search_count_text(results: Any) -> str:
    return " - ".join(
        (
            format_count_label(len(results.artists.items), "artist", "artists"),
            format_count_label(len(results.albums.items), "album", "albums"),
            format_count_label(len(results.songs.items), "track", "tracks"),
        )
    )


def build_help_page_context(
    runtime: PlayerRuntime,
    options: Any,
    *,
    browser_cookie: str | None = None,
    user_agent: str = "",
    client_ip: str = "",
) -> dict[str, Any]:
    from .app_metadata import kukicha_version
    from .player_config import player_config_summary
    from .player_navigation import player_page_context
    from .use_case import opensubsonic_clients

    opensubsonic_configured = getattr(options, "opensubsonic", None) is not None
    context = base_player_context(
        runtime,
        view_template="player/help.html",
        app_version=kukicha_version(),
        browser_login=browser_login_help_context(
            options,
            browser_cookie=browser_cookie,
            user_agent=user_agent,
            client_ip=client_ip,
        ),
        opensubsonic_configured=opensubsonic_configured,
        opensubsonic_clients=(
            opensubsonic_clients(runtime.database) if opensubsonic_configured else ()
        ),
        config_summary=player_config_summary(options=options),
    )
    context.update(player_page_context("help"))
    return context


def browser_login_help_context(
    options: Any,
    *,
    browser_cookie: str | None,
    user_agent: str,
    client_ip: str,
) -> dict[str, object]:
    from .player_auth import auth_cookie_details

    auth = getattr(options, "auth", None)
    if auth is None:
        return {
            "configured": False,
            "active": False,
            "status": "Not configured",
        }

    details = auth_cookie_details(auth, browser_cookie)
    if details is None:
        return {
            "configured": True,
            "active": False,
            "status": "Inactive",
            "user_agent": display_user_agent(user_agent),
            "client_ip": display_client_ip(client_ip),
        }
    return {
        "configured": True,
        "active": True,
        "status": "Active",
        "username": details.username,
        "user_agent": display_user_agent(user_agent),
        "client_ip": display_client_ip(client_ip),
        "expires_at": help_timestamp(details.expires_at),
        "expires_in": format_time_remaining(details.seconds_remaining),
    }


def display_user_agent(value: str) -> str:
    return value.strip() or "<unset>"


def display_client_ip(value: str) -> str:
    return value.strip() or "<unknown>"


def help_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat()


def format_time_remaining(seconds: int) -> str:
    if seconds <= 0:
        return "expired"
    if seconds >= 24 * 60 * 60:
        days = rounded_up(seconds, 24 * 60 * 60)
        return format_count_label(days, "day", "days")
    if seconds >= 60 * 60:
        hours = rounded_up(seconds, 60 * 60)
        return format_count_label(hours, "hour", "hours")
    if seconds >= 60:
        minutes = rounded_up(seconds, 60)
        return format_count_label(minutes, "minute", "minutes")
    return "less than 1 minute"


def rounded_up(value: int, divisor: int) -> int:
    return (value + divisor - 1) // divisor


def build_artists_page_context(runtime: PlayerRuntime) -> dict[str, Any]:
    from .player_navigation import artist_cloud_links, player_page_context

    stats = LibraryQueries(runtime.database).library_stats()
    artists = artist_cloud_links(stats.album_artists)
    context = base_player_context(
        runtime,
        view_template="player/artists.html",
        artists=artists,
        count_text=format_count_label(len(artists), "artist", "artists"),
    )
    context.update(player_page_context("artists"))
    return context


def build_roots_page_context(runtime: PlayerRuntime) -> dict[str, Any]:
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
        count_text=format_count_label(len(roots), "root", "roots"),
    )
    context.update(player_page_context("roots"))
    return context


def build_artist_split_rules_page_context(runtime: PlayerRuntime) -> dict[str, Any]:
    from .player_navigation import player_page_context

    album_artist_mappings = LibraryQueries(runtime.database).album_artist_split_mappings()
    context = base_player_context(
        runtime,
        view_template="player/artist_split_rules.html",
        album_artist_mappings=album_artist_mappings,
        count_text=format_count_label(
            len(album_artist_mappings),
            "mapping",
            "mappings",
        ),
    )
    context.update(player_page_context("artist-split-rules"))
    return context


def build_metadata_overrides_page_context(runtime: PlayerRuntime) -> dict[str, Any]:
    from .player_navigation import player_page_context

    overrides = LibraryQueries(runtime.database).album_metadata_overrides()
    context = base_player_context(
        runtime,
        view_template="player/metadata_overrides.html",
        metadata_overrides=tuple(
            {
                "album_id": override.album_id,
                "album": override.album,
                "artist": override.artist,
                "year": override.year,
                "provider": override.provider,
                "provider_label": metadata_provider_label(override.provider),
                "entity_type": override.entity_type,
                "entity_label": metadata_entity_label(override.entity_type),
                "entity_id": override.entity_id,
                "entity_url": metadata_url_for_entity(
                    override.provider,
                    override.entity_type,
                    override.entity_id,
                ),
                "related_entity_type": override.related_entity_type,
                "related_entity_label": metadata_entity_label(
                    override.related_entity_type
                ),
                "related_entity_id": override.related_entity_id,
                "related_entity_url": metadata_url_for_entity(
                    override.provider,
                    override.related_entity_type,
                    override.related_entity_id,
                ),
                "is_current_album": override.is_current_album,
                "album_url": f"/albums/{quote(override.album_id, safe=':')}"
                if override.is_current_album
                else "",
                "album_edit_url": f"/albums/{quote(override.album_id, safe=':')}/edit"
                if override.is_current_album
                else "",
                "delete_url": (
                    f"/api/metadata-overrides/{quote(override.album_id, safe=':')}/delete"
                ),
            }
            for override in overrides
        ),
        count_text=format_count_label(len(overrides), "override", "overrides"),
    )
    context.update(player_page_context("metadata-overrides"))
    return context


def build_musicbrainz_overrides_page_context(runtime: PlayerRuntime) -> dict[str, Any]:
    return build_metadata_overrides_page_context(runtime)


def build_cache_page_context(runtime: PlayerRuntime) -> dict[str, Any]:
    from .player_navigation import player_page_context

    cache_stats = LibraryQueries(runtime.database).cache_stats()
    total_entries = sum(stat.count for stat in cache_stats)
    context = base_player_context(
        runtime,
        view_template="player/cache.html",
        cache_sections=cache_sections_context(cache_stats),
        count_text=format_count_label(total_entries, "entry", "entries"),
    )
    context.update(player_page_context("cache"))
    return context


def build_listening_data_page_context(runtime: PlayerRuntime) -> dict[str, Any]:
    from .player_navigation import player_page_context
    from .use_case import listening_data_stats

    stats = listening_data_stats(runtime.database)
    total_entries = sum(stat.count for stat in stats)
    context = base_player_context(
        runtime,
        view_template="player/listening_data.html",
        listening_data_sections=listening_data_sections_context(stats),
        reset_url="/api/listening-data/reset",
        count_text=format_count_label(total_entries, "entry", "entries"),
    )
    context.update(player_page_context("listening-data"))
    return context


def cache_sections_context(cache_stats: tuple[Any, ...]) -> tuple[dict[str, Any], ...]:
    sections: list[dict[str, Any]] = []
    for stat in cache_stats:
        if not sections or sections[-1]["label"] != stat.section:
            sections.append({"label": stat.section, "stats": []})
        sections[-1]["stats"].append(
            {
                "key": stat.key,
                "label": stat.label,
                "count": stat.count,
                "clear_url": f"/api/cache/{quote(stat.key, safe='')}/clear",
            }
        )
    return tuple(
        {
            "label": section["label"],
            "stats": tuple(section["stats"]),
        }
        for section in sections
    )


def listening_data_sections_context(stats: tuple[Any, ...]) -> tuple[dict[str, Any], ...]:
    sections: list[dict[str, Any]] = []
    for stat in stats:
        if not sections or sections[-1]["label"] != stat.section:
            sections.append({"label": stat.section, "stats": []})
        sections[-1]["stats"].append(
            {
                "label": stat.label,
                "count": stat.count,
            }
        )
    return tuple(
        {
            "label": section["label"],
            "stats": tuple(section["stats"]),
        }
        for section in sections
    )


def build_jobs_page_context(runtime: PlayerRuntime) -> dict[str, Any]:
    from .player_jobs import group_job_payloads_by_day
    from .player_navigation import player_page_context

    jobs = runtime.job_payloads()
    job_groups = group_job_payloads_by_day(jobs)
    context = base_player_context(
        runtime,
        view_template="player/jobs.html",
        job_groups=job_groups,
        count_text=format_count_label(len(jobs), "job", "jobs"),
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
        album_style_links,
    )
    from .player_presenters import (
        album_track_meta,
        album_track_sections,
        track_view,
        track_views_with_artist_display_lines,
        track_views_with_playlist_options,
    )

    api = LibraryQueries(runtime.database)
    query = AlbumListQuery()
    album = api.get_album(album_id)
    track_views = track_views_with_playlist_options(
        runtime.database,
        [track_view(track) for track in album.tracks],
    )
    track_views = track_views_with_artist_display_lines(
        track_views,
        split_patterns=runtime.album_artist_split_patterns,
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
        selected_track_id=selected_track_id_from_query_string(query_string),
    )


def selected_track_id_from_query_string(query_string: str) -> int | None:
    values = parse_qs(query_string, keep_blank_values=True).get("selectedTrackId", ())
    if not values:
        return None
    try:
        track_id = int(values[0])
    except (TypeError, ValueError):
        return None
    return track_id if track_id > 0 else None


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
        playlist_track_meta=playlist_track_meta(playlist, track_views),
        playlist_cover_url=playlist_cover_url(
            playlist.cover_svg,
            playlist.name,
            playlist_id=playlist.playlist_id,
            cover_mime_type=playlist.cover_mime_type,
        ),
        playlist_edit_page_url=f"/playlists/{playlist.playlist_id}/edit",
    )


def build_playlist_edit_context(
    runtime: PlayerRuntime,
    playlist_id: int,
    _query_string: str,
) -> dict[str, Any]:
    from .player_navigation import (
        playlist_cover_url,
        playlist_index_url,
    )

    api = LibraryQueries(runtime.database)
    query = playlist_index_query_from_query_string(_query_string)
    playlist = api.get_playlist(playlist_id)
    return base_player_context(
        runtime,
        page_name="playlist-edit",
        view_template="player/playlist_edit.html",
        playlist=playlist,
        playlist_back_url=f"/playlists/{playlist.playlist_id}",
        playlist_index_url=playlist_index_url(query),
        playlist_cover_url=playlist_cover_url(
            playlist.cover_svg,
            playlist.name,
            playlist_id=playlist.playlist_id,
            cover_mime_type=playlist.cover_mime_type,
        ),
        playlist_cover_upload_action_url=f"/api/playlists/{playlist.playlist_id}/cover",
        playlist_delete_action_url=f"/api/playlists/{playlist.playlist_id}/delete",
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
        track_view,
    )
    from .use_case.commands.album_covers import album_cover_upload_enabled_for_metadata

    api = LibraryQueries(runtime.database)
    query = AlbumListQuery()
    album = api.get_album(album_id)
    album_artist_part_values = album_artist_parts(album)
    track_views = [track_view(track) for track in album.tracks]
    metadata_link = album_metadata_link(runtime.database, album.album_id)
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
        album_artist_parts=album_artist_part_values,
        album_year_text=str(album.year) if album.year else "",
        album_genre_parts=album_genre_parts(album),
        album_style_parts=album_style_parts(album),
        album_back_url=album_url(album, query),
        album_edit_action_url=f"/api/albums/{quote(album.album_id, safe=':')}/edit",
        album_cover_upload_action_url=f"/api/albums/{quote(album.album_id, safe=':')}/cover",
        album_cover_upload_enabled=(
            len(musicbrainz_sections) <= 1
            and album_cover_upload_enabled_for_metadata(
                album.album,
                album_artist_part_values,
            )
        ),
        album_delete_action_url=f"/api/albums/{quote(album.album_id, safe=':')}/delete",
        album_metadata_url=metadata_url_for_link(metadata_link),
    )


def album_musicbrainz_edit_sections(
    database: Path,
    album_id: str,
    track_views: list[Any],
    roots: tuple[Any, ...],
) -> list[Any]:
    from .player_presenters import album_tag_edit_sections
    from .use_case.database import connect_existing_database
    from .use_case.metadata import load_album_metadata_track_links

    sections = album_tag_edit_sections(track_views, roots)
    paths = tuple(
        item.track.path
        for section in sections
        for item in section.tracks
        if item.track.path
    )
    with connect_existing_database(database) as connection:
        track_links = load_album_metadata_track_links(connection, paths)

    fallback_url = ""
    if len(sections) == 1:
        fallback_url = metadata_url_for_link(album_metadata_link(database, album_id))

    resolved_sections = []
    for section in sections:
        link_values = tuple(
            dict.fromkeys(
                (
                    link.provider,
                    link.entity_type,
                    link.entity_id,
                )
                for item in section.tracks
                for link in (track_links.get(item.track.path),)
                if link is not None and link.provider and link.entity_id
            )
        )
        section_url = (
            metadata_url_for_entity(*link_values[0])
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
    return metadata_url_for_link(link)


def musicbrainz_url_for_link(
    release_mbid: str | None,
    release_group_mbid: str | None,
) -> str:
    if release_mbid:
        return f"https://musicbrainz.org/release/{release_mbid}"
    if release_group_mbid:
        return f"https://musicbrainz.org/release-group/{release_group_mbid}"
    return ""


def metadata_url_for_link(link: Any) -> str:
    if link is None:
        return ""
    return metadata_url_for_entity(
        getattr(link, "provider", None),
        getattr(link, "entity_type", None),
        getattr(link, "entity_id", None),
    )


def metadata_url_for_entity(
    provider: object,
    entity_type: object,
    entity_id: object,
) -> str:
    provider_text = str(provider or "")
    entity_type_text = str(entity_type or "")
    entity_id_text = str(entity_id or "")
    if not provider_text or not entity_type_text or not entity_id_text:
        return ""
    if provider_text == "musicbrainz":
        if entity_type_text == "release":
            return f"https://musicbrainz.org/release/{entity_id_text}"
        if entity_type_text == "release-group":
            return f"https://musicbrainz.org/release-group/{entity_id_text}"
    if provider_text == "discogs":
        if entity_type_text == "release":
            return f"https://www.discogs.com/release/{entity_id_text}"
        if entity_type_text == "master":
            return f"https://www.discogs.com/master/{entity_id_text}"
    return ""


def metadata_provider_label(provider: object) -> str:
    if provider == "musicbrainz":
        return "MusicBrainz"
    if provider == "discogs":
        return "Discogs"
    return str(provider or "")


def metadata_entity_label(entity_type: object) -> str:
    if entity_type == "release-group":
        return "Release group"
    if entity_type == "release":
        return "Release"
    if entity_type == "master":
        return "Master"
    return str(entity_type or "")


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
