from __future__ import annotations

import re
from collections.abc import Iterable

DEFAULT_ALBUM_ARTIST_SPLIT_PATTERNS = ("with", "and", "&", ",", ";", "/", "-", "=")
WITH_WORD_RE = re.compile(r"(?<!\w)with(?!\w)", re.IGNORECASE)


def normalize_album_artist_split_patterns(values: Iterable[str | None]) -> tuple[str, ...]:
    patterns: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value is None:
            continue
        pattern = str(value).strip()
        if not pattern:
            continue
        key = pattern.casefold()
        if key in seen:
            continue
        seen.add(key)
        patterns.append(pattern)
    return tuple(patterns)


def album_artist_has_split_pattern(value: str | None, patterns: Iterable[str]) -> bool:
    text = (value or "").strip()
    if not text:
        return False
    folded = text.casefold()
    for pattern in patterns:
        needle = pattern.strip()
        if not needle:
            continue
        if is_word_pattern(needle):
            if re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", text, re.IGNORECASE):
                return True
            continue
        if needle.casefold() in folded:
            return True
    return False


def album_artist_has_mapping_pattern(value: str | None, patterns: Iterable[str]) -> bool:
    return album_artist_has_split_pattern(value, patterns)


def default_album_artist_mapping(value: str | None) -> tuple[str, ...]:
    text = (value or "").strip()
    if not text:
        return ()

    split_text = text
    if "&" in split_text and WITH_WORD_RE.search(split_text):
        split_text = WITH_WORD_RE.sub(",", split_text)

    if "&" in split_text:
        return normalized_album_artist_values(re.split(r"[/,&]", split_text))
    return normalized_album_artist_values(re.split(r"/", split_text))


def display_track_artist_lines(
    value: str | None,
    patterns: Iterable[str | None],
) -> tuple[str, ...]:
    text = (value or "").strip()
    if not text:
        return ()

    try:
        normalized_patterns = normalize_album_artist_split_patterns(patterns)
    except TypeError:
        normalized_patterns = DEFAULT_ALBUM_ARTIST_SPLIT_PATTERNS
    active_patterns = {pattern.casefold() for pattern in normalized_patterns}
    split_text = text
    split_with = (
        "with" in active_patterns
        and "&" in active_patterns
        and "&" in split_text
        and WITH_WORD_RE.search(split_text) is not None
    )
    if split_with:
        split_text = WITH_WORD_RE.sub(",", split_text)

    separators: list[str] = []
    for separator in ("/", ";", "&"):
        if separator in active_patterns:
            separators.append(separator)
    if ("," in active_patterns and "&" in text) or split_with:
        separators.append(",")

    if not separators:
        return normalized_album_artist_values((text,))

    return normalized_album_artist_values(
        re.split("|".join(re.escape(separator) for separator in separators), split_text)
    )


def mapped_album_artist_text(artists: Iterable[str | None]) -> str:
    return "\n".join(normalized_album_artist_values(artists))


def mapped_album_artists_from_text(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return normalized_album_artist_values(str(value).splitlines())


def album_artist_id_text(artists: Iterable[str | None]) -> str:
    return mapped_album_artist_text(artists).replace("\n", "-")


def display_album_artists(artists: Iterable[str | None]) -> str:
    return ", ".join(normalized_album_artist_values(artists))


def track_album_artist_source(track: object) -> str:
    album_artist = getattr(track, "album_artist", None)
    artist = getattr(track, "artist", None)
    return str(album_artist or artist or "").strip()


def track_album_artist_values(track: object) -> tuple[str, ...]:
    mapped = getattr(track, "album_artists", ())
    if mapped:
        return normalized_album_artist_values(mapped)
    source = track_album_artist_source(track)
    return normalized_album_artist_values((source,))


def normalized_album_artist_values(values: Iterable[str | None]) -> tuple[str, ...]:
    artists: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value is None:
            continue
        artist = " ".join(str(value).strip().split())
        if not artist:
            continue
        key = artist.casefold()
        if key in seen:
            continue
        seen.add(key)
        artists.append(artist)
    return tuple(artists)


def is_word_pattern(value: str) -> bool:
    return bool(value) and all(char.isalnum() or char == "_" for char in value)
