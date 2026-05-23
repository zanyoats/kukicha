from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from .album_artists import (
    album_artist_id_text,
    display_album_artists,
    track_album_artist_source,
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
    file_album_id: str
    release_variant: str | None
    musicbrainz_release_mbid: str | None
    musicbrainz_release_group_mbid: str | None
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
    grouped_tracks: dict[tuple[str, str, str], list[TrackRecord]] = defaultdict(list)
    for track in library.tracks:
        if track.scan_error:
            continue
        artist = track_raw_album_artist_id_text(track)
        album = track.album
        if not artist or not album:
            continue
        release_variant = normalize_release_variant(track.musicbrainz_release_variant) or ""
        key = (normalize_text(artist), normalize_text(album), release_variant)
        if not key[0] or not key[1]:
            continue
        grouped_tracks[key].append(track)

    albums: list[LocalAlbum] = []
    for tracks in grouped_tracks.values():
        artists = most_common_artist_values(track_album_artist_values(track) for track in tracks)
        artist = display_album_artists(artists) or "<unknown artist>"
        artist_id = most_common_value(
            track_raw_album_artist_id_text(track) for track in tracks
        ) or artist
        album = most_common_value(track.album for track in tracks) or "<unknown album>"
        year = most_common_year(parse_year(track.date) for track in tracks)
        release_variant = most_common_value(
            normalize_release_variant(track.musicbrainz_release_variant)
            for track in tracks
        )
        file_album_id = local_album_id(artist_id, album)
        album_id = local_album_id(artist_id, album, release_variant=release_variant)
        albums.append(
            LocalAlbum(
                album_id=album_id,
                artist=artist,
                artists=artists,
                artist_id_text=artist_id,
                album=album,
                file_album_id=file_album_id,
                release_variant=release_variant,
                musicbrainz_release_mbid=most_common_value(
                    track.musicbrainz_release_mbid
                    for track in tracks
                    if normalize_release_variant(track.musicbrainz_release_variant) == release_variant
                ),
                musicbrainz_release_group_mbid=most_common_value(
                    track.musicbrainz_release_group_mbid
                    for track in tracks
                    if normalize_release_variant(track.musicbrainz_release_variant) == release_variant
                ),
                year=year,
                track_count=len(tracks),
                file_created_at=earliest_file_created_at(tracks),
            )
        )
    return sorted(albums, key=lambda album: (album.artist_key, album.album_key))


def track_raw_album_artist_id_text(track: TrackRecord) -> str:
    return album_artist_id_text((track_album_artist_source(track),))


def local_album_id(
    artist_id: str,
    album: str,
    *,
    release_variant: str | None = None,
) -> str:
    album_id = f"{normalize_slug_text(artist_id)}::{normalize_slug_text(album)}"
    variant = normalize_release_variant(release_variant)
    return f"{album_id}::{variant}" if variant else album_id


def normalize_release_variant(value: object) -> str | None:
    if value is None:
        return None
    variant = str(value).strip().lower()
    return variant or None


def file_album_id_from_album_id(album_id: str) -> str:
    parts = str(album_id or "").split("::")
    if len(parts) != 3:
        return str(album_id or "")
    release_variant = normalize_release_variant(parts[2])
    if release_variant and len(release_variant) == 3 and all(
        character in "0123456789abcdef" for character in release_variant
    ):
        return "::".join(parts[:2])
    return str(album_id or "")


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
