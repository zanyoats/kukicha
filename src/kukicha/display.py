from __future__ import annotations

import re


ALBUM_TITLE_QUALIFIER_RE = re.compile(r"\s*(?:\([^()]*\)|\[[^\[\]]*\])")


def display_album_title(album: str) -> str:
    cleaned = " ".join(ALBUM_TITLE_QUALIFIER_RE.sub(" ", album).split())
    return cleaned or album
