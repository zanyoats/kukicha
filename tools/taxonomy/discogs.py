from __future__ import annotations

import gzip
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from xml.etree.ElementTree import Element, iterparse


@dataclass(slots=True)
class DiscogsMaster:
    master_id: int
    artist_names: list[str]
    title: str
    year: int | None
    genres: list[str]
    styles: list[str]


def iter_discogs_masters(source: Path) -> Iterator[DiscogsMaster]:
    opener = gzip.open if source.suffix == ".gz" else open
    with opener(source, "rb") as handle:
        for _, element in iterparse(handle, events=("end",)):
            if element.tag != "master":
                continue
            try:
                yield parse_master(element)
            finally:
                element.clear()


def parse_master(element: Element) -> DiscogsMaster:
    master_id = int(element.attrib["id"])
    title = text_of(element.find("title")) or ""
    year = parse_int(text_of(element.find("year")))
    genres = [
        genre.text.strip()
        for genre in element.findall("./genres/genre")
        if genre.text and genre.text.strip()
    ]
    styles = [
        style.text.strip()
        for style in element.findall("./styles/style")
        if style.text and style.text.strip()
    ]
    artist_names = [
        name.text.strip()
        for name in element.findall("./artists/artist/name")
        if name.text and name.text.strip()
    ]
    return DiscogsMaster(
        master_id=master_id,
        artist_names=artist_names,
        title=title,
        year=year,
        genres=genres,
        styles=styles,
    )


def parse_int(value: str | None) -> int | None:
    if not value or not value.isdigit():
        return None
    return int(value)


def text_of(element: Element | None) -> str | None:
    if element is None or element.text is None:
        return None
    value = element.text.strip()
    return value or None


def unique_casefold(values: Iterable[str]) -> list[str]:
    seen: dict[str, str] = {}
    for value in values:
        stripped = value.strip()
        if not stripped:
            continue
        seen.setdefault(stripped.casefold(), stripped)
    return sorted(seen.values(), key=str.casefold)
