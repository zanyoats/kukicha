from __future__ import annotations

import mimetypes
from pathlib import Path

GENERIC_CONTENT_TYPES = frozenset({"", "application/octet-stream", "binary/octet-stream"})
KNOWN_AUDIO_MIME_TYPES = {
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
    ".m4b": "audio/mp4",
    ".m4p": "audio/mp4",
    ".m4r": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".oga": "audio/ogg",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
}


def audio_mime_type_for_name(name: str) -> str:
    path = Path(name)
    known_type = KNOWN_AUDIO_MIME_TYPES.get(path.suffix.casefold())
    if known_type:
        return known_type
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def audio_content_type_for_name(name: str, content_type: str | None) -> str:
    declared_type = str(content_type or "").strip()
    declared_base_type = declared_type.split(";", 1)[0].strip().casefold()
    if declared_base_type in GENERIC_CONTENT_TYPES:
        return audio_mime_type_for_name(name)
    return declared_type
