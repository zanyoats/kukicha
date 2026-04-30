from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from .album_artists import (
    album_artist_id_text,
    display_album_artists,
    track_album_artist_values,
)
from .models import MusicLibrary, TrackRecord
from .text import normalize_slug_text, normalize_text

YEAR_PATTERN = re.compile(r"(\d{4})")


@dataclass(slots=True)
class LocalAlbum:
    album_id: str
    artist: str
    artists: tuple[str, ...]
    artist_id_text: str
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
        artists = track_album_artist_values(track)
        artist = album_artist_id_text(artists)
        album = track.album
        if not artist or not album:
            continue
        key = (normalize_text(artist), normalize_text(album))
        if not key[0] or not key[1]:
            continue
        grouped_tracks[key].append(track)

    albums: list[LocalAlbum] = []
    for tracks in grouped_tracks.values():
        artists = most_common_artist_values(track_album_artist_values(track) for track in tracks)
        artist = display_album_artists(artists) or "<unknown artist>"
        artist_id = album_artist_id_text(artists) or artist
        album = most_common_value(track.album for track in tracks) or "<unknown album>"
        year = most_common_year(parse_year(track.date) for track in tracks)
        album_id = f"{normalize_slug_text(artist_id)}::{normalize_slug_text(album)}"
        albums.append(
            LocalAlbum(
                album_id=album_id,
                artist=artist,
                artists=artists,
                artist_id_text=artist_id,
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


def most_common_artist_values(values: Iterable[tuple[str, ...]]) -> tuple[str, ...]:
    counter = Counter(value for value in values if value)
    return counter.most_common(1)[0][0] if counter else ()


def most_common_year(values: Iterable[int | None]) -> int | None:
    counter = Counter(value for value in values if value is not None)
    return counter.most_common(1)[0][0] if counter else None


def earliest_file_created_at(tracks: Iterable[TrackRecord]) -> str | None:
    return min(
        (track.file_created_at for track in tracks if track.file_created_at),
        default=None,
    )
