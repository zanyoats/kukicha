from __future__ import annotations

import json
import re
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


ITUNES_LOOKUP_API_ROOT = "https://itunes.apple.com/lookup"
ITUNES_TIMEOUT_SECONDS = 20
ITUNES_MAX_RESPONSE_BYTES = 25 * 1024 * 1024
ITUNES_MAX_IMAGE_BYTES = 100 * 1024 * 1024
ITUNES_ARTWORK_TARGET_SIZE = 3000
_ARTWORK_URL_KEY_PATTERN = re.compile(r"^artworkUrl(?P<size>\d+)$")
_FIXED_ARTWORK_SEGMENT_PATTERN = re.compile(
    r"^(?P<width>\d+)x(?P<height>\d+)(?P<suffix>(?:bb|sr)?(?:-[^.]+)?)\.(?P<extension>jpe?g|png|webp)$",
    re.IGNORECASE,
)


@dataclass(slots=True)
class ItunesLookupStats:
    lookup_api_calls: int = 0
    lookup_cached_calls: int = 0
    fetch_failures: int = 0
    missing_art: int = 0


@dataclass(frozen=True, slots=True)
class ItunesLookupCandidate:
    lookup_kind: str
    lookup_id: str

    @property
    def cache_key(self) -> str:
        return f"{self.lookup_kind}:{self.lookup_id}"


@dataclass(slots=True)
class CachedItunesLookupImage:
    result_kind: str
    artwork: TrackArtwork | None = None


class ItunesLookupClient:
    def __init__(
        self,
        *,
        stats: ItunesLookupStats,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.stats = stats
        self.log = log

    def fetch_lookup(
        self,
        candidate: ItunesLookupCandidate,
    ) -> tuple[dict[str, object] | None, str]:
        url = build_itunes_lookup_url(candidate.lookup_id)
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": MUSICBRAINZ_USER_AGENT,
            },
        )
        self.stats.lookup_api_calls += 1
        try:
            with urllib.request.urlopen(
                request,
                timeout=ITUNES_TIMEOUT_SECONDS,
            ) as response:
                data = response.read(ITUNES_MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                self.stats.missing_art += 1
                self._emit(
                    f"iTunes lookup returned no results for {candidate.lookup_kind} "
                    f"{candidate.lookup_id}"
                )
            else:
                self.stats.fetch_failures += 1
                self._emit(
                    f"iTunes lookup failed for {candidate.lookup_kind} {candidate.lookup_id} "
                    f"(HTTP {error.code})"
                )
            return None, url
        except (OSError, TimeoutError, urllib.error.URLError) as error:
            self.stats.fetch_failures += 1
            self._emit(
                f"iTunes lookup failed for {candidate.lookup_kind} {candidate.lookup_id}: {error}"
            )
            return None, url

        if len(data) > ITUNES_MAX_RESPONSE_BYTES:
            self.stats.fetch_failures += 1
            self._emit(
                f"iTunes lookup response for {candidate.lookup_kind} {candidate.lookup_id} "
                f"exceeded {ITUNES_MAX_RESPONSE_BYTES} bytes"
            )
            return None, url

        try:
            payload = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            self.stats.fetch_failures += 1
            self._emit(
                f"iTunes lookup response for {candidate.lookup_kind} {candidate.lookup_id} "
                f"was not valid JSON: {error}"
            )
            return None, url

        if not isinstance(payload, dict):
            self.stats.fetch_failures += 1
            self._emit(
                f"iTunes lookup response for {candidate.lookup_kind} {candidate.lookup_id} "
                "was not an object"
            )
            return None, url

        return payload, url

    def fetch_artwork(self, image_url: str) -> tuple[TrackArtwork | None, str | None]:
        for candidate_url in candidate_artwork_urls(image_url):
            artwork = self._fetch_image(candidate_url)
            if artwork is not None:
                return artwork, candidate_url
        self.stats.fetch_failures += 1
        self._emit(f"iTunes artwork fetch failed for {image_url}")
        return None, None

    def _fetch_image(self, image_url: str) -> TrackArtwork | None:
        request = urllib.request.Request(
            image_url,
            headers={"User-Agent": MUSICBRAINZ_USER_AGENT},
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=ITUNES_TIMEOUT_SECONDS,
            ) as response:
                data = response.read(ITUNES_MAX_IMAGE_BYTES + 1)
                content_type = response.headers.get_content_type()
        except (OSError, TimeoutError, urllib.error.URLError, urllib.error.HTTPError):
            return None

        if len(data) > ITUNES_MAX_IMAGE_BYTES or not data:
            return None

        return TrackArtwork(
            mime_type=sniff_image_mime_type(data, content_type),
            data=data,
        )

    def _emit(self, message: str) -> None:
        if self.log is not None:
            self.log(message)


def get_itunes_lookup_image(
    connection: sqlite3.Connection,
    client: ItunesLookupClient,
    *,
    candidate: ItunesLookupCandidate,
) -> TrackArtwork | None:
    cached_result = load_cached_itunes_lookup_image(connection, cache_key=candidate.cache_key)
    if cached_result is not None:
        client.stats.lookup_cached_calls += 1
        return cached_result.artwork

    payload, lookup_url = client.fetch_lookup(candidate)
    if payload is None:
        return None

    image_url = lookup_artwork_url(payload)
    if image_url is None:
        client.stats.missing_art += 1
        client._emit(
            f"iTunes lookup returned no artwork for {candidate.lookup_kind} {candidate.lookup_id}"
        )
        store_missing_itunes_lookup_image(
            connection,
            candidate=candidate,
            lookup_url=lookup_url,
        )
        return None

    artwork, resolved_image_url = client.fetch_artwork(image_url)
    if artwork is None or resolved_image_url is None:
        return None

    store_itunes_lookup_image(
        connection,
        candidate=candidate,
        lookup_url=lookup_url,
        artwork_url=resolved_image_url,
        artwork=artwork,
    )
    return artwork


def load_cached_itunes_lookup_image(
    connection: sqlite3.Connection,
    *,
    cache_key: str,
) -> CachedItunesLookupImage | None:
    row = connection.execute(
        """
        SELECT result_kind, mime_type, data
        FROM itunes_lookup_image_cache
        WHERE cache_key = ?
        """,
        (cache_key,),
    ).fetchone()
    if row is None:
        return None
    result_kind = str(row["result_kind"] or "hit")
    if result_kind == "missing":
        return CachedItunesLookupImage(result_kind=result_kind)
    return CachedItunesLookupImage(
        result_kind=result_kind,
        artwork=TrackArtwork(mime_type=str(row["mime_type"]), data=bytes(row["data"])),
    )


def store_itunes_lookup_image(
    connection: sqlite3.Connection,
    *,
    candidate: ItunesLookupCandidate,
    lookup_url: str,
    artwork_url: str,
    artwork: TrackArtwork,
) -> None:
    connection.execute(
        """
        INSERT OR REPLACE INTO itunes_lookup_image_cache (
            cache_key,
            lookup_kind,
            lookup_id,
            result_kind,
            fetched_at,
            lookup_url,
            artwork_url,
            mime_type,
            data
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            candidate.cache_key,
            candidate.lookup_kind,
            candidate.lookup_id,
            "hit",
            datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            lookup_url,
            artwork_url,
            artwork.mime_type,
            artwork.data,
        ),
    )


def store_missing_itunes_lookup_image(
    connection: sqlite3.Connection,
    *,
    candidate: ItunesLookupCandidate,
    lookup_url: str,
) -> None:
    connection.execute(
        """
        INSERT OR REPLACE INTO itunes_lookup_image_cache (
            cache_key,
            lookup_kind,
            lookup_id,
            result_kind,
            fetched_at,
            lookup_url,
            artwork_url,
            mime_type,
            data
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            candidate.cache_key,
            candidate.lookup_kind,
            candidate.lookup_id,
            "missing",
            datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            lookup_url,
            "",
            "",
            b"",
        ),
    )


def lookup_artwork_url(payload: dict[str, object]) -> str | None:
    results = payload.get("results")
    if not isinstance(results, list):
        return None

    scored_results: list[tuple[tuple[int, int], str]] = []
    for raw_result in results:
        if not isinstance(raw_result, dict):
            continue
        image_url, image_size = largest_artwork_url(raw_result)
        if image_url is None:
            continue
        wrapper_type = raw_result.get("wrapperType")
        has_collection_id = "collectionId" in raw_result
        rank = (
            0 if wrapper_type == "collection" else 1 if has_collection_id else 2,
            -image_size,
        )
        scored_results.append((rank, image_url))
    if not scored_results:
        return None
    scored_results.sort(key=lambda item: item[0])
    return scored_results[0][1]


def largest_artwork_url(result: dict[str, object]) -> tuple[str | None, int]:
    largest_size = -1
    largest_url: str | None = None
    for key, value in result.items():
        if not isinstance(key, str) or not isinstance(value, str):
            continue
        match = _ARTWORK_URL_KEY_PATTERN.match(key)
        if match is None or not value.startswith(("http://", "https://")):
            continue
        size = int(match.group("size"))
        if size > largest_size:
            largest_size = size
            largest_url = value
    return largest_url, largest_size


def candidate_artwork_urls(image_url: str) -> tuple[str, ...]:
    enlarged_url = high_resolution_artwork_url(image_url)
    if enlarged_url == image_url:
        return (image_url,)
    return (enlarged_url, image_url)


def high_resolution_artwork_url(
    image_url: str,
    *,
    size: int = ITUNES_ARTWORK_TARGET_SIZE,
) -> str:
    if not image_url.startswith(("http://", "https://")):
        return image_url

    parsed = urllib.parse.urlsplit(image_url)
    rewritten_path = parsed.path.replace("{w}", str(size)).replace("{h}", str(size))
    if rewritten_path != parsed.path:
        return urllib.parse.urlunsplit(parsed._replace(path=rewritten_path))

    directory, separator, filename = parsed.path.rpartition("/")
    if not separator:
        return image_url
    match = _FIXED_ARTWORK_SEGMENT_PATTERN.match(filename)
    if match is None:
        return image_url

    rewritten_filename = (
        f"{size}x{size}{match.group('suffix')}.{match.group('extension')}"
    )
    rewritten_path = f"{directory}/{rewritten_filename}" if directory else f"/{rewritten_filename}"
    return urllib.parse.urlunsplit(parsed._replace(path=rewritten_path))


def build_itunes_lookup_url(lookup_id: str) -> str:
    query = urllib.parse.urlencode({"id": lookup_id, "media": "music"})
    return f"{ITUNES_LOOKUP_API_ROOT}?{query}"
