from __future__ import annotations

import json
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from ..models import TrackArtwork
from .musicbrainz import MUSICBRAINZ_USER_AGENT
from ..scanner import sniff_image_mime_type


COVER_ART_ARCHIVE_API_ROOT = "https://coverartarchive.org"
COVER_ART_ARCHIVE_TIMEOUT_SECONDS = 20
COVER_ART_ARCHIVE_MAX_RESPONSE_BYTES = 25 * 1024 * 1024
COVER_ART_ARCHIVE_MAX_IMAGE_BYTES = 100 * 1024 * 1024


@dataclass(slots=True)
class CoverArtArchiveStats:
    metadata_api_calls: int = 0
    metadata_cached_calls: int = 0
    image_downloads: int = 0
    image_cached_calls: int = 0
    fetch_failures: int = 0
    missing_art: int = 0


class CoverArtArchiveClient:
    def __init__(
        self,
        *,
        stats: CoverArtArchiveStats,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.stats = stats
        self.log = log

    def fetch_metadata(self, entity_type: str, mbid: str) -> tuple[dict[str, object] | None, str]:
        url = build_cover_art_archive_url(entity_type, mbid)
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": MUSICBRAINZ_USER_AGENT,
            },
        )
        self.stats.metadata_api_calls += 1
        try:
            with urllib.request.urlopen(
                request,
                timeout=COVER_ART_ARCHIVE_TIMEOUT_SECONDS,
            ) as response:
                data = response.read(COVER_ART_ARCHIVE_MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                self.stats.missing_art += 1
                self._emit(f"Cover Art Archive has no {entity_type} art for {mbid}")
            else:
                self.stats.fetch_failures += 1
                self._emit(
                    f"Cover Art Archive lookup failed for {entity_type} {mbid} "
                    f"(HTTP {error.code})"
                )
            return None, url
        except (OSError, TimeoutError, urllib.error.URLError) as error:
            self.stats.fetch_failures += 1
            self._emit(f"Cover Art Archive lookup failed for {entity_type} {mbid}: {error}")
            return None, url

        if len(data) > COVER_ART_ARCHIVE_MAX_RESPONSE_BYTES:
            self.stats.fetch_failures += 1
            self._emit(
                f"Cover Art Archive response for {entity_type} {mbid} exceeded "
                f"{COVER_ART_ARCHIVE_MAX_RESPONSE_BYTES} bytes"
            )
            return None, url

        try:
            payload = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            self.stats.fetch_failures += 1
            self._emit(
                f"Cover Art Archive response for {entity_type} {mbid} was not valid JSON: {error}"
            )
            return None, url

        if not isinstance(payload, dict):
            self.stats.fetch_failures += 1
            self._emit(f"Cover Art Archive response for {entity_type} {mbid} was not an object")
            return None, url

        return payload, url

    def fetch_image(self, image_url: str) -> TrackArtwork | None:
        request = urllib.request.Request(
            image_url,
            headers={"User-Agent": MUSICBRAINZ_USER_AGENT},
        )
        self.stats.image_downloads += 1
        try:
            with urllib.request.urlopen(
                request,
                timeout=COVER_ART_ARCHIVE_TIMEOUT_SECONDS,
            ) as response:
                data = response.read(COVER_ART_ARCHIVE_MAX_IMAGE_BYTES + 1)
                content_type = response.headers.get_content_type()
        except urllib.error.HTTPError as error:
            self.stats.fetch_failures += 1
            self._emit(f"Cover Art Archive image fetch failed for {image_url} (HTTP {error.code})")
            return None
        except (OSError, TimeoutError, urllib.error.URLError) as error:
            self.stats.fetch_failures += 1
            self._emit(f"Cover Art Archive image fetch failed for {image_url}: {error}")
            return None

        if len(data) > COVER_ART_ARCHIVE_MAX_IMAGE_BYTES:
            self.stats.fetch_failures += 1
            self._emit(
                f"Cover Art Archive image {image_url} exceeded "
                f"{COVER_ART_ARCHIVE_MAX_IMAGE_BYTES} bytes"
            )
            return None
        if not data:
            self.stats.fetch_failures += 1
            self._emit(f"Cover Art Archive image {image_url} was empty")
            return None

        return TrackArtwork(
            mime_type=sniff_image_mime_type(data, content_type),
            data=data,
        )

    def _emit(self, message: str) -> None:
        if self.log is not None:
            self.log(message)


def get_cover_art_archive_entity(
    connection: sqlite3.Connection,
    client: CoverArtArchiveClient,
    *,
    entity_type: str,
    mbid: str,
) -> dict[str, object] | None:
    cached_payload = load_cached_cover_art_archive_entity(
        connection,
        entity_type=entity_type,
        mbid=mbid,
    )
    if cached_payload is not None:
        client.stats.metadata_cached_calls += 1
        return cached_payload

    payload, endpoint_url = client.fetch_metadata(entity_type, mbid)
    if payload is None:
        return None

    store_cover_art_archive_entity(
        connection,
        entity_type=entity_type,
        mbid=mbid,
        endpoint_url=endpoint_url,
        payload=payload,
    )
    return payload


def get_cover_art_archive_image(
    connection: sqlite3.Connection,
    client: CoverArtArchiveClient,
    *,
    image_url: str,
) -> TrackArtwork | None:
    cached_artwork = load_cached_cover_art_archive_image(connection, image_url=image_url)
    if cached_artwork is not None:
        client.stats.image_cached_calls += 1
        return cached_artwork

    artwork = client.fetch_image(image_url)
    if artwork is None:
        return None

    store_cover_art_archive_image(connection, image_url=image_url, artwork=artwork)
    return artwork


def load_cached_cover_art_archive_entity(
    connection: sqlite3.Connection,
    *,
    entity_type: str,
    mbid: str,
) -> dict[str, object] | None:
    row = connection.execute(
        """
        SELECT response_json
        FROM cover_art_archive_entity_cache
        WHERE entity_type = ? AND mbid = ?
        """,
        (entity_type, mbid),
    ).fetchone()
    if row is None:
        return None
    try:
        payload = json.loads(str(row["response_json"]))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def store_cover_art_archive_entity(
    connection: sqlite3.Connection,
    *,
    entity_type: str,
    mbid: str,
    endpoint_url: str,
    payload: dict[str, object],
) -> None:
    connection.execute(
        """
        INSERT OR REPLACE INTO cover_art_archive_entity_cache (
            entity_type,
            mbid,
            fetched_at,
            endpoint_url,
            response_json
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            entity_type,
            mbid,
            datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            endpoint_url,
            json.dumps(payload, sort_keys=True, separators=(",", ":")),
        ),
    )


def load_cached_cover_art_archive_image(
    connection: sqlite3.Connection,
    *,
    image_url: str,
) -> TrackArtwork | None:
    row = connection.execute(
        """
        SELECT mime_type, data
        FROM cover_art_archive_image_cache
        WHERE image_url = ?
        """,
        (image_url,),
    ).fetchone()
    if row is None:
        return None
    return TrackArtwork(mime_type=str(row["mime_type"]), data=bytes(row["data"]))


def store_cover_art_archive_image(
    connection: sqlite3.Connection,
    *,
    image_url: str,
    artwork: TrackArtwork,
) -> None:
    connection.execute(
        """
        INSERT OR REPLACE INTO cover_art_archive_image_cache (
            image_url,
            fetched_at,
            mime_type,
            data
        ) VALUES (?, ?, ?, ?)
        """,
        (
            image_url,
            datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            artwork.mime_type,
            artwork.data,
        ),
    )


def front_image_url(payload: dict[str, object]) -> str | None:
    images = payload.get("images")
    if not isinstance(images, list):
        return None

    image_rows = [image for image in images if isinstance(image, dict)]
    for prefer_approved in (True, False):
        for image in image_rows:
            if prefer_approved and image.get("approved") is False:
                continue
            if not is_front_image(image):
                continue
            image_url = image.get("image")
            if isinstance(image_url, str) and image_url.startswith(("http://", "https://")):
                return image_url
    return None


def is_front_image(image: dict[object, object]) -> bool:
    if image.get("front") is True:
        return True
    types = image.get("types")
    if not isinstance(types, list):
        return False
    return any(isinstance(value, str) and value.casefold() == "front" for value in types)


def build_cover_art_archive_url(entity_type: str, mbid: str) -> str:
    return (
        f"{COVER_ART_ARCHIVE_API_ROOT}/"
        f"{entity_type}/{urllib.parse.quote(mbid, safe='')}/"
    )
