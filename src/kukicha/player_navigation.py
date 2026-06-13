from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from math import log1p
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from .use_case import (
    ALBUM_LIST_SORT_ALBUMS,
    ALBUM_LIST_SORT_ARTIST,
    ALBUM_LIST_SORT_FREQUENT,
    ALBUM_LIST_SORT_GENRE,
    ALBUM_LIST_SORT_RECENT,
    ALBUM_LIST_SORT_RECENTLY_ADDED,
    ALBUM_LIST_SORT_STARRED,
    RECOMMENDATION_MODE_ARTIST_ONLY,
    RECOMMENDATION_MODE_DEFAULT,
    RECOMMENDATION_MODE_DISCOVERY,
    SUPPORTED_RECOMMENDATION_MODES,
    AlbumDetails,
    AlbumListQuery,
    AlbumSummary,
    GenreFilterGroup,
    GenreStyleFilter,
    LibraryAlbumArtistStats,
    LibraryFilterOptions,
    LibrarySearchQuery,
)
from .use_case import album_query_params, library_search_query_params
from .models import ALBUM_ARTWORK_HEIGHT
from .player_common import format_count_label
from .playlist_art import playlist_cover_data_url, playlist_cover_svg

PLAYLIST_COVER_SVG = playlist_cover_svg("Playlist")
PLAYLIST_COVER_DATA_URL = playlist_cover_data_url(PLAYLIST_COVER_SVG)
ALBUM_SORT_OPTIONS = (
    (ALBUM_LIST_SORT_ARTIST, "Artist"),
    (ALBUM_LIST_SORT_ALBUMS, "Albums"),
    (ALBUM_LIST_SORT_RECENT, "Recent"),
    (ALBUM_LIST_SORT_FREQUENT, "Frequent"),
    (ALBUM_LIST_SORT_RECENTLY_ADDED, "Recently Added"),
    (ALBUM_LIST_SORT_GENRE, "Genre"),
    (ALBUM_LIST_SORT_STARRED, "Starred"),
)
RECOMMENDATION_MODE_LABELS = {
    RECOMMENDATION_MODE_DEFAULT: "Default",
    RECOMMENDATION_MODE_DISCOVERY: "Discovery",
    RECOMMENDATION_MODE_ARTIST_ONLY: "Artist-Only",
}
HOME_RADIO_RECOMMENDATION_MODES = (
    RECOMMENDATION_MODE_DEFAULT,
    RECOMMENDATION_MODE_DISCOVERY,
)
PLAYER_PAGE_LINKS = (
    ("home", "Home", "/"),
    ("search", "Search", "/search"),
    ("library", "Albums", "/albums"),
    ("artists", "Artists", "/artists"),
    ("playlists", "Playlists", "/playlists"),
    ("roots", "Roots", "/roots"),
    ("artist-split-rules", "Artists Split Rules", "/artist-split-rules"),
    ("metadata-overrides", "Metadata Overrides", "/metadata-overrides"),
    ("listening-data", "Listening Data", "/listening-data"),
    ("cache", "Cache", "/cache"),
    ("jobs", "Jobs", "/jobs"),
    ("help", "Help", "/help"),
)
PLAYER_PAGE_BY_KEY = {key: {"title": title, "url": url} for key, title, url in PLAYER_PAGE_LINKS}
PLAYER_PAGE_ROUTE_KEYS = {url: key for key, _title, url in PLAYER_PAGE_LINKS[2:]}
_URL_OFFSET_UNSET = object()

@dataclass(frozen=True, slots=True)
class PlayerPageLink:
    kind: str
    key: str = ""
    title: str = ""
    url: str = ""
    current: bool = False


PLAYER_PAGE_MENU_ITEMS = (
    PlayerPageLink(kind="heading", title="LIBRARY"),
    PlayerPageLink(kind="link", key="home", title="Home", url="/"),
    PlayerPageLink(kind="link", key="library", title="Albums", url="/albums"),
    PlayerPageLink(kind="link", key="artists", title="Artists", url="/artists"),
    PlayerPageLink(kind="link", key="playlists", title="Playlists", url="/playlists"),
    PlayerPageLink(kind="divider"),
    PlayerPageLink(kind="heading", title="SETTINGS"),
    PlayerPageLink(kind="link", key="roots", title="Roots", url="/roots"),
    PlayerPageLink(
        kind="link",
        key="artist-split-rules",
        title="Artists Split Rules",
        url="/artist-split-rules",
    ),
    PlayerPageLink(
        kind="link",
        key="metadata-overrides",
        title="Metadata Overrides",
        url="/metadata-overrides",
    ),
    PlayerPageLink(
        kind="link",
        key="listening-data",
        title="Listening Data",
        url="/listening-data",
    ),
    PlayerPageLink(kind="link", key="cache", title="Cache", url="/cache"),
    PlayerPageLink(kind="divider"),
    PlayerPageLink(kind="link", key="jobs", title="Jobs", url="/jobs"),
    PlayerPageLink(kind="action", key="keyboard-shortcuts", title="Keyboard Shortcuts"),
    PlayerPageLink(kind="link", key="help", title="Help", url="/help"),
)


@dataclass(frozen=True, slots=True)
class MetaLink:
    label: str
    url: str

@dataclass(frozen=True, slots=True)
class ArtistCloudLink:
    label: str
    url: str
    font_size_rem: float
    title: str

def player_page_heading(page_key: str) -> str:
    try:
        return str(PLAYER_PAGE_BY_KEY[page_key]["title"])
    except KeyError as error:
        raise ValueError(f"unknown player page: {page_key}") from error

def player_page_menu_items(current_page: str) -> tuple[PlayerPageLink, ...]:
    player_page_heading(current_page)
    return tuple(
        PlayerPageLink(
            kind=item.kind,
            key=item.key,
            title=item.title,
            url=item.url,
            current=item.key == current_page,
        )
        for item in PLAYER_PAGE_MENU_ITEMS
    )

def player_page_context(page_key: str) -> dict[str, Any]:
    return {
        "page_name": page_key,
        "page_key": page_key,
        "page_heading": player_page_heading(page_key),
        "page_menu_items": player_page_menu_items(page_key),
    }

def album_index_url(
    query: AlbumListQuery,
    *,
    offset: int | None | object = _URL_OFFSET_UNSET,
) -> str:
    if offset is _URL_OFFSET_UNSET:
        params = album_query_params(query)
    else:
        params = album_query_params(query, offset=offset)
    encoded = urlencode(params, doseq=True, safe="[]")
    return f"/albums?{encoded}" if encoded else "/albums"


def album_bulk_metadata_edit_url(query: AlbumListQuery) -> str:
    bulk_query = AlbumListQuery(
        artists=query.artists,
        album=query.album,
        genres=query.genres,
        styles=query.styles,
        genre_filters=query.genre_filters,
        search=query.search,
        sort=query.sort,
        is_playlist=False,
    )
    encoded = urlencode(album_query_params(bulk_query), doseq=True, safe="[]")
    return f"/albums/metadata-urls/edit?{encoded}" if encoded else "/albums/metadata-urls/edit"


def album_bulk_star_action_url(query: AlbumListQuery) -> str:
    bulk_query = AlbumListQuery(
        artists=query.artists,
        album=query.album,
        genres=query.genres,
        styles=query.styles,
        genre_filters=query.genre_filters,
        search=query.search,
        sort=query.sort,
        is_playlist=False,
    )
    encoded = urlencode(album_query_params(bulk_query), doseq=True, safe="[]")
    return f"/api/albums/star?{encoded}" if encoded else "/api/albums/star"


def recommendation_mode_label(mode: str) -> str:
    return RECOMMENDATION_MODE_LABELS.get(mode, mode.replace("_", " ").title())


def recommendation_track_radio_url(
    track: Any,
    *,
    mode: str | None = None,
) -> str:
    track_id = recommendation_track_id(track)
    if track_id is None:
        return ""
    return recommendation_url(
        f"/recommendations/radio/track/{track_id}",
        mode=mode,
    )


def recommendation_album_radio_url(
    album: Any,
    *,
    mode: str | None = None,
) -> str:
    if bool(getattr(album, "is_playlist", False)):
        return ""
    album_id = str(getattr(album, "album_id", album)).strip()
    if not album_id:
        return ""
    return recommendation_url(
        f"/recommendations/radio/album/{quote(album_id, safe=':')}",
        mode=mode,
    )


def recommendation_artist_radio_url(
    artist: str,
    *,
    mode: str | None = None,
) -> str:
    artist_value = str(artist).strip()
    if not artist_value:
        return ""
    return recommendation_url(
        f"/recommendations/radio/artist/{quote(artist_value, safe='')}",
        mode=mode,
    )


def recommendation_genre_radio_url(
    genre: str,
    *,
    mode: str | None = None,
) -> str:
    genre_value = str(genre).strip()
    if not genre_value:
        return ""
    return recommendation_url(
        f"/recommendations/radio/genre/{quote(genre_value, safe='')}",
        mode=mode,
    )


def recommendation_random_radio_url(
    *,
    mode: str | None = None,
) -> str:
    return recommendation_url("/recommendations/radio/random", mode=mode)


def recommendation_url(
    path: str,
    *,
    mode: str | None = None,
    date: str | None = None,
) -> str:
    params: dict[str, str] = {}
    if mode and mode != RECOMMENDATION_MODE_DEFAULT:
        params["mode"] = mode
    if date:
        params["date"] = date
    encoded = urlencode(params)
    return f"{path}?{encoded}" if encoded else path


def recommendation_mode_menu_items(
    base_url: str,
    modes: Iterable[str] | None = None,
) -> tuple[dict[str, str], ...]:
    if not base_url:
        return ()
    mode_values = (
        tuple(modes)
        if modes is not None
        else SUPPORTED_RECOMMENDATION_MODES
    )
    return tuple(
        {
            "mode": mode,
            "label": recommendation_mode_label(mode),
            "url": recommendation_mode_url(base_url, mode),
        }
        for mode in mode_values
    )


def recommendation_mode_url(base_url: str, mode: str) -> str:
    split = urlsplit(base_url)
    params = [
        (key, value)
        for key, value in parse_qsl(split.query, keep_blank_values=True)
        if key != "mode"
    ]
    if mode != RECOMMENDATION_MODE_DEFAULT:
        params.append(("mode", mode))
    return urlunsplit(
        (
            split.scheme,
            split.netloc,
            split.path,
            urlencode(params),
            split.fragment,
        )
    )


def recommendation_track_id(track: Any) -> int | None:
    track_id = getattr(track, "library_track_id", None)
    if track_id is None:
        track_id = getattr(track, "track_id", track)
    try:
        resolved_id = int(track_id)
    except (TypeError, ValueError):
        return None
    return resolved_id if resolved_id > 0 else None


def playlist_index_url(_query: AlbumListQuery, *, offset: int | None = None) -> str:
    return "/playlists"


def search_url(
    query: LibrarySearchQuery,
    *,
    artist_offset: int | None | object = _URL_OFFSET_UNSET,
    album_offset: int | None | object = _URL_OFFSET_UNSET,
    song_offset: int | None | object = _URL_OFFSET_UNSET,
) -> str:
    offset_overrides = {}
    if artist_offset is not _URL_OFFSET_UNSET:
        offset_overrides["artist_offset"] = artist_offset
    if album_offset is not _URL_OFFSET_UNSET:
        offset_overrides["album_offset"] = album_offset
    if song_offset is not _URL_OFFSET_UNSET:
        offset_overrides["song_offset"] = song_offset
    encoded = urlencode(
        library_search_query_params(
            query,
            **offset_overrides,
        ),
        doseq=True,
    )
    return f"/search?{encoded}"


def artist_cloud_links(
    stats: Iterable[LibraryAlbumArtistStats],
) -> tuple[ArtistCloudLink, ...]:
    rows = tuple(
        stat
        for stat in stats
        if stat.album_artist.strip()
    )
    if not rows:
        return ()

    scores = tuple(artist_cloud_score(stat) for stat in rows)
    log_scores = tuple(log1p(score) for score in scores)
    min_score = min(log_scores)
    max_score = max(log_scores)
    return tuple(
        ArtistCloudLink(
            label=stat.album_artist,
            url=album_index_url(AlbumListQuery(artists=(stat.album_artist,))),
            font_size_rem=artist_cloud_font_size(log_score, min_score, max_score),
            title=artist_cloud_title(stat),
        )
        for stat, log_score in zip(rows, log_scores)
    )

def artist_cloud_score(stat: LibraryAlbumArtistStats) -> int:
    return stat.albums_scanned * 12 + stat.tracks_scanned

def artist_cloud_font_size(log_score: float, min_score: float, max_score: float) -> float:
    min_size = 0.95
    max_size = 2.20
    if max_score == min_score:
        normalized = 0.5
    else:
        normalized = (log_score - min_score) / (max_score - min_score)
    return round(min_size + normalized * (max_size - min_size), 2)

def artist_cloud_title(stat: LibraryAlbumArtistStats) -> str:
    return (
        f"{format_count_label(stat.albums_scanned, 'album', 'albums')} - "
        f"{format_count_label(stat.tracks_scanned, 'track', 'tracks')}"
    )

def album_url(album: AlbumSummary, query: AlbumListQuery | None = None) -> str:
    if album.is_playlist and album.playlist_id is not None:
        path = f"/playlists/{album.playlist_id}"
        if query is None:
            return path
        encoded = urlencode(album_query_params(query), doseq=True, safe="[]")
        return f"{path}?{encoded}" if encoded else path
    path = f"/albums/{quote(album.album_id, safe=':')}"
    if query is None:
        return path
    encoded = urlencode(album_query_params(query), doseq=True, safe="[]")
    return f"{path}?{encoded}" if encoded else path

def album_edit_url(album: AlbumSummary, query: AlbumListQuery | None = None) -> str:
    path = f"/albums/{quote(album.album_id, safe=':')}/edit"
    if query is None:
        return path
    encoded = urlencode(album_query_params(query), doseq=True, safe="[]")
    return f"{path}?{encoded}" if encoded else path

def album_art_url(album: AlbumSummary | AlbumDetails) -> str:
    if album.is_playlist:
        return playlist_cover_url(
            album.cover_svg,
            album.album,
            playlist_id=album.playlist_id,
            cover_mime_type=album.cover_mime_type,
        )
    return f"/art/{ALBUM_ARTWORK_HEIGHT}/{album.art_track_id}" if album.art_track_id else ""

def playlist_cover_url(
    cover_svg: str,
    playlist_name: str,
    *,
    playlist_id: int | None = None,
    cover_mime_type: str = "",
) -> str:
    if playlist_id is not None and cover_mime_type:
        return f"/api/playlists/{playlist_id}/cover"
    return playlist_cover_data_url(cover_svg or playlist_cover_svg(playlist_name))

def album_summary_text(album: AlbumSummary) -> str:
    parts: list[str] = []
    if album.year:
        parts.append(str(album.year))
    genres = getattr(album, "genres", ())
    styles = getattr(album, "styles", ())
    if genres:
        parts.append(f"Genres: {', '.join(genres)}")
    if styles:
        parts.append(f"Styles: {', '.join(styles)}")
    parts.append(format_count_label(album.track_count, "track", "tracks"))
    return " - ".join(parts)

def album_artist_parts(album: AlbumDetails) -> tuple[str, ...]:
    if album.album_artists:
        return album.album_artists
    return (album.artist,) if album.artist else ()

def album_meta_query(
    query: AlbumListQuery,
    *,
    artists: tuple[str, ...] = (),
    genre_filters: tuple[GenreStyleFilter, ...] = (),
) -> AlbumListQuery:
    return AlbumListQuery(
        artists=artists or query.artists,
        album=query.album,
        genre_filters=genre_filters or query.genre_filters,
        is_playlist=query.is_playlist,
        size=query.size,
        search=query.search,
        sort=query.sort,
    )

def album_artist_url(album: AlbumSummary, query: AlbumListQuery | None = None) -> str:
    if album.is_playlist:
        return ""
    artists = unique_meta_values(album.album_artists)
    if not artists:
        return ""
    return album_index_url(
        album_meta_query(query or AlbumListQuery(), artists=artists)
    )

def unique_meta_values(values: Iterable[str]) -> tuple[str, ...]:
    items: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        items.append(text)
    return tuple(items)

def album_artist_links(album: AlbumSummary, query: AlbumListQuery) -> tuple[MetaLink, ...]:
    if album.is_playlist:
        return ()
    return tuple(
        MetaLink(
            label=artist,
            url=album_index_url(album_meta_query(query, artists=(artist,))),
        )
        for artist in unique_meta_values(album.album_artists or (album.artist,))
    )

def album_genre_links(
    album: AlbumDetails,
    query: AlbumListQuery,
    filters: LibraryFilterOptions,
) -> tuple[MetaLink, ...]:
    items: list[MetaLink] = []
    for genre in unique_meta_values(album.genres):
        group = genre_filter_group(filters, genre)
        url = ""
        if group is not None:
            url = album_index_url(
                album_meta_query(
                    query,
                    genre_filters=(
                        GenreStyleFilter(genre=group.genre),
                    ),
                )
            )
        items.append(MetaLink(label=genre, url=url))
    return tuple(items)

def album_style_links(
    album: AlbumDetails,
    query: AlbumListQuery,
    filters: LibraryFilterOptions,
) -> tuple[MetaLink, ...]:
    items: list[MetaLink] = []
    for style in unique_meta_values(album.styles):
        group = genre_filter_group_for_style(filters, style)
        url = ""
        if group is not None:
            url = album_index_url(
                album_meta_query(
                    query,
                    genre_filters=(
                        GenreStyleFilter(genre=group.genre, styles=(style,)),
                    ),
                )
            )
        items.append(MetaLink(label=style, url=url))
    return tuple(items)

def genre_filter_group(
    filters: LibraryFilterOptions,
    genre: str,
) -> GenreFilterGroup | None:
    genre_key = filter_match_key(genre)
    for group in filters.genre_groups:
        if filter_match_key(group.genre) == genre_key:
            return group
    return None

def genre_filter_group_for_style(
    filters: LibraryFilterOptions,
    style: str,
) -> GenreFilterGroup | None:
    style_key = filter_match_key(style)
    for group in filters.genre_groups:
        if any(filter_match_key(group_style) == style_key for group_style in group.styles):
            return group
    return None

def filter_match_key(value: str) -> str:
    return " ".join(value.casefold().strip().split())

def album_genre_parts(album: AlbumDetails) -> tuple[str, ...]:
    parts: list[str] = []
    if album.genres:
        parts.append(", ".join(album.genres))
    return tuple(parts)

def album_style_parts(album: AlbumDetails) -> tuple[str, ...]:
    parts: list[str] = []
    if album.styles:
        parts.append(", ".join(album.styles))
    return tuple(parts)

def selected_genre_values(
    filters: LibraryFilterOptions,
    query: AlbumListQuery,
) -> set[str]:
    selected_keys = {
        *(filter_match_key(value) for value in query.genres),
        *(filter_match_key(filter_item.genre) for filter_item in query.genre_filters),
    }
    values = {
        group.genre
        for group in filters.genre_groups
        if filter_match_key(group.genre) in selected_keys
    }
    values.update(query.genres)
    values.update(filter_item.genre for filter_item in query.genre_filters)
    return values

def selected_style_values(
    filters: LibraryFilterOptions,
    query: AlbumListQuery,
) -> set[str]:
    selected_keys = {
        *(filter_match_key(value) for value in query.styles),
        *(
            filter_match_key(style)
            for filter_item in query.genre_filters
            for style in filter_item.styles
        ),
    }
    whole_genre_keys = {
        filter_match_key(filter_item.genre)
        for filter_item in query.genre_filters
        if not filter_item.styles
    }
    values = {
        style
        for group in filters.genre_groups
        for style in group.styles
        if (
            filter_match_key(group.genre) in whole_genre_keys
            or filter_match_key(style) in selected_keys
        )
    }
    values.update(
        style
        for style in filters.loose_styles
        if filter_match_key(style) in selected_keys
    )
    values.update(query.styles)
    values.update(style for filter_item in query.genre_filters for style in filter_item.styles)
    return values

def checked_genre_values(
    filters: LibraryFilterOptions,
    query: AlbumListQuery,
) -> set[str]:
    selected_genre_keys = {filter_match_key(value) for value in selected_genre_values(filters, query)}
    selected_style_keys = {filter_match_key(value) for value in selected_style_values(filters, query)}
    checked: set[str] = set()
    for group in filters.genre_groups:
        genre_key = filter_match_key(group.genre)
        if genre_key not in selected_genre_keys:
            continue
        if not group.styles:
            checked.add(group.genre)
            continue
        if all(filter_match_key(style) in selected_style_keys for style in group.styles):
            checked.add(group.genre)
    return checked

def selected_genre_filter_count(
    filters: LibraryFilterOptions,
    query: AlbumListQuery,
) -> int:
    selected_genres = {filter_match_key(value) for value in selected_genre_values(filters, query)}
    selected_styles = {filter_match_key(value) for value in selected_style_values(filters, query)}
    count = sum(
        filter_match_key(style) in selected_styles
        for style in filters.loose_styles
    )
    for group in filters.genre_groups:
        matched_styles = [
            style
            for style in group.styles
            if filter_match_key(style) in selected_styles
        ]
        if matched_styles:
            count += len(matched_styles)
            continue
        if filter_match_key(group.genre) in selected_genres:
            count += 1
    return count
