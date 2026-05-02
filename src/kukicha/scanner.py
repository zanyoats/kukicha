from __future__ import annotations

import base64
import binascii
import json
import mimetypes
import re
import struct
import unicodedata
from collections.abc import Iterable
from datetime import UTC, datetime
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from mutagen import File as MutagenFile

from .file_metadata import file_created_at
from .models import (
    ALBUM_ARTWORK_HEIGHT,
    TRACK_ARTWORK_HEIGHT,
    MusicLibrary,
    PlaylistItemRecord,
    PlaylistRecord,
    TrackArtwork,
    TrackRecord,
    UNKNOWN_METADATA_TAG,
    normalize_genre_values,
)
from .playlist_art import playlist_cover_svg

SUPPORTED_EXTENSIONS = {".flac", ".m4a", ".m4p", ".mp3", ".ogg", ".opus"}
PLAYLIST_EXTENSIONS = {".m3u", ".m3u8"}
ARTWORK_THUMBNAIL_HEIGHT = TRACK_ARTWORK_HEIGHT
ALBUM_ARTWORK_THUMBNAIL_HEIGHT = ALBUM_ARTWORK_HEIGHT
ARTWORK_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".gif")
ALBUM_ARTWORK_NAMES = ("cover", "folder", "front", "album", "artwork", "albumart")
RAW_KEY_ALIASES = {
    "tpe1": "artist",
    "tpe2": "albumartist",
    "tcom": "composer",
    "talb": "album",
    "tit1": "grouping",
    "tit2": "title",
    "trck": "tracknumber",
    "tpos": "discnumber",
    "tdor": "originaldate",
    "tdrc": "date",
    "tory": "originalyear",
    "tyer": "date",
    "tcon": "genre",
    "\xa9art": "artist",
    "aart": "albumartist",
    "\xa9wrt": "composer",
    "\xa9alb": "album",
    "\xa9grp": "grouping",
    "\xa9mvn": "movementname",
    "\xa9nam": "title",
    "\xa9wrk": "work",
    "\xa9day": "date",
    "\xa9gen": "genre",
    "----:com.apple.itunes:work": "work",
    "----:com.apple.itunes:movementname": "movementname",
    "----:com.apple.itunes:originaldate": "originaldate",
    "----:com.apple.itunes:originalyear": "originalyear",
    "----:com.apple.itunes:originalreleasedate": "originaldate",
    "----:com.apple.itunes:originalreleaseyear": "originalyear",
    "trkn": "tracknumber",
    "disk": "discnumber",
    "tcmp": "compilation",
    "cpil": "compilation",
    "----:com.apple.itunes:compilation": "compilation",
    "originaldate": "originaldate",
    "originalyear": "originalyear",
    "originalreleasedate": "originaldate",
    "originalreleaseyear": "originalyear",
}
PRIMARY_TAG_FIELDS: dict[str, tuple[str, ...]] = {
    "artist": ("artist", "albumartist", "album artist", "composer"),
    "album_artist": ("albumartist", "album artist", "artist"),
    "composer": ("composer",),
    "album": ("album",),
    "title": ("title",),
    "work": ("work",),
    "grouping": ("grouping", "contentgroup", "content group"),
    "movement_name": ("movementname", "movement name", "movement_name"),
    "is_compilation": ("compilation",),
    "track_number": ("tracknumber", "track"),
    "disc_number": ("discnumber", "disc"),
    "date": ("originaldate", "originalyear", "date", "year"),
}
GENRE_TAG_NAMES = {
    "genre",
    "genres",
    "style",
    "styles",
}
ARTWORK_TAG_NAMES = {
    "covr",
    "coverart",
    "metadata_block_picture",
    "metadata-block-picture",
    "metadatablockpicture",
    "picture",
}
ARTWORK_TAG_COMPACTS = {
    value.replace("_", "").replace("-", "") for value in ARTWORK_TAG_NAMES
}
DEFAULT_SCAN_PROGRESS_EVERY = 500
IGNORED_RAW_TAG_PREFIXES = {
    "apic",
    "coverart",
    "covr",
    "geob",
    "mcdifact",
    "metadatablockpicture",
    "picture",
    "priv",
    "ufid",
}
ITUNES_STORE_FILE_TYPES = {"m4a", "m4p"}
PLAYLIST_NAME_PREFIX = "#PLAYLIST:"
EXTINF_PREFIX = "#EXTINF:"
EXTGENRE_PREFIX = "#EXTGENRE:"
EXTALBUMARTURL_PREFIX = "#EXTALBUMARTURL:"


def build_library(
    roots: Iterable[Path],
    *,
    progress: Callable[[str], None] | None = None,
    progress_every: int = DEFAULT_SCAN_PROGRESS_EVERY,
    on_missing_required_tags: Callable[[TrackRecord, list[str]], None] | None = None,
) -> MusicLibrary:
    clear_external_artwork_caches()
    resolved_roots = [root.expanduser().resolve() for root in roots]
    tracks: list[TrackRecord] = []
    playlist_paths: list[tuple[int, Path]] = []
    count = 0
    progress_step = max(1, int(progress_every))
    for root_position, root in enumerate(resolved_roots):
        if progress:
            progress(f"scanning root {root_position + 1}/{len(resolved_roots)}: {root}")
        for path in iter_library_files([root]):
            if path.suffix.casefold() in PLAYLIST_EXTENSIONS:
                playlist_paths.append((root_position, path))
                continue
            count += 1
            track = scan_track(path)
            track.root_position = root_position
            missing_fields = missing_required_tags(track)
            if missing_fields:
                if on_missing_required_tags is not None:
                    on_missing_required_tags(track, missing_fields)
            else:
                tracks.append(track)
            if progress and count % progress_step == 0:
                progress(f"scanned {count} music files")
    if progress and count % progress_step:
        progress(f"scanned {count} music files")
    playlists = parse_playlists(playlist_paths, tracks)
    return MusicLibrary(
        roots=[str(root) for root in resolved_roots],
        tracks=tracks,
        supported_extensions=sorted(SUPPORTED_EXTENSIONS),
        generated_at=datetime.now(UTC).isoformat(),
        playlists=playlists,
    )


def iter_library_files(roots: Iterable[Path]) -> Iterable[Path]:
    yield from iter_music_files(roots)
    yield from iter_playlist_files(roots)


def iter_music_files(roots: Iterable[Path]) -> Iterable[Path]:
    for root in roots:
        if root.is_file() and root.suffix.casefold() in SUPPORTED_EXTENSIONS:
            yield root
            continue
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.casefold() in SUPPORTED_EXTENSIONS:
                yield path


def iter_playlist_files(roots: Iterable[Path]) -> Iterable[Path]:
    for root in roots:
        if root.is_file() and root.suffix.casefold() in PLAYLIST_EXTENSIONS:
            yield root
            continue
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.casefold() in PLAYLIST_EXTENSIONS:
                yield path


def parse_playlists(
    playlist_paths: Iterable[tuple[int | None, Path]],
    tracks: Iterable[TrackRecord],
) -> list[PlaylistRecord]:
    tracks_by_path = {
        normalize_local_playlist_path(track.path): track
        for track in tracks
    }
    playlists: list[PlaylistRecord] = []
    for root_position, path in playlist_paths:
        playlist = parse_m3u_playlist(path, tracks_by_path, root_position=root_position)
        if playlist is not None:
            playlists.append(playlist)
    return playlists


def parse_m3u_playlist(
    path: Path,
    tracks_by_path: dict[str, TrackRecord],
    *,
    root_position: int | None = None,
) -> PlaylistRecord | None:
    resolved_path = path.expanduser().resolve(strict=False)
    name = resolved_path.stem
    items: list[PlaylistItemRecord] = []
    pending_title: str | None = None
    pending_duration: float | None = None
    pending_genre: str | None = None
    pending_cover_url: str | None = None

    text = read_playlist_text(resolved_path)
    if text is None:
        return None
    lines = text.splitlines()

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        line_key = line.casefold()
        if line.startswith("#"):
            if line_key.startswith(PLAYLIST_NAME_PREFIX.casefold()):
                candidate = line[len(PLAYLIST_NAME_PREFIX) :].strip()
                if candidate:
                    name = candidate
                continue
            if line_key.startswith(EXTINF_PREFIX.casefold()):
                pending_duration, pending_title = parse_extinf(line[len(EXTINF_PREFIX) :])
                continue
            if line_key.startswith(EXTGENRE_PREFIX.casefold()):
                pending_genre = line[len(EXTGENRE_PREFIX) :].strip() or None
                continue
            if line_key.startswith(EXTALBUMARTURL_PREFIX.casefold()):
                pending_cover_url = line[len(EXTALBUMARTURL_PREFIX) :].strip() or None
                continue
            continue

        item_path = normalize_playlist_resource(line, resolved_path)
        track = tracks_by_path.get(item_path) if not is_url_resource(item_path) else None
        if track is not None:
            items.append(
                PlaylistItemRecord(
                    path=track.path,
                    track_id=track.track_id,
                )
            )
        else:
            items.append(
                PlaylistItemRecord(
                    path=item_path,
                    title=pending_title or item_path,
                    duration_seconds=pending_duration,
                    genre=pending_genre,
                    cover_url=pending_cover_url,
                )
            )
        pending_title = None
        pending_duration = None
        pending_genre = None
        pending_cover_url = None

    return PlaylistRecord(
        path=str(resolved_path),
        name=name,
        root_position=root_position,
        file_created_at=file_created_at(resolved_path),
        cover_svg=playlist_cover_svg(name),
        items=items,
    )


def read_playlist_text(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if path.suffix.casefold() == ".m3u":
        try:
            return data.decode("ascii")
        except UnicodeDecodeError:
            return None
    return data.decode("utf-8-sig", errors="replace")


def parse_extinf(value: str) -> tuple[float | None, str | None]:
    duration_text, separator, title = value.partition(",")
    duration = parse_playlist_duration(duration_text)
    resolved_title = title.strip() if separator and title.strip() else None
    return duration, resolved_title


def parse_playlist_duration(value: str) -> float | None:
    match = re.match(r"\s*(-?\d+(?:\.\d+)?)", value)
    if match is None:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def normalize_playlist_resource(value: str, playlist_path: Path) -> str:
    if is_url_resource(value):
        return value.strip()
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = playlist_path.parent / path
    return normalize_local_playlist_path(str(path))


def normalize_local_playlist_path(value: str) -> str:
    return str(Path(value).expanduser().resolve(strict=False))


def is_url_resource(value: str) -> bool:
    parsed = urlsplit(value.strip())
    return parsed.scheme.casefold() in {"http", "https"} and bool(parsed.netloc)


def scan_track(path: Path) -> TrackRecord:
    record = TrackRecord(
        path=str(path),
        file_created_at=file_created_at(path),
        file_type=path.suffix.lower().lstrip("."),
    )

    try:
        audio = MutagenFile(path, easy=False)
    except Exception as exc:
        record.scan_error = str(exc)
        return record

    try:
        easy_audio = MutagenFile(path, easy=True)
    except Exception:
        easy_audio = None

    if audio is None:
        return record

    tags = normalize_tags(getattr(audio, "tags", None), getattr(easy_audio, "tags", None))
    genres = collect_genres(tags)
    info = getattr(audio, "info", None)

    record.artist = first_value(tags, PRIMARY_TAG_FIELDS["artist"])
    record.album_artist = first_value(tags, PRIMARY_TAG_FIELDS["album_artist"])
    record.composer = first_value(tags, PRIMARY_TAG_FIELDS["composer"])
    if not (record.album_artist or record.artist):
        record.album_artist = UNKNOWN_METADATA_TAG
    record.album = first_value(tags, PRIMARY_TAG_FIELDS["album"]) or UNKNOWN_METADATA_TAG
    record.title = first_value(tags, PRIMARY_TAG_FIELDS["title"]) or fallback_track_title(path)
    record.work = first_value(tags, PRIMARY_TAG_FIELDS["work"])
    record.grouping = first_value(tags, PRIMARY_TAG_FIELDS["grouping"])
    record.movement_name = first_value(tags, PRIMARY_TAG_FIELDS["movement_name"])
    record.is_compilation = first_bool(tags, PRIMARY_TAG_FIELDS["is_compilation"])
    record.track_number = first_value(tags, PRIMARY_TAG_FIELDS["track_number"])
    record.disc_number = first_value(tags, PRIMARY_TAG_FIELDS["disc_number"])
    record.date = first_value(tags, PRIMARY_TAG_FIELDS["date"])
    if record.file_type in ITUNES_STORE_FILE_TYPES:
        record.itunes_store_track_id = first_numeric_value(tags, ("cnid",))
        record.itunes_store_album_id = first_numeric_value(tags, ("plid",))
    record.genres = genres or [UNKNOWN_METADATA_TAG]
    artwork_by_height = extract_preferred_artworks(
        audio,
        path,
        heights=(
            ARTWORK_THUMBNAIL_HEIGHT,
            ALBUM_ARTWORK_THUMBNAIL_HEIGHT,
        ),
    )
    record.artwork = artwork_by_height.get(ARTWORK_THUMBNAIL_HEIGHT)
    record.album_artwork = artwork_by_height.get(ALBUM_ARTWORK_THUMBNAIL_HEIGHT)
    record.duration_seconds = round(float(getattr(info, "length", 0.0)), 3) or None
    bitrate = getattr(info, "bitrate", None)
    record.bitrate = int(bitrate) if bitrate else None
    return record


def fallback_track_title(path: Path) -> str:
    return path.stem.strip() or UNKNOWN_METADATA_TAG


def write_track_audio_tags(
    path: Path,
    *,
    artist: str | None,
    album_artist: str | None,
    album: str | None,
    track_number: str | None,
    title: str | None,
    genre: str | None,
) -> None:
    resolved_artist = artist.strip() if artist else ""
    resolved_album_artist = album_artist.strip() if album_artist else ""
    resolved_album = album.strip() if album else ""
    resolved_track_number = track_number.strip() if track_number else ""
    resolved_title = title.strip() if title else ""
    resolved_genre = genre.strip() if genre else ""

    try:
        audio = MutagenFile(path, easy=True)
    except Exception as error:
        raise OSError(f"failed to open tags for {path}: {error}") from error

    if audio is None:
        raise OSError(f"unsupported or unreadable audio file: {path}")

    if getattr(audio, "tags", None) is None and hasattr(audio, "add_tags"):
        try:
            audio.add_tags()
        except Exception:
            pass

    try:
        if resolved_artist:
            audio["artist"] = [resolved_artist]
        else:
            delete_easy_tag(audio, "artist")

        if resolved_album_artist:
            audio["albumartist"] = [resolved_album_artist]
        else:
            delete_easy_tag(audio, "albumartist")

        if resolved_album:
            audio["album"] = [resolved_album]
        else:
            delete_easy_tag(audio, "album")

        if resolved_track_number:
            audio["tracknumber"] = [resolved_track_number]
        else:
            delete_easy_tag(audio, "tracknumber")

        if resolved_title:
            audio["title"] = [resolved_title]
        else:
            delete_easy_tag(audio, "title")

        if resolved_genre:
            audio["genre"] = [resolved_genre]
        else:
            delete_easy_tag(audio, "genre")

        audio.save()
    except Exception as error:
        raise OSError(f"failed to update tags for {path}: {error}") from error


def write_album_audio_tags(
    path: Path,
    *,
    album_artist: str,
    album: str,
    genre: str,
) -> None:
    resolved_album_artist = album_artist.strip()
    resolved_album = album.strip()
    resolved_genre = genre.strip()

    try:
        audio = MutagenFile(path, easy=True)
    except Exception as error:
        raise OSError(f"failed to open tags for {path}: {error}") from error

    if audio is None:
        raise OSError(f"unsupported or unreadable audio file: {path}")

    if getattr(audio, "tags", None) is None and hasattr(audio, "add_tags"):
        try:
            audio.add_tags()
        except Exception:
            pass

    try:
        audio["albumartist"] = [resolved_album_artist]
        audio["album"] = [resolved_album]
        audio["genre"] = [resolved_genre]
        audio.save()
    except Exception as error:
        raise OSError(f"failed to update tags for {path}: {error}") from error


def missing_required_tags(track: TrackRecord) -> list[str]:
    missing: list[str] = []
    if not (track.album_artist or track.artist):
        missing.append("artist")
    if not track.album:
        missing.append("album")
    if not track.title:
        missing.append("title")
    return missing


def normalize_tags(*tag_sets: object) -> dict[str, list[str]]:
    normalized: dict[str, list[str]] = {}
    for tags in tag_sets:
        if not tags:
            continue
        for key in tags.keys():
            normalized_key = canonicalize_key(str(key))
            if is_ignored_raw_tag_key(normalized_key):
                continue
            values = tags.get(key, [])
            flattened = normalize_values(values)
            if not flattened:
                continue
            bucket = normalized.setdefault(normalized_key, [])
            for value in flattened:
                if value not in bucket:
                    bucket.append(value)
    return normalized


def normalize_values(values: object) -> list[str]:
    if values is None:
        return []
    if hasattr(values, "text"):
        return normalize_values(values.text)
    if hasattr(values, "value"):
        return normalize_values(values.value)
    if isinstance(values, (str, bytes)):
        return [stringify_value(values)]
    if isinstance(values, tuple):
        return [stringify_value(values)]
    if isinstance(values, list | set):
        flattened: list[str] = []
        for value in values:
            flattened.extend(normalize_values(value))
        return [value for value in flattened if value]
    return [stringify_value(values)]


def stringify_value(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").strip()
    if isinstance(value, tuple) and len(value) == 2 and all(isinstance(item, int) for item in value):
        current, total = value
        return f"{current}/{total}" if total else str(current)
    return str(value).strip()


def canonicalize_key(key: str) -> str:
    cleaned = key.strip().casefold()
    compact = cleaned.replace(" ", "")
    return RAW_KEY_ALIASES.get(compact, cleaned)


def delete_easy_tag(audio: object, key: str) -> None:
    try:
        del audio[key]
    except KeyError:
        return


def first_value(tags: dict[str, list[str]], keys: Iterable[str]) -> str | None:
    for key in keys:
        values = tags.get(key.casefold())
        if values:
            return values[0]
    return None


def first_bool(tags: dict[str, list[str]], keys: Iterable[str]) -> bool:
    for key in keys:
        values = tags.get(key.casefold())
        if values:
            return any(is_truthy_tag_value(value) for value in values)
    return False


def first_numeric_value(tags: dict[str, list[str]], keys: Iterable[str]) -> str | None:
    value = first_value(tags, keys)
    if not value:
        return None
    digits = "".join(character for character in value if character.isdigit())
    return digits or None


def is_truthy_tag_value(value: str) -> bool:
    folded = value.strip().casefold()
    return folded in {"1", "true", "t", "yes", "y", "on"}


def collect_genres(tags: dict[str, list[str]]) -> list[str]:
    collected: dict[str, str] = {}
    for key, values in tags.items():
        if is_genre_key(key):
            for genre in normalize_genre_values(values):
                collected.setdefault(genre.casefold(), genre)
    return sorted(collected.values(), key=str.casefold)


def is_genre_key(key: str) -> bool:
    compact = key.replace(" ", "")
    return compact in GENRE_TAG_NAMES or "genre" in compact or "style" in compact


def extract_preferred_artworks(
    audio: object,
    path: Path,
    *,
    heights: Iterable[int],
) -> dict[int, TrackArtwork]:
    height_values = tuple(sorted({height for height in heights}))
    if not height_values:
        return {}

    artwork_by_height = extract_external_artworks(path, heights=height_values)
    if artwork_by_height:
        return artwork_by_height
    artwork = extract_artwork_source(audio)
    if artwork is not None:
        artwork_by_height = thumbnail_artworks(artwork, heights=height_values)
        if artwork_by_height:
            return artwork_by_height
    return {}


def extract_artwork_source(audio: object) -> TrackArtwork | None:
    for picture in getattr(audio, "pictures", []) or []:
        artwork = artwork_from_picture_object(picture)
        if artwork:
            return artwork

    tags = getattr(audio, "tags", None)
    if not tags:
        return None

    coverart_mime_type = tag_first_value(tags, "coverartmime")
    for key in tags.keys():
        normalized_key = canonicalize_key(str(key))
        if not is_artwork_key(normalized_key):
            continue
        values = tags.get(key, [])
        for value in iter_artwork_values(values):
            artwork = artwork_from_tag_value(
                value,
                key=normalized_key,
                mime_type_hint=coverart_mime_type,
            )
            if artwork:
                return artwork
    return None


def extract_external_artworks(
    path: Path,
    *,
    heights: Iterable[int],
) -> dict[int, TrackArtwork]:
    height_values = tuple(sorted({height for height in heights}))
    if not height_values:
        return {}
    return cached_external_artworks(str(path.parent), height_values)


@lru_cache(maxsize=4096)
def cached_external_artworks(
    directory: str,
    heights: tuple[int, ...],
) -> dict[int, TrackArtwork]:
    artwork = cached_external_artwork(directory)
    if artwork is None:
        return {}
    return thumbnail_artworks(artwork, heights=heights)


@lru_cache(maxsize=4096)
def cached_external_artwork(directory: str) -> TrackArtwork | None:
    for artwork_path in iter_external_artwork_paths(Path(directory)):
        artwork = artwork_from_image_path(artwork_path)
        if artwork:
            return artwork
    return None


def clear_external_artwork_caches() -> None:
    cached_external_artwork.cache_clear()
    cached_external_artworks.cache_clear()


def iter_external_artwork_paths(directory: Path) -> Iterable[Path]:
    yield from iter_named_artwork_files(directory, ALBUM_ARTWORK_NAMES)


def iter_named_artwork_files(directory: Path, names: Iterable[str]) -> Iterable[Path]:
    if not directory.is_dir():
        return

    seen: set[Path] = set()
    for name in names:
        for extension in ARTWORK_IMAGE_EXTENSIONS:
            candidate = directory / f"{name}{extension}"
            if candidate not in seen and candidate.is_file():
                seen.add(candidate)
                yield candidate

    normalized_names = {normalize_cache_component(name) for name in names}
    try:
        children = sorted(directory.iterdir(), key=lambda child: child.name.casefold())
    except OSError:
        return
    for child in children:
        if child in seen or not child.is_file() or child.suffix.casefold() not in ARTWORK_IMAGE_EXTENSIONS:
            continue
        if normalize_cache_component(child.stem) in normalized_names:
            seen.add(child)
            yield child


def artwork_from_image_path(path: Path) -> TrackArtwork | None:
    if path.suffix.casefold() not in ARTWORK_IMAGE_EXTENSIONS:
        return None
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if not data:
        return None
    return TrackArtwork(
        mime_type=sniff_image_mime_type(data, mimetypes.guess_type(path.name)[0]),
        data=data,
    )


def normalize_cache_component(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold().strip()


def tag_first_value(tags: object, key: str) -> str | None:
    if not hasattr(tags, "keys") or not hasattr(tags, "get"):
        return None
    values: object = []
    for tag_key in tags.keys():
        if canonicalize_key(str(tag_key)) == key:
            values = tags.get(tag_key, [])
            break
    normalized = normalize_values(values)
    return normalized[0] if normalized else None


def iter_artwork_values(values: object) -> Iterable[object]:
    if values is None:
        return []
    if isinstance(values, list | set | tuple):
        return values
    return [values]


def artwork_from_tag_value(
    value: object,
    *,
    key: str,
    mime_type_hint: str | None = None,
) -> TrackArtwork | None:
    artwork = artwork_from_picture_object(value)
    if artwork:
        return artwork

    if isinstance(value, bytes | bytearray):
        data = bytes(value)
        return TrackArtwork(mime_type=artwork_mime_type(value, data, mime_type_hint), data=data)

    if isinstance(value, str):
        data = decode_base64_data(value)
        if not data:
            return None
        if is_flac_picture_key(key):
            return parse_flac_picture_block(data)
        return TrackArtwork(mime_type=sniff_image_mime_type(data, mime_type_hint), data=data)

    data = getattr(value, "data", None)
    if isinstance(data, bytes | bytearray):
        raw_data = bytes(data)
        return TrackArtwork(
            mime_type=artwork_mime_type(value, raw_data, mime_type_hint),
            data=raw_data,
        )
    return None


def artwork_from_picture_object(value: object) -> TrackArtwork | None:
    data = getattr(value, "data", None)
    if not isinstance(data, bytes | bytearray):
        return None
    raw_data = bytes(data)
    mime_type = getattr(value, "mime", None)
    return TrackArtwork(mime_type=sniff_image_mime_type(raw_data, mime_type), data=raw_data)


def thumbnail_artwork(
    artwork: TrackArtwork,
    *,
    height: int = ARTWORK_THUMBNAIL_HEIGHT,
) -> TrackArtwork | None:
    return thumbnail_artworks(artwork, heights=(height,)).get(height)


def thumbnail_artworks(
    artwork: TrackArtwork,
    *,
    heights: Iterable[int],
) -> dict[int, TrackArtwork]:
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise RuntimeError("Pillow is required to extract cover art thumbnails.") from exc

    height_values = sorted({height for height in heights})
    if not height_values:
        return {}

    try:
        with Image.open(BytesIO(artwork.data)) as image:
            transposed = ImageOps.exif_transpose(image)
            if not transposed.width or not transposed.height:
                return {}
            has_alpha = image_has_alpha(transposed)
            converted = transposed.convert("RGBA" if has_alpha else "RGB")
            thumbnails: dict[int, TrackArtwork] = {}
            for height in height_values:
                target_height = max(1, height)
                width = max(1, round(transposed.width * target_height / transposed.height))
                resized = converted.resize(
                    (width, target_height),
                    Image.Resampling.LANCZOS,
                )
                output = BytesIO()
                if has_alpha:
                    resized.save(output, format="PNG", optimize=True)
                    thumbnails[height] = TrackArtwork(
                        mime_type="image/png",
                        data=output.getvalue(),
                    )
                    continue
                resized.save(output, format="JPEG", quality=85, optimize=True)
                thumbnails[height] = TrackArtwork(
                    mime_type="image/jpeg",
                    data=output.getvalue(),
                )
            return thumbnails
    except Exception:
        return {}


def image_has_alpha(image: object) -> bool:
    getbands = getattr(image, "getbands", None)
    if callable(getbands) and "A" in getbands():
        return True
    return getattr(image, "mode", "") == "P" and "transparency" in getattr(image, "info", {})


def artwork_mime_type(value: object, data: bytes, mime_type_hint: str | None = None) -> str:
    image_format = getattr(value, "imageformat", None)
    if image_format == 13:
        return "image/jpeg"
    if image_format == 14:
        return "image/png"
    return sniff_image_mime_type(data, mime_type_hint)


def sniff_image_mime_type(data: bytes, mime_type_hint: str | None = None) -> str:
    if mime_type_hint and mime_type_hint.startswith("image/"):
        return mime_type_hint
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def decode_base64_data(value: str) -> bytes | None:
    compact = "".join(value.split())
    if not compact:
        return None
    try:
        return base64.b64decode(compact, validate=True)
    except (binascii.Error, ValueError):
        return None


def parse_flac_picture_block(data: bytes) -> TrackArtwork | None:
    offset = 4
    mime_length, offset = read_flac_picture_uint(data, offset)
    if mime_length is None:
        return None
    mime_end = offset + mime_length
    if mime_end > len(data):
        return None
    mime_type = data[offset:mime_end].decode("utf-8", errors="replace")
    offset = mime_end

    description_length, offset = read_flac_picture_uint(data, offset)
    if description_length is None:
        return None
    offset += description_length
    if offset + 20 > len(data):
        return None
    offset += 16

    image_length, offset = read_flac_picture_uint(data, offset)
    if image_length is None:
        return None
    image_end = offset + image_length
    if image_end > len(data):
        return None
    image_data = data[offset:image_end]
    if not image_data:
        return None
    return TrackArtwork(
        mime_type=sniff_image_mime_type(image_data, mime_type),
        data=image_data,
    )


def read_flac_picture_uint(data: bytes, offset: int) -> tuple[int | None, int]:
    if offset + 4 > len(data):
        return None, offset
    return struct.unpack_from(">I", data, offset)[0], offset + 4


def is_artwork_key(key: str) -> bool:
    compact = compact_tag_key(key)
    return compact.startswith("apic") or compact in ARTWORK_TAG_COMPACTS


def is_ignored_raw_tag_key(key: str) -> bool:
    compact = compact_tag_key(key)
    return any(compact.startswith(prefix) for prefix in IGNORED_RAW_TAG_PREFIXES)


def is_flac_picture_key(key: str) -> bool:
    return compact_tag_key(key) == "metadatablockpicture"


def compact_tag_key(key: str) -> str:
    return key.replace(" ", "").replace("_", "").replace("-", "").casefold()
