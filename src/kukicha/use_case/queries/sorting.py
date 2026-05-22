from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
import re

from ..._compat import UTC
from .models import (
    ALBUM_LIST_SORT_ALBUMS,
    ALBUM_LIST_SORT_ARTIST,
    ALBUM_LIST_SORT_GENRE,
    ALBUM_LIST_SORT_STARRED,
    AlbumSummary,
    PlaylistTrack,
)

TRACK_NUMBER_SEGMENT_RE = re.compile(r"\d+|\D+")


def album_page_sort_key(
    sort: str,
) -> Callable[[AlbumSummary], tuple[object, ...]]:
    if sort == ALBUM_LIST_SORT_GENRE:
        return album_page_genre_sort_key
    if sort == ALBUM_LIST_SORT_ALBUMS:
        return album_page_album_sort_key
    if sort == ALBUM_LIST_SORT_ARTIST:
        return album_page_item_sort_key
    if sort == ALBUM_LIST_SORT_STARRED:
        return album_page_starred_sort_key
    return album_page_recently_added_sort_key


def album_page_starred_sort_key(
    item: AlbumSummary,
) -> tuple[int, float, str, tuple[int, int], str, int]:
    timestamp = parsed_iso_timestamp(item.starred_at)
    return (
        1 if timestamp is None else 0,
        -timestamp if timestamp is not None else 0,
        item.artist.casefold().strip(),
        album_year_sort_key(item.year),
        item.album.casefold().strip(),
        1 if item.is_playlist else 0,
    )


def album_page_recently_added_sort_key(
    item: AlbumSummary,
) -> tuple[int, float, str, tuple[int, int], str, int]:
    timestamp = parsed_iso_timestamp(item.added_at or item.file_created_at)
    return (
        1 if timestamp is None else 0,
        -timestamp if timestamp is not None else 0,
        item.artist.casefold().strip(),
        album_year_sort_key(item.year),
        item.album.casefold().strip(),
        1 if item.is_playlist else 0,
    )


def album_page_item_sort_key(item: AlbumSummary) -> tuple[str, tuple[int, int], str, int]:
    return (
        item.artist.casefold().strip(),
        album_year_sort_key(item.year),
        item.album.casefold().strip(),
        1 if item.is_playlist else 0,
    )


def album_page_album_sort_key(
    item: AlbumSummary,
) -> tuple[str, str, tuple[int, int], str]:
    return (
        item.album.casefold().strip(),
        item.artist.casefold().strip(),
        album_year_sort_key(item.year),
        item.album_id,
    )


def album_page_genre_sort_key(
    item: AlbumSummary,
) -> tuple[int, str, str, tuple[int, int], str, int]:
    return (
        1 if item.sort_genre is None else 0,
        normalize_sort_value(item.sort_genre),
        *album_page_item_sort_key(item),
    )


def album_year_sort_key(year: int | None) -> tuple[int, int]:
    return (1, 0) if year is None else (0, year)


def parsed_iso_timestamp(value: str | None) -> float | None:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def playlist_track_sort_key(
    track: PlaylistTrack,
) -> tuple[
    str,
    str,
    tuple[int, tuple[tuple[int, object], ...], str],
    tuple[int, tuple[tuple[int, object], ...], str],
    str,
    str,
    str,
]:
    return (
        normalize_sort_value(track.album_artist or track.artist),
        normalize_sort_value(track.album),
        track_index_sort_key(track.disc_number),
        track_index_sort_key(track.track_number),
        normalize_sort_value(track.artist),
        normalize_sort_value(track.title or Path(track.path).name),
        track.path.casefold(),
    )


def normalize_sort_value(value: str | None) -> str:
    return value.casefold().strip() if value else ""


def track_index_sort_key(
    value: str | None,
) -> tuple[int, tuple[tuple[int, object], ...], str]:
    if not value:
        return (0, (), "")
    number = value.split("/", maxsplit=1)[0].strip()
    if not number:
        return (0, (), "")
    segments: list[tuple[int, object]] = []
    for segment in TRACK_NUMBER_SEGMENT_RE.findall(number):
        if segment.isdigit():
            segments.append((0, int(segment)))
        else:
            segments.append((1, segment.casefold()))
    return (1, tuple(segments), number.casefold())
