from __future__ import annotations

import re
from typing import Any

from .models import (
    ALBUM_LIST_SORT_RECENTLY_ADDED,
    AlbumListQuery,
    GenreStyleFilter,
)

GENRE_FILTER_PARAM_RE = re.compile(r"^genre\[(\d+)]\[(p|c)](?:\[\])?$")
DEFAULT_ALBUMS_PER_PAGE = 200
_UNSET = object()


def album_list_query_from_params(params: dict[str, list[str]]) -> AlbumListQuery:
    return AlbumListQuery(
        artists=tuple(params.get("artist", ())),
        album=first_value(params.get("album", ())),
        search=first_value(params.get("search", ())),
        genre_filters=genre_filters_from_params(params),
        page=parse_positive_int(first_value(params.get("page", ())), default=1),
        per_page=parse_positive_int(
            first_value(params.get("per_page", ())),
            default=DEFAULT_ALBUMS_PER_PAGE,
        ),
        sort=first_value(params.get("sort", ())) or ALBUM_LIST_SORT_RECENTLY_ADDED,
        cursor=first_value(params.get("cursor", ())),
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


def album_query_params(
    query: AlbumListQuery,
    *,
    page: int | None = None,
    cursor: str | None | object = _UNSET,
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
    if query.sort != ALBUM_LIST_SORT_RECENTLY_ADDED:
        params.append(("sort", query.sort))
    resolved_cursor = query.cursor if cursor is _UNSET else cursor
    if resolved_cursor:
        params.append(("cursor", resolved_cursor))
    if query.per_page != DEFAULT_ALBUMS_PER_PAGE:
        params.append(("per_page", query.per_page))
    return params


__all__ = [
    "DEFAULT_ALBUMS_PER_PAGE",
    "album_list_query_from_params",
    "album_query_params",
    "first_value",
    "genre_filters_from_params",
    "parse_positive_int",
]
