from __future__ import annotations

import unicodedata


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    normalized = strip_diacritics(value).casefold().replace("&", " and ")
    parts: list[str] = []
    current: list[str] = []
    for character in normalized:
        if character.isalnum():
            current.append(character)
            continue
        if current:
            parts.append("".join(current))
            current.clear()
    if current:
        parts.append("".join(current))
    return " ".join(parts)


def normalize_slug_text(value: str | None) -> str:
    return normalize_text(value).replace(" ", "-")


def strip_diacritics(value: str) -> str:
    return "".join(
        char
        for char in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(char)
    )
