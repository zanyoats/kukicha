from __future__ import annotations

import re
from dataclasses import dataclass


SEARCH_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


@dataclass(frozen=True, slots=True)
class SearchFactor:
    match_query: str
    negated: bool = False


def parse_album_search_query(value: str | None) -> tuple[tuple[SearchFactor, ...], ...]:
    if not value:
        return ()

    groups: list[list[SearchFactor]] = []
    current: list[SearchFactor] = []
    index = 0
    while index < len(value):
        character = value[index]
        if character.isspace():
            index += 1
            continue
        if character == ";":
            if current:
                groups.append(current)
                current = []
            index += 1
            continue

        negated = False
        if character == "-":
            negated = True
            index += 1
            while index < len(value) and value[index].isspace():
                index += 1
            if index >= len(value) or value[index] == ";":
                continue

        if value[index] == '"':
            phrase, index = read_quoted_phrase(value, index + 1)
            tokens = search_tokens(phrase)
            if tokens:
                current.append(SearchFactor(fts5_phrase(tokens), negated=negated))
            continue

        start = index
        while index < len(value) and not value[index].isspace() and value[index] != ";":
            index += 1
        for token in search_tokens(value[start:index]):
            current.append(SearchFactor(fts5_phrase((token,)), negated=negated))

    if current:
        groups.append(current)
    return tuple(tuple(group) for group in groups)


def read_quoted_phrase(value: str, index: int) -> tuple[str, int]:
    start = index
    while index < len(value):
        if value[index] == '"':
            return value[start:index], index + 1
        index += 1
    return value[start:], index


def search_tokens(value: str) -> tuple[str, ...]:
    return tuple(match.group(0) for match in SEARCH_TOKEN_RE.finditer(value))


def fts5_phrase(tokens: tuple[str, ...]) -> str:
    return f'"{" ".join(tokens)}"'


__all__ = [
    "SearchFactor",
    "parse_album_search_query",
]
