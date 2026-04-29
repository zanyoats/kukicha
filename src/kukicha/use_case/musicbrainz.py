from __future__ import annotations

import json
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


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


@dataclass(slots=True)
class MusicBrainzAlbumLink:
    album_id: str
    release_mbid: str | None = None
    release_group_mbid: str | None = None

    @property
    def has_identifier(self) -> bool:
        return bool(self.release_mbid or self.release_group_mbid)


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
) -> dict[str, MusicBrainzAlbumLink]:
    links: dict[str, MusicBrainzAlbumLink] = {}
    for row in connection.execute(
        """
        SELECT album_id, release_mbid, release_group_mbid
        FROM album_musicbrainz_links
        WHERE COALESCE(release_mbid, '') != ''
            OR COALESCE(release_group_mbid, '') != ''
        """
    ):
        album_id = str(row["album_id"])
        links[album_id] = MusicBrainzAlbumLink(
            album_id=album_id,
            release_mbid=clean_mbid(row["release_mbid"]),
            release_group_mbid=clean_mbid(row["release_group_mbid"]),
        )
    return links


def store_album_musicbrainz_link(
    connection: sqlite3.Connection,
    album_id: str,
    *,
    release_mbid: str | None | object = _UNCHANGED,
    release_group_mbid: str | None | object = _UNCHANGED,
) -> None:
    existing = connection.execute(
        """
        SELECT release_mbid, release_group_mbid
        FROM album_musicbrainz_links
        WHERE album_id = ?
        """,
        (album_id,),
    ).fetchone()

    next_release_mbid = (
        clean_mbid(existing["release_mbid"])
        if existing and release_mbid is _UNCHANGED
        else clean_mbid(release_mbid)
    )
    next_release_group_mbid = (
        clean_mbid(existing["release_group_mbid"])
        if existing and release_group_mbid is _UNCHANGED
        else clean_mbid(release_group_mbid)
    )

    if not next_release_mbid and not next_release_group_mbid:
        connection.execute(
            "DELETE FROM album_musicbrainz_links WHERE album_id = ?",
            (album_id,),
        )
        return

    connection.execute(
        """
        INSERT INTO album_musicbrainz_links (
            album_id,
            release_mbid,
            release_group_mbid
        ) VALUES (?, ?, ?)
        ON CONFLICT(album_id) DO UPDATE SET
            release_mbid = excluded.release_mbid,
            release_group_mbid = excluded.release_group_mbid
        """,
        (album_id, next_release_mbid, next_release_group_mbid),
    )


def store_album_musicbrainz_release_group_if_missing(
    connection: sqlite3.Connection,
    album_id: str,
    release_group_mbid: str,
) -> bool:
    current = connection.execute(
        """
        SELECT release_group_mbid
        FROM album_musicbrainz_links
        WHERE album_id = ?
        """,
        (album_id,),
    ).fetchone()
    if current is not None and clean_mbid(current["release_group_mbid"]):
        return False

    store_album_musicbrainz_link(
        connection,
        album_id,
        release_group_mbid=release_group_mbid,
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

    genres: list[str] = []
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
        genres.append(genre)
    return genres


def musicbrainz_release_group_mbid(payload: dict[str, object]) -> str | None:
    release_group = payload.get("release-group")
    if not isinstance(release_group, dict):
        return None
    return clean_mbid(release_group.get("id"))


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
