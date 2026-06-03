from __future__ import annotations

import hashlib
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


DISCOGS_API_ROOT = "https://api.discogs.com"
DISCOGS_USER_AGENT = "kukicha/0.1.0 +https://cconroy.com/kukicha"
DISCOGS_TIMEOUT_SECONDS = 20
DISCOGS_MAX_RESPONSE_BYTES = 25 * 1024 * 1024
DISCOGS_MIN_REQUEST_INTERVAL_SECONDS = 1.0
DISCOGS_RATE_LIMIT_STATUS_CODES = {429, 503}
DISCOGS_RELEASE_PATH = "release"
DISCOGS_MASTER_PATH = "master"
DISCOGS_ENTITY_RELEASE = "release"
DISCOGS_ENTITY_MASTER = "master"
DISCOGS_ID_PATTERN = re.compile(r"^(\d+)")


@dataclass(slots=True)
class DiscogsLookupStats:
    api_calls: int = 0
    cached_calls: int = 0
    rate_limit_retries: int = 0
    fetch_failures: int = 0


@dataclass(frozen=True, slots=True)
class DiscogsEntityReference:
    entity_type: str
    entity_id: str


class DiscogsClient:
    def __init__(
        self,
        *,
        stats: DiscogsLookupStats,
        log: Callable[[str], None] | None = None,
        min_interval_seconds: float = DISCOGS_MIN_REQUEST_INTERVAL_SECONDS,
        max_retries: int = 5,
    ) -> None:
        self.stats = stats
        self.log = log
        self.min_interval_seconds = min_interval_seconds
        self.max_retries = max_retries
        self._last_request_at = 0.0

    def fetch_lookup(self, entity_type: str, entity_id: str) -> tuple[dict[str, object] | None, str]:
        url = build_discogs_lookup_url(entity_type, entity_id)
        backoff_seconds = 2.0
        attempts = 0

        while True:
            self._wait_for_local_rate_limit()
            request = urllib.request.Request(
                url,
                headers={
                    "Accept": "application/json",
                    "User-Agent": DISCOGS_USER_AGENT,
                },
            )
            self._last_request_at = time.monotonic()
            self.stats.api_calls += 1
            try:
                with urllib.request.urlopen(
                    request,
                    timeout=DISCOGS_TIMEOUT_SECONDS,
                ) as response:
                    data = response.read(DISCOGS_MAX_RESPONSE_BYTES + 1)
            except urllib.error.HTTPError as error:
                if error.code in DISCOGS_RATE_LIMIT_STATUS_CODES and attempts < self.max_retries:
                    attempts += 1
                    self.stats.rate_limit_retries += 1
                    delay = retry_delay_seconds(error, default=backoff_seconds)
                    self._emit(
                        "Discogs rate limit reached "
                        f"(HTTP {error.code}) for {entity_type} {entity_id}; "
                        f"retrying in {delay:.1f}s"
                    )
                    time.sleep(delay)
                    backoff_seconds = min(backoff_seconds * 2.0, 60.0)
                    continue

                self.stats.fetch_failures += 1
                if error.code == 404:
                    self._emit(f"Discogs {entity_type} {entity_id} was not found")
                else:
                    self._emit(
                        f"Discogs lookup failed for {entity_type} {entity_id} "
                        f"(HTTP {error.code})"
                    )
                return None, url
            except (OSError, TimeoutError, urllib.error.URLError) as error:
                self.stats.fetch_failures += 1
                self._emit(f"Discogs lookup failed for {entity_type} {entity_id}: {error}")
                return None, url

            if len(data) > DISCOGS_MAX_RESPONSE_BYTES:
                self.stats.fetch_failures += 1
                self._emit(
                    f"Discogs response for {entity_type} {entity_id} exceeded "
                    f"{DISCOGS_MAX_RESPONSE_BYTES} bytes"
                )
                return None, url

            try:
                payload = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                self.stats.fetch_failures += 1
                self._emit(f"Discogs response for {entity_type} {entity_id} was not valid JSON: {error}")
                return None, url

            if not isinstance(payload, dict):
                self.stats.fetch_failures += 1
                self._emit(f"Discogs response for {entity_type} {entity_id} was not an object")
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


def get_discogs_entity(
    connection: sqlite3.Connection,
    client: DiscogsClient,
    *,
    entity_type: str,
    entity_id: str,
) -> dict[str, object] | None:
    cached_payload = load_cached_discogs_entity(
        connection,
        entity_type=entity_type,
        entity_id=entity_id,
    )
    if cached_payload is not None:
        client.stats.cached_calls += 1
        return cached_payload

    payload, endpoint_url = client.fetch_lookup(entity_type, entity_id)
    if payload is None:
        return None

    store_discogs_entity(
        connection,
        entity_type=entity_type,
        entity_id=entity_id,
        endpoint_url=endpoint_url,
        payload=payload,
    )
    return payload


def load_cached_discogs_entity(
    connection: sqlite3.Connection,
    *,
    entity_type: str,
    entity_id: str,
) -> dict[str, object] | None:
    row = connection.execute(
        """
        SELECT response_json
        FROM discogs_entity_cache
        WHERE entity_type = ? AND entity_id = ?
        """,
        (entity_type, entity_id),
    ).fetchone()
    if row is None:
        return None
    try:
        payload = json.loads(str(row["response_json"]))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def store_discogs_entity(
    connection: sqlite3.Connection,
    *,
    entity_type: str,
    entity_id: str,
    endpoint_url: str,
    payload: dict[str, object],
) -> None:
    connection.execute(
        """
        INSERT OR REPLACE INTO discogs_entity_cache (
            entity_type,
            entity_id,
            fetched_at,
            endpoint_url,
            response_json
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            entity_type,
            entity_id,
            datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            endpoint_url,
            json.dumps(payload, sort_keys=True, separators=(",", ":")),
        ),
    )


def parse_discogs_album_url(value: str) -> DiscogsEntityReference:
    parts = urllib.parse.urlsplit(value.strip())
    if not parts.scheme or not parts.netloc:
        raise ValueError("Expected a Discogs release or master URL.")
    host = parts.netloc.casefold()
    if host not in {"discogs.com", "www.discogs.com"}:
        raise ValueError("Expected a Discogs release or master URL.")

    path_parts = [part for part in parts.path.split("/") if part]
    if len(path_parts) < 2:
        raise ValueError("Expected a Discogs release or master URL.")

    path_type = path_parts[0].casefold()
    entity_id = discogs_id_from_path_part(path_parts[1])
    if path_type == DISCOGS_RELEASE_PATH:
        return DiscogsEntityReference(
            entity_type=DISCOGS_ENTITY_RELEASE,
            entity_id=entity_id,
        )
    if path_type == DISCOGS_MASTER_PATH:
        return DiscogsEntityReference(
            entity_type=DISCOGS_ENTITY_MASTER,
            entity_id=entity_id,
        )
    raise ValueError("Expected a Discogs release or master URL.")


def discogs_id_from_path_part(value: str) -> str:
    match = DISCOGS_ID_PATTERN.match(value.strip())
    if match is None:
        raise ValueError("Discogs URLs must include a numeric release or master ID.")
    return match.group(1)


def discogs_master_id(payload: dict[str, object]) -> str | None:
    value = payload.get("master_id")
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return str(value)
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit() and int(text) > 0:
            return text
    return None


def discogs_release_fingerprint(release_id: str, *, bits: int = 12) -> str:
    source = f"discogs:release:{str(release_id or '').strip()}"
    digest = hashlib.sha1(source.encode("utf-8")).hexdigest()
    return digest[: bits // 4]


def discogs_primary_image_url(payload: dict[str, object]) -> str | None:
    images = payload.get("images")
    if not isinstance(images, list):
        return None

    candidates: list[tuple[int, int, str]] = []
    for index, item in enumerate(images):
        if not isinstance(item, dict):
            continue
        image_url = discogs_image_item_url(item)
        if image_url is None:
            continue
        image_type = item.get("type")
        priority = 0 if isinstance(image_type, str) and image_type.casefold() == "primary" else 1
        candidates.append((priority, index, image_url))

    if not candidates:
        return None
    return min(candidates)[2]


def discogs_image_item_url(item: dict[object, object]) -> str | None:
    for field in ("uri", "resource_url", "uri150"):
        value = item.get(field)
        if not isinstance(value, str):
            continue
        image_url = value.strip()
        parts = urllib.parse.urlsplit(image_url)
        if image_url and parts.scheme in {"http", "https"} and parts.netloc:
            return image_url
    return None


def discogs_title(payload: dict[str, object], *, entity_type: str) -> str:
    title = payload.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError(f"Discogs {entity_type} payload is missing a title.")
    return title.strip()


def discogs_artist_tag_value(payload: dict[str, object], *, entity_type: str) -> str:
    artists = payload.get("artists")
    if not isinstance(artists, list):
        raise ValueError(f"Discogs {entity_type} payload is missing artists.")

    parts: list[str] = []
    for item in artists:
        if not isinstance(item, dict):
            continue
        name = discogs_artist_name(item)
        if not name:
            continue
        joinphrase = item.get("join")
        parts.append(name + (joinphrase if isinstance(joinphrase, str) else ""))

    artist = "".join(parts).strip()
    if not artist:
        raise ValueError(f"Discogs {entity_type} payload is missing artists.")
    return artist


def discogs_artist_name(item: dict[object, object]) -> str:
    anv = item.get("anv")
    if isinstance(anv, str) and anv.strip():
        return anv.strip()
    name = item.get("name")
    return name.strip() if isinstance(name, str) else ""


def discogs_genre_style_values(payload: dict[str, object]) -> tuple[str, ...]:
    values: list[str] = []
    seen: set[str] = set()
    for field in ("genres", "styles"):
        raw_values = payload.get(field)
        if not isinstance(raw_values, list):
            continue
        for raw_value in raw_values:
            if not isinstance(raw_value, str):
                continue
            value = raw_value.strip()
            key = value.casefold()
            if not value or key in seen:
                continue
            seen.add(key)
            values.append(value)
    return tuple(values)


def build_discogs_lookup_url(entity_type: str, entity_id: str) -> str:
    if entity_type == DISCOGS_ENTITY_RELEASE:
        path = "releases"
    elif entity_type == DISCOGS_ENTITY_MASTER:
        path = "masters"
    else:
        raise ValueError(f"Unsupported Discogs entity type: {entity_type}")
    return f"{DISCOGS_API_ROOT}/{path}/{urllib.parse.quote(entity_id, safe='')}"


def discogs_url_for_entity(entity_type: str, entity_id: str) -> str:
    if entity_type == DISCOGS_ENTITY_RELEASE:
        return f"https://www.discogs.com/release/{entity_id}"
    if entity_type == DISCOGS_ENTITY_MASTER:
        return f"https://www.discogs.com/master/{entity_id}"
    return ""


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
