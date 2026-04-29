from __future__ import annotations

import mimetypes
from pathlib import Path

from .use_case import store_track_artwork_by_path
from .models import ALBUM_ARTWORK_HEIGHT, TRACK_ARTWORK_HEIGHT, TrackArtwork

PROTECTED_AUDIO_SCAN_BYTES = 1024 * 1024
PROTECTED_MP4_SAMPLE_ENTRIES = (b"drms", b"drmi")

def audio_mime_type(path: Path) -> str:
    if path.suffix.casefold() in {".m4a", ".m4p"}:
        return "audio/mp4"
    if path.suffix.casefold() == ".opus":
        return "audio/ogg"
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"

def audio_unsupported_reason_for_path(path: Path) -> str:
    if path.suffix.casefold() == ".m4p" and is_protected_mpeg4_audio(path):
        return "This appears to be a protected .m4p file. Browsers cannot play DRM-protected audio from the local player."
    return ""

def mpeg4_audio_codec_for_path(path: Path) -> str:
    if path.suffix.casefold() not in {".m4a", ".m4p"}:
        return ""

    try:
        from mutagen import File as MutagenFile
    except ImportError:
        return ""

    try:
        audio = MutagenFile(path)
    except Exception:
        return ""
    if audio is None:
        return ""

    codec = getattr(getattr(audio, "info", None), "codec", "")
    return str(codec).casefold()

def is_protected_mpeg4_audio(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            data = handle.read(PROTECTED_AUDIO_SCAN_BYTES)
    except OSError:
        return False
    return any(sample_entry in data for sample_entry in PROTECTED_MP4_SAMPLE_ENTRIES)

def extract_and_store_artwork(database: Path, path: str) -> dict[int, TrackArtwork]:
    try:
        from .scanner import scan_track
    except ImportError:
        return {}

    try:
        track = scan_track(Path(path))
    except Exception:
        return {}

    artwork_by_height = {
        height_px: artwork
        for height_px, artwork in (
            (TRACK_ARTWORK_HEIGHT, track.artwork),
            (ALBUM_ARTWORK_HEIGHT, track.album_artwork),
        )
        if artwork is not None and artwork.data
    }
    for height_px, artwork in artwork_by_height.items():
        store_track_artwork_by_path(database, path, artwork, height_px=height_px)
    return artwork_by_height
