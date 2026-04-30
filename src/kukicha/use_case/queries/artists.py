from __future__ import annotations

from collections.abc import Iterable
from sqlite3 import Connection

from .models import normalize_match


def canonical_album_artist_map(connection: Connection) -> dict[str, str]:
    rows = connection.execute(
        """
        SELECT MIN(artist) AS artist
        FROM library_album_artists
        WHERE COALESCE(artist, '') != ''
        GROUP BY artist COLLATE NOCASE
        """
    )
    return {
        normalize_match(str(row["artist"])): str(row["artist"])
        for row in rows
        if row["artist"]
    }


def canonical_album_artist_values(
    connection: Connection,
    values: Iterable[str | None],
) -> tuple[str, ...]:
    canonical_artists = canonical_album_artist_map(connection)
    artists: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        artist = canonical_artists.get(normalize_match(text), text)
        key = normalize_match(artist)
        if key in seen:
            continue
        seen.add(key)
        artists.append(artist)
    return tuple(artists)
