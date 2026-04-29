from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from .models import MusicLibrary, TrackRecord
from .text import normalize_slug_text, normalize_text

YEAR_PATTERN = re.compile(r"(\d{4})")


@dataclass(slots=True)
class LocalAlbum:
    album_id: str
    artist: str
    album: str
    year: int | None
    track_count: int
    file_created_at: str | None = None

    @property
    def artist_key(self) -> str:
        return normalize_text(self.artist)

    @property
    def album_key(self) -> str:
        return normalize_text(self.album)


def group_library_albums(library: MusicLibrary) -> list[LocalAlbum]:
    grouped_tracks: dict[tuple[str, str], list[TrackRecord]] = defaultdict(list)
    for track in library.tracks:
        if track.scan_error:
            continue
        artist = track.album_artist or track.artist
        album = track.album
        if not artist or not album:
            continue
        key = (normalize_text(artist), normalize_text(album))
        if not key[0] or not key[1]:
            continue
        grouped_tracks[key].append(track)

    albums: list[LocalAlbum] = []
    for tracks in grouped_tracks.values():
        artist = (
            most_common_value(track.album_artist or track.artist for track in tracks)
            or "<unknown artist>"
        )
        album = most_common_value(track.album for track in tracks) or "<unknown album>"
        year = most_common_year(parse_year(track.date) for track in tracks)
        album_id = f"{normalize_slug_text(artist)}::{normalize_slug_text(album)}"
        albums.append(
            LocalAlbum(
                album_id=album_id,
                artist=artist,
                album=album,
                year=year,
                track_count=len(tracks),
                file_created_at=earliest_file_created_at(tracks),
            )
        )
    return sorted(albums, key=lambda album: (album.artist_key, album.album_key))


def parse_year(value: str | None) -> int | None:
    if not value:
        return None
    match = YEAR_PATTERN.search(value)
    return int(match.group(1)) if match else None


def most_common_value(values: Iterable[str | None]) -> str | None:
    counter = Counter(value for value in values if value)
    return counter.most_common(1)[0][0] if counter else None


def most_common_year(values: Iterable[int | None]) -> int | None:
    counter = Counter(value for value in values if value is not None)
    return counter.most_common(1)[0][0] if counter else None


def earliest_file_created_at(tracks: Iterable[TrackRecord]) -> str | None:
    return min(
        (track.file_created_at for track in tracks if track.file_created_at),
        default=None,
    )
