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
KNOWN_IMAGE_MIME_TYPES = {
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def audio_mime_type_for_name(name: str) -> str:
    path = Path(name)
    known_type = KNOWN_AUDIO_MIME_TYPES.get(path.suffix.casefold())
    if known_type:
        return known_type
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def content_type_for_name(name: str) -> str:
    path = Path(name)
    suffix = path.suffix.casefold()
    known_type = KNOWN_AUDIO_MIME_TYPES.get(suffix) or KNOWN_IMAGE_MIME_TYPES.get(suffix)
    if known_type:
        return known_type
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def audio_content_type_for_name(name: str, content_type: str | None) -> str:
    declared_type = str(content_type or "").strip()
    declared_base_type = declared_type.split(";", 1)[0].strip().casefold()
    resolved_type = content_type_for_name(name)
    if resolved_type != "application/octet-stream":
        return resolved_type
    if declared_base_type in GENERIC_CONTENT_TYPES:
        return resolved_type
    return declared_type
