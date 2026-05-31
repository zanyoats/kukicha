from __future__ import annotations

import re
from typing import Any

from .models import (
    DEFAULT_ALBUM_LIST_SORT,
    AlbumListQuery,
    GenreStyleFilter,
    LibrarySearchQuery,
)

GENRE_FILTER_PARAM_RE = re.compile(r"^genre\[(\d+)]\[(p|c)](?:\[\])?$")
DEFAULT_ALBUMS_SIZE = 200
DEFAULT_SEARCH_COUNT = 20
_UNSET = object()


def album_list_query_from_params(params: dict[str, list[str]]) -> AlbumListQuery:
    return AlbumListQuery(
        artists=tuple(params.get("artist", ())),
        album=first_value(params.get("album", ())),
        search=first_value(params.get("search", ())),
        genre_filters=genre_filters_from_params(params),
        size=parse_positive_int(
            first_value(params.get("size", ())),
            default=DEFAULT_ALBUMS_SIZE,
        ),
        offset=parse_non_negative_int(first_value(params.get("offset", ())), default=0),
        sort=first_value(params.get("sort", ())) or DEFAULT_ALBUM_LIST_SORT,
    )


def library_search_query_from_params(params: dict[str, list[str]]) -> LibrarySearchQuery:
    return LibrarySearchQuery(
        query=first_raw_value(params.get("query", ())) or "",
        artist_count=parse_count(
            first_value(params.get("artistCount", ())),
            default=DEFAULT_SEARCH_COUNT,
        ),
        artist_offset=parse_non_negative_int(
            first_value(params.get("artistOffset", ())),
            default=0,
        ),
        album_count=parse_count(
            first_value(params.get("albumCount", ())),
            default=DEFAULT_SEARCH_COUNT,
        ),
        album_offset=parse_non_negative_int(
            first_value(params.get("albumOffset", ())),
            default=0,
        ),
        song_count=parse_count(
            first_value(params.get("songCount", ())),
            default=DEFAULT_SEARCH_COUNT,
        ),
        song_offset=parse_non_negative_int(
            first_value(params.get("songOffset", ())),
            default=0,
        ),
        music_folder_id=parse_optional_non_negative_int(
            first_value(params.get("musicFolderId", ())),
        ),
    )


def genre_filters_from_params(params: dict[str, list[str]]) -> tuple[GenreStyleFilter, ...]:
    grouped: dict[int, dict[str, list[str]]] = {}
    for key, values in params.items():
        match = GENRE_FILTER_PARAM_RE.match(key)
        if match is None:
            continue
        index = int(match.group(1))
        slot = match.group(2)
        grouped.setdefault(index, {"p": [], "c": []})[slot].extend(values)

    filters: list[GenreStyleFilter] = []
    for index in sorted(grouped):
        values = grouped[index]
        parent = first_value(values.get("p", ()))
        children = tuple(value for value in values.get("c", ()) if value and value.strip())
        if parent:
            filters.append(GenreStyleFilter(genre=parent, styles=children))
    return tuple(filters)


def first_value(values: Any) -> str | None:
    if not values:
        return None
    value = values[0]
    return str(value) if value not in {None, ""} else None


def first_raw_value(values: Any) -> str | None:
    if not values:
        return None
    value = values[0]
    return str(value) if value is not None else None


def parse_positive_int(value: str | None, *, default: int) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return max(1, parsed)


def parse_non_negative_int(value: str | None, *, default: int) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return max(0, parsed)


def parse_optional_non_negative_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return max(0, parsed)


def parse_count(value: str | None, *, default: int) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return min(500, max(0, parsed))


def album_query_params(
    query: AlbumListQuery,
    *,
    offset: int | None | object = _UNSET,
) -> list[tuple[str, object]]:
    params: list[tuple[str, object]] = []
    params.extend(("artist", artist) for artist in query.artists)
    if query.album:
        params.append(("album", query.album))
    if query.search:
        params.append(("search", query.search))
    for index, genre_filter in enumerate(query.genre_filters):
        params.append((f"genre[{index}][p]", genre_filter.genre))
        params.extend(
            (f"genre[{index}][c][]", style)
            for style in genre_filter.styles
        )
    if query.sort != DEFAULT_ALBUM_LIST_SORT:
        params.append(("sort", query.sort))
    if query.size != DEFAULT_ALBUMS_SIZE:
        params.append(("size", query.size))
    resolved_offset = query.offset if offset is _UNSET else offset
    if resolved_offset:
        params.append(("offset", resolved_offset))
    return params


def library_search_query_params(
    query: LibrarySearchQuery,
    *,
    artist_offset: int | None | object = _UNSET,
    album_offset: int | None | object = _UNSET,
    song_offset: int | None | object = _UNSET,
) -> list[tuple[str, object]]:
    params: list[tuple[str, object]] = [("query", query.query)]
    if query.music_folder_id is not None:
        params.append(("musicFolderId", query.music_folder_id))
    params.extend(
        (
            ("artistCount", query.artist_count),
            (
                "artistOffset",
                query.artist_offset if artist_offset is _UNSET else artist_offset or 0,
            ),
            ("albumCount", query.album_count),
            (
                "albumOffset",
                query.album_offset if album_offset is _UNSET else album_offset or 0,
            ),
            ("songCount", query.song_count),
            (
                "songOffset",
                query.song_offset if song_offset is _UNSET else song_offset or 0,
            ),
        )
    )
    return params


__all__ = [
    "DEFAULT_ALBUMS_SIZE",
    "DEFAULT_SEARCH_COUNT",
    "album_list_query_from_params",
    "album_query_params",
    "first_value",
    "genre_filters_from_params",
    "library_search_query_from_params",
    "library_search_query_params",
    "parse_count",
    "parse_non_negative_int",
    "parse_optional_non_negative_int",
    "parse_positive_int",
]
