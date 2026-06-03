from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from .metadata import (
    METADATA_ENTITY_RELEASE,
    METADATA_ENTITY_RELEASE_GROUP,
    METADATA_PROVIDER_MUSICBRAINZ,
    AlbumMetadataLink,
    AlbumMetadataTrackLink,
    album_metadata_link_for_album_id,
    delete_album_metadata_track_links,
    load_album_metadata_links,
    load_album_metadata_track_links,
    store_album_metadata_link,
    store_album_metadata_track_link,
)


MUSICBRAINZ_API_ROOT = "https://musicbrainz.org/ws/2"
MUSICBRAINZ_USER_AGENT = "kukicha/0.1.0 ( https://cconroy.com/kukicha )"
MUSICBRAINZ_TIMEOUT_SECONDS = 20
MUSICBRAINZ_MAX_RESPONSE_BYTES = 25 * 1024 * 1024
MUSICBRAINZ_MIN_REQUEST_INTERVAL_SECONDS = 1.0
MUSICBRAINZ_RATE_LIMIT_STATUS_CODES = {429, 503}

RELATIONSHIP_INCLUDES = (
    "area-rels",
    "artist-rels",
    "event-rels",
    "genre-rels",
    "instrument-rels",
    "label-rels",
    "place-rels",
    "recording-rels",
    "release-rels",
    "release-group-rels",
    "series-rels",
    "url-rels",
    "work-rels",
)
RELEASE_INCLUDES = (
    "artist-credits",
    "collections",
    "labels",
    "recordings",
    "release-groups",
    "media",
    "discids",
    "isrcs",
    "aliases",
    "annotation",
    "tags",
    "genres",
    "recording-level-rels",
    "release-group-level-rels",
    "work-level-rels",
    *RELATIONSHIP_INCLUDES,
)
RELEASE_GROUP_INCLUDES = (
    "artist-credits",
    "releases",
    "media",
    "discids",
    "aliases",
    "annotation",
    "tags",
    "genres",
    "ratings",
    *RELATIONSHIP_INCLUDES,
)
MUSICBRAINZ_LOOKUP_INCLUDES = {
    "release": RELEASE_INCLUDES,
    "release-group": RELEASE_GROUP_INCLUDES,
}
_UNCHANGED = object()
MUSICBRAINZ_MBID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


MusicBrainzAlbumLink = AlbumMetadataLink
MusicBrainzTrackLink = AlbumMetadataTrackLink


@dataclass(slots=True)
class MusicBrainzLookupStats:
    api_calls: int = 0
    cached_calls: int = 0
    rate_limit_retries: int = 0
    fetch_failures: int = 0


class MusicBrainzClient:
    def __init__(
        self,
        *,
        stats: MusicBrainzLookupStats,
        log: Callable[[str], None] | None = None,
        min_interval_seconds: float = MUSICBRAINZ_MIN_REQUEST_INTERVAL_SECONDS,
        max_retries: int = 5,
    ) -> None:
        self.stats = stats
        self.log = log
        self.min_interval_seconds = min_interval_seconds
        self.max_retries = max_retries
        self._last_request_at = 0.0

    def fetch_lookup(self, entity_type: str, mbid: str) -> tuple[dict[str, object] | None, str]:
        url = build_lookup_url(entity_type, mbid)
        backoff_seconds = 2.0
        attempts = 0

        while True:
            self._wait_for_local_rate_limit()
            request = urllib.request.Request(
                url,
                headers={
                    "Accept": "application/json",
                    "User-Agent": MUSICBRAINZ_USER_AGENT,
                },
            )
            self._last_request_at = time.monotonic()
            self.stats.api_calls += 1
            try:
                with urllib.request.urlopen(
                    request,
                    timeout=MUSICBRAINZ_TIMEOUT_SECONDS,
                ) as response:
                    data = response.read(MUSICBRAINZ_MAX_RESPONSE_BYTES + 1)
            except urllib.error.HTTPError as error:
                if error.code in MUSICBRAINZ_RATE_LIMIT_STATUS_CODES and attempts < self.max_retries:
                    attempts += 1
                    self.stats.rate_limit_retries += 1
                    delay = retry_delay_seconds(error, default=backoff_seconds)
                    self._emit(
                        "MusicBrainz rate limit reached "
                        f"(HTTP {error.code}) for {entity_type} {mbid}; "
                        f"retrying in {delay:.1f}s"
                    )
                    time.sleep(delay)
                    backoff_seconds = min(backoff_seconds * 2.0, 60.0)
                    continue

                self.stats.fetch_failures += 1
                if error.code == 404:
                    self._emit(f"MusicBrainz {entity_type} {mbid} was not found")
                else:
                    self._emit(
                        f"MusicBrainz lookup failed for {entity_type} {mbid} "
                        f"(HTTP {error.code})"
                    )
                return None, url
            except (OSError, TimeoutError, urllib.error.URLError) as error:
                self.stats.fetch_failures += 1
                self._emit(f"MusicBrainz lookup failed for {entity_type} {mbid}: {error}")
                return None, url

            if len(data) > MUSICBRAINZ_MAX_RESPONSE_BYTES:
                self.stats.fetch_failures += 1
                self._emit(
                    f"MusicBrainz response for {entity_type} {mbid} exceeded "
                    f"{MUSICBRAINZ_MAX_RESPONSE_BYTES} bytes"
                )
                return None, url

            try:
                payload = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                self.stats.fetch_failures += 1
                self._emit(f"MusicBrainz response for {entity_type} {mbid} was not valid JSON: {error}")
                return None, url

            if not isinstance(payload, dict):
                self.stats.fetch_failures += 1
                self._emit(f"MusicBrainz response for {entity_type} {mbid} was not an object")
                return None, url

            return payload, url

    def _wait_for_local_rate_limit(self) -> None:
        if not self._last_request_at:
            return
        elapsed = time.monotonic() - self._last_request_at
        delay = self.min_interval_seconds - elapsed
        if delay > 0:
            time.sleep(delay)

    def _emit(self, message: str) -> None:
        if self.log is not None:
            self.log(message)


def load_album_musicbrainz_links(
    connection: sqlite3.Connection,
) -> dict[str, tuple[MusicBrainzAlbumLink, ...]]:
    return load_album_metadata_links(
        connection,
        provider=METADATA_PROVIDER_MUSICBRAINZ,
    )


def album_musicbrainz_link_for_album_id(
    connection: sqlite3.Connection,
    album_id: str,
) -> MusicBrainzAlbumLink | None:
    return album_metadata_link_for_album_id(
        connection,
        album_id,
        provider=METADATA_PROVIDER_MUSICBRAINZ,
    )


def store_album_musicbrainz_link(
    connection: sqlite3.Connection,
    file_album_id: str,
    *,
    release_mbid: str | None | object = _UNCHANGED,
    release_group_mbid: str | None | object = _UNCHANGED,
) -> None:
    if release_mbid is _UNCHANGED or release_group_mbid is _UNCHANGED:
        existing = connection.execute(
            """
            SELECT entity_type, entity_id, related_entity_type, related_entity_id
            FROM album_metadata_links
            WHERE file_album_id = ? AND provider = 'musicbrainz'
            ORDER BY entity_type = 'release' DESC, entity_id, related_entity_id
            LIMIT 1
            """,
            (file_album_id,),
        ).fetchone()
    else:
        existing = None

    if existing and release_mbid is _UNCHANGED:
        next_release_mbid = (
            clean_mbid(existing["entity_id"])
            if existing["entity_type"] == METADATA_ENTITY_RELEASE
            else None
        )
    else:
        next_release_mbid = clean_mbid(release_mbid)

    if existing and release_group_mbid is _UNCHANGED:
        next_release_group_mbid = (
            clean_mbid(existing["entity_id"])
            if existing["entity_type"] == METADATA_ENTITY_RELEASE_GROUP
            else clean_mbid(existing["related_entity_id"])
        )
    else:
        next_release_group_mbid = clean_mbid(release_group_mbid)

    if not next_release_mbid and not next_release_group_mbid:
        connection.execute(
            """
            DELETE FROM album_metadata_links
            WHERE file_album_id = ? AND provider = 'musicbrainz'
            """,
            (file_album_id,),
        )
        return

    if next_release_mbid:
        connection.execute(
            """
            DELETE FROM album_metadata_links
            WHERE file_album_id = ?
                AND provider = 'musicbrainz'
                AND entity_type = 'release'
                AND entity_id = ?
                AND (
                    COALESCE(related_entity_id, '') = ''
                    OR related_entity_id = ?
                )
            """,
            (file_album_id, next_release_mbid, next_release_group_mbid or ""),
        )
        store_album_metadata_link(
            connection,
            file_album_id,
            provider=METADATA_PROVIDER_MUSICBRAINZ,
            entity_type=METADATA_ENTITY_RELEASE,
            entity_id=next_release_mbid,
            related_entity_type=(
                METADATA_ENTITY_RELEASE_GROUP if next_release_group_mbid else None
            ),
            related_entity_id=next_release_group_mbid,
        )
        return

    store_album_metadata_link(
        connection,
        file_album_id,
        provider=METADATA_PROVIDER_MUSICBRAINZ,
        entity_type=METADATA_ENTITY_RELEASE_GROUP,
        entity_id=next_release_group_mbid,
    )


def load_album_musicbrainz_track_links(
    connection: sqlite3.Connection,
    paths: Iterable[str],
) -> dict[str, MusicBrainzTrackLink]:
    return load_album_metadata_track_links(
        connection,
        paths,
        provider=METADATA_PROVIDER_MUSICBRAINZ,
    )


def store_album_musicbrainz_track_link(
    connection: sqlite3.Connection,
    path: str,
    file_album_id: str,
    *,
    release_mbid: str | None,
    release_group_mbid: str | None,
) -> None:
    cleaned_release_mbid = clean_mbid(release_mbid)
    cleaned_release_group_mbid = clean_mbid(release_group_mbid)
    store_album_metadata_track_link(
        connection,
        path,
        file_album_id,
        provider=METADATA_PROVIDER_MUSICBRAINZ,
        entity_type=(
            METADATA_ENTITY_RELEASE
            if cleaned_release_mbid
            else METADATA_ENTITY_RELEASE_GROUP
        ),
        entity_id=cleaned_release_mbid or cleaned_release_group_mbid,
        related_entity_type=(
            METADATA_ENTITY_RELEASE_GROUP
            if cleaned_release_mbid and cleaned_release_group_mbid
            else None
        ),
        related_entity_id=cleaned_release_group_mbid if cleaned_release_mbid else None,
    )


def delete_album_musicbrainz_track_links(
    connection: sqlite3.Connection,
    paths: Iterable[str],
) -> None:
    delete_album_metadata_track_links(connection, paths)


def store_album_musicbrainz_release_group_if_missing(
    connection: sqlite3.Connection,
    file_album_id: str,
    *,
    release_mbid: str | None = None,
    release_group_mbid: str,
) -> bool:
    cleaned_release_mbid = clean_mbid(release_mbid) or ""
    cleaned_release_group_mbid = clean_mbid(release_group_mbid)
    if not cleaned_release_group_mbid:
        return False
    current = connection.execute(
        """
        SELECT related_entity_id
        FROM album_metadata_links
        WHERE file_album_id = ?
            AND provider = 'musicbrainz'
            AND entity_type = 'release'
            AND entity_id = ?
        """,
        (file_album_id, cleaned_release_mbid),
    ).fetchone()
    if current is not None and clean_mbid(current["related_entity_id"]):
        return False
    if current is not None:
        connection.execute(
            """
            UPDATE album_metadata_links
            SET related_entity_type = 'release-group',
                related_entity_id = ?
            WHERE file_album_id = ?
                AND provider = 'musicbrainz'
                AND entity_type = 'release'
                AND entity_id = ?
            """,
            (cleaned_release_group_mbid, file_album_id, cleaned_release_mbid),
        )
        return True

    store_album_musicbrainz_link(
        connection,
        file_album_id,
        release_mbid=cleaned_release_mbid,
        release_group_mbid=cleaned_release_group_mbid,
    )
    return True


def get_musicbrainz_entity(
    connection: sqlite3.Connection,
    client: MusicBrainzClient,
    *,
    entity_type: str,
    mbid: str,
) -> dict[str, object] | None:
    cached_payload = load_cached_musicbrainz_entity(connection, entity_type=entity_type, mbid=mbid)
    if cached_payload is not None:
        client.stats.cached_calls += 1
        return cached_payload

    payload, endpoint_url = client.fetch_lookup(entity_type, mbid)
    if payload is None:
        return None

    store_musicbrainz_entity(
        connection,
        entity_type=entity_type,
        mbid=mbid,
        endpoint_url=endpoint_url,
        payload=payload,
    )
    return payload


def load_cached_musicbrainz_entity(
    connection: sqlite3.Connection,
    *,
    entity_type: str,
    mbid: str,
) -> dict[str, object] | None:
    row = connection.execute(
        """
        SELECT response_json
        FROM musicbrainz_entity_cache
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


def store_musicbrainz_entity(
    connection: sqlite3.Connection,
    *,
    entity_type: str,
    mbid: str,
    endpoint_url: str,
    payload: dict[str, object],
) -> None:
    connection.execute(
        """
        INSERT OR REPLACE INTO musicbrainz_entity_cache (
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


def musicbrainz_genres(payload: dict[str, object]) -> list[str]:
    raw_genres = payload.get("genres")
    if not isinstance(raw_genres, list):
        return []

    genres: list[tuple[str, int | None]] = []
    seen: set[str] = set()
    for raw_genre in raw_genres:
        if not isinstance(raw_genre, dict):
            continue
        name = raw_genre.get("name")
        if not isinstance(name, str):
            continue
        genre = name.strip()
        key = genre.casefold()
        if not genre or key in seen:
            continue
        seen.add(key)
        genres.append((genre, musicbrainz_genre_count(raw_genre.get("count"))))

    strong_genres = [
        genre
        for genre, count in genres
        if count is None or count > 1
    ]
    if strong_genres and len(strong_genres) < len(genres):
        return strong_genres
    return [genre for genre, _count in genres]


def musicbrainz_genre_count(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def musicbrainz_release_group_mbid(payload: dict[str, object]) -> str | None:
    release_group = payload.get("release-group")
    if not isinstance(release_group, dict):
        return None
    return clean_mbid(release_group.get("id"))


def musicbrainz_release_fingerprint(
    release: dict[str, object],
    *,
    fallback_release_mbid: str | None = None,
    bits: int = 12,
) -> str:
    source = musicbrainz_release_variant_source(
        release,
        fallback_release_mbid=fallback_release_mbid,
    )
    digest = hashlib.sha1(source.encode("utf-8")).hexdigest()
    return digest[: bits // 4]


def musicbrainz_release_variant_source(
    release: dict[str, object],
    *,
    fallback_release_mbid: str | None = None,
) -> str:
    country = normalize_musicbrainz_variant_value(release.get("country")) or "unknown-country"
    year = musicbrainz_release_year(release)
    primary_format = musicbrainz_release_primary_format(release)
    external_id = (
        normalize_musicbrainz_variant_value(release.get("barcode"))
        or musicbrainz_release_catalog_number(release)
        or normalize_musicbrainz_variant_value(release.get("id"))[:8]
        or normalize_musicbrainz_variant_value(fallback_release_mbid)[:8]
        or "unknown-id"
    )
    return f"{country}:{year}:{primary_format}:{external_id}"


def normalize_musicbrainz_variant_value(value: object) -> str:
    if not value:
        return ""
    return str(value).strip().lower()


def musicbrainz_release_year(release: dict[str, object]) -> str:
    date = release.get("date")
    if not isinstance(date, str):
        return "unknown-year"
    return date[:4] if len(date) >= 4 else "unknown-year"


def musicbrainz_release_primary_format(release: dict[str, object]) -> str:
    media = release.get("media")
    if not isinstance(media, list) or not media:
        return "unknown-format"
    first = media[0]
    if not isinstance(first, dict):
        return "unknown-format"
    return normalize_musicbrainz_variant_value(first.get("format")) or "unknown-format"


def musicbrainz_release_catalog_number(release: dict[str, object]) -> str | None:
    label_info = release.get("label-info")
    if not isinstance(label_info, list):
        return None
    for item in label_info:
        if not isinstance(item, dict):
            continue
        catalog_number = normalize_musicbrainz_variant_value(item.get("catalog-number"))
        if catalog_number:
            return catalog_number
    return None


def build_lookup_url(entity_type: str, mbid: str) -> str:
    includes = MUSICBRAINZ_LOOKUP_INCLUDES[entity_type]
    query = urllib.parse.urlencode(
        {
            "inc": "+".join(includes),
            "fmt": "json",
        },
        safe="+",
    )
    return f"{MUSICBRAINZ_API_ROOT}/{entity_type}/{urllib.parse.quote(mbid, safe='')}?{query}"


def retry_delay_seconds(error: urllib.error.HTTPError, *, default: float) -> float:
    retry_after = error.headers.get("Retry-After") if error.headers else None
    if retry_after:
        try:
            return max(float(retry_after), 0.0)
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(retry_after)
            except (TypeError, ValueError):
                pass
            else:
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                return max((retry_at - datetime.now(timezone.utc)).total_seconds(), 0.0)
    return default


def clean_mbid(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    mbid = value.strip()
    return mbid or None


def normalize_musicbrainz_mbid(value: str, *, entity_type: str) -> str | None:
    stripped = value.strip()
    if not stripped:
        return None

    expected_path_part = "release" if entity_type == "release" else "release-group"
    parts = urllib.parse.urlsplit(stripped)
    if parts.scheme or parts.netloc:
        path_parts = [part for part in parts.path.split("/") if part]
        if len(path_parts) < 2 or path_parts[0] != expected_path_part:
            label = "release" if entity_type == "release" else "release group"
            raise ValueError(f"Expected a MusicBrainz {label} URL or MBID.")
        stripped = path_parts[1]

    if not MUSICBRAINZ_MBID_PATTERN.match(stripped):
        raise ValueError("MusicBrainz IDs must be UUID-form MBIDs.")
    return stripped.lower()
