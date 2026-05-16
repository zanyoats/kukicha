from __future__ import annotations

import re
from typing import Any

from .models import (
    DEFAULT_ALBUM_LIST_SORT,
    AlbumListQuery,
    GenreStyleFilter,
)

GENRE_FILTER_PARAM_RE = re.compile(r"^genre\[(\d+)]\[(p|c)](?:\[\])?$")
DEFAULT_ALBUMS_SIZE = 200
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


__all__ = [
    "DEFAULT_ALBUMS_SIZE",
    "album_list_query_from_params",
    "album_query_params",
    "first_value",
    "genre_filters_from_params",
    "parse_non_negative_int",
    "parse_positive_int",
]
