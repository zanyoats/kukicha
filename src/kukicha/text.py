from __future__ import annotations

import re
import unicodedata

NORMALIZE_PATTERN = re.compile(r"[^a-z0-9]+")


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    normalized = strip_diacritics(value).casefold().replace("&", " and ")
    normalized = NORMALIZE_PATTERN.sub(" ", normalized)
    return " ".join(normalized.split())


def normalize_slug_text(value: str | None) -> str:
    return normalize_text(value).replace(" ", "-")


def strip_diacritics(value: str) -> str:
    return "".join(
        char
        for char in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(char)
    )
