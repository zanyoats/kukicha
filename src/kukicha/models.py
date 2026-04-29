from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field


TRACK_ARTWORK_HEIGHT = 32
ALBUM_ARTWORK_HEIGHT = 250


@dataclass(slots=True)
class TrackArtwork:
    mime_type: str
    data: bytes


@dataclass(slots=True)
class TrackRecord:
    path: str
    track_id: int | None = None
    root_position: int | None = None
    file_created_at: str | None = None
    file_type: str | None = None
    scan_error: str | None = None
    artist: str | None = None
    album_artist: str | None = None
    composer: str | None = None
    album: str | None = None
    title: str | None = None
    work: str | None = None
    grouping: str | None = None
    movement_name: str | None = None
    track_number: str | None = None
    disc_number: str | None = None
    date: str | None = None
    itunes_store_track_id: str | None = None
    itunes_store_album_id: str | None = None
    genres: list[str] = field(default_factory=list)
    styles: list[str] = field(default_factory=list)
    has_cover: bool = False
    is_compilation: bool = False
    artwork: TrackArtwork | None = None
    album_artwork: TrackArtwork | None = None
    duration_seconds: float | None = None
    bitrate: int | None = None


@dataclass(slots=True)
class PlaylistItemRecord:
    path: str
    track_id: int | None = None
    title: str | None = None
    duration_seconds: float | None = None
    genre: str | None = None
    cover_url: str | None = None


@dataclass(slots=True)
class PlaylistRecord:
    path: str
    name: str
    root_position: int | None = None
    playlist_id: int | None = None
    file_created_at: str | None = None
    cover_svg: str = ""
    items: list[PlaylistItemRecord] = field(default_factory=list)


def normalize_genre_values(values: Iterable[str | None]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value:
            continue
        parts = [value]
        for separator in (";", "/", "|", ","):
            next_parts: list[str] = []
            for part in parts:
                next_parts.extend(part.split(separator))
            parts = next_parts
        for part in parts:
            genre = part.strip()
            if not genre:
                continue
            key = genre.casefold()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(genre)
    return normalized


@dataclass(slots=True)
class MusicLibrary:
    roots: list[str]
    tracks: list[TrackRecord]
    supported_extensions: list[str]
    generated_at: str
    playlists: list[PlaylistRecord] = field(default_factory=list)
