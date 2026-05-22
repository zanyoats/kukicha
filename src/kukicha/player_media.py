from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Iterable

from .library_sources import (
    SOURCE_KIND_LOCAL,
    SOURCE_KIND_S3,
    create_s3_client,
    remote_root_from_source_json,
)
from .media_resources import AudioResource
from .use_case import store_track_artwork_by_path
from .models import ALBUM_ARTWORK_HEIGHT, TRACK_ARTWORK_HEIGHT, TrackArtwork

PROTECTED_AUDIO_SCAN_BYTES = 1024 * 1024
PROTECTED_MP4_SAMPLE_ENTRIES = (b"drms", b"drmi")
DOWNLOAD_CHUNK_SIZE = 1024 * 512

def audio_mime_type(path: Path) -> str:
    return audio_mime_type_for_name(path.name)

def audio_mime_type_for_name(name: str) -> str:
    path = Path(name)
    if path.suffix.casefold() in {".m4a", ".m4b", ".m4p", ".m4r"}:
        return "audio/mp4"
    if path.suffix.casefold() in {".oga", ".opus"}:
        return "audio/ogg"
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"

def audio_resource_head(resource: AudioResource) -> tuple[int, str]:
    if resource.kind == SOURCE_KIND_LOCAL:
        path = resource.local_path
        if not path.is_file():
            raise FileNotFoundError(path)
        file_size = path.stat().st_size
        if file_size <= 0:
            raise FileNotFoundError(path)
        return file_size, audio_mime_type(path)

    if resource.kind != SOURCE_KIND_S3 or not resource.object_key:
        raise FileNotFoundError(resource.path)
    remote = remote_root_from_source_json(resource.source_json)
    client = create_s3_client(remote)
    try:
        response = client.head_object(Bucket=remote.bucket, Key=resource.object_key)
    except Exception as error:
        if s3_error_is_not_found(error):
            raise FileNotFoundError(resource.path) from error
        raise
    file_size = int(response.get("ContentLength") or resource.size_bytes or 0)
    if file_size <= 0:
        raise FileNotFoundError(resource.path)
    content_type = str(response.get("ContentType") or resource.content_type or "")
    return file_size, content_type or audio_mime_type_for_name(resource.name)

def iter_audio_resource_bytes(
    resource: AudioResource,
    *,
    start: int,
    length: int,
) -> Iterable[bytes]:
    if resource.kind == SOURCE_KIND_LOCAL:
        with resource.local_path.open("rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining > 0:
                chunk = handle.read(min(DOWNLOAD_CHUNK_SIZE, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk
        return

    if resource.kind != SOURCE_KIND_S3 or not resource.object_key:
        raise FileNotFoundError(resource.path)
    remote = remote_root_from_source_json(resource.source_json)
    client = create_s3_client(remote)
    end = start + length - 1
    try:
        response = client.get_object(
            Bucket=remote.bucket,
            Key=resource.object_key,
            Range=f"bytes={start}-{end}",
        )
    except Exception as error:
        if s3_error_is_not_found(error):
            raise FileNotFoundError(resource.path) from error
        raise
    body = response.get("Body") if isinstance(response, dict) else None
    if body is None or not hasattr(body, "read"):
        raise FileNotFoundError(resource.path)
    try:
        remaining = length
        while remaining > 0:
            chunk = body.read(min(DOWNLOAD_CHUNK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
    finally:
        close = getattr(body, "close", None)
        if callable(close):
            close()

def s3_error_is_not_found(error: Exception) -> bool:
    response = getattr(error, "response", None)
    if isinstance(response, dict):
        code = str(response.get("Error", {}).get("Code", ""))
        return code in {"404", "NoSuchKey", "NotFound"}
    return isinstance(error, FileNotFoundError)

def audio_unsupported_reason_for_path(path: Path) -> str:
    if path.suffix.casefold() == ".m4p" and is_protected_mpeg4_audio(path):
        return "This appears to be a protected .m4p file. Browsers cannot play DRM-protected audio from the local player."
    return ""

def mpeg4_audio_codec_for_path(path: Path) -> str:
    if path.suffix.casefold() not in {".m4a", ".m4b", ".m4p", ".m4r"}:
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
