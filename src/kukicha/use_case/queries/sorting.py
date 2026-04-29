from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from .models import (
    ALBUM_LIST_SORT_ARTIST,
    AlbumSummary,
    PlaylistTrack,
)


def album_page_sort_key(
    sort: str,
) -> Callable[[AlbumSummary], tuple[object, ...]]:
    if sort == ALBUM_LIST_SORT_ARTIST:
        return album_page_item_sort_key
    return album_page_recently_added_sort_key


def album_page_recently_added_sort_key(
    item: AlbumSummary,
) -> tuple[int, float, str, str, int]:
    timestamp = parsed_iso_timestamp(item.file_created_at)
    return (
        1 if timestamp is None else 0,
        -timestamp if timestamp is not None else 0,
        item.artist.casefold().strip(),
        item.album.casefold().strip(),
        1 if item.is_playlist else 0,
    )


def album_page_item_sort_key(item: AlbumSummary) -> tuple[str, str, int]:
    return (
        item.artist.casefold().strip(),
        item.album.casefold().strip(),
        1 if item.is_playlist else 0,
    )


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
) -> tuple[str, str, int, int, str, str, str]:
    return (
        normalize_sort_value(track.album_artist or track.artist),
        normalize_sort_value(track.album),
        parse_track_index(track.disc_number),
        parse_track_index(track.track_number),
        normalize_sort_value(track.artist),
        normalize_sort_value(track.title or Path(track.path).name),
        track.path.casefold(),
    )


def normalize_sort_value(value: str | None) -> str:
    return value.casefold().strip() if value else ""


def parse_track_index(value: str | None) -> int:
    if not value:
        return 0
    number = value.split("/", maxsplit=1)[0].strip()
    return int(number) if number.isdigit() else 0
