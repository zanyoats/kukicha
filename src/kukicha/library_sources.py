from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit


SOURCE_KIND_LOCAL = "local"
SOURCE_KIND_S3 = "s3"
REMOTE_ROOT_ADDRESSING_STYLES = frozenset(("auto", "path", "virtual"))


@dataclass(frozen=True, slots=True)
class RemoteRootConfig:
    name: str
    endpoint_url: str
    bucket: str
    prefix: str = ""
    profile: str | None = None
    region: str | None = None
    addressing_style: str = "auto"

    @property
    def root_path(self) -> str:
        return canonical_s3_path(self, self.prefix)

    @property
    def source_json(self) -> str:
        payload: dict[str, object] = {
            "name": self.name,
            "endpoint_url": self.endpoint_url,
            "bucket": self.bucket,
            "prefix": self.prefix,
            "addressing_style": self.addressing_style,
        }
        if self.profile:
            payload["profile"] = self.profile
        if self.region:
            payload["region"] = self.region
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class LibraryRootSource:
    position: int
    path: str
    kind: str = SOURCE_KIND_LOCAL
    source_json: str = "{}"


def local_root_source(position: int, root: Path | str) -> LibraryRootSource:
    return LibraryRootSource(position=position, path=str(root))


def remote_root_source(position: int, remote: RemoteRootConfig) -> LibraryRootSource:
    return LibraryRootSource(
        position=position,
        path=remote.root_path,
        kind=SOURCE_KIND_S3,
        source_json=remote.source_json,
    )


def normalize_remote_root_config(value: MappingLike) -> RemoteRootConfig:
    name = required_non_empty_string(value, "name")
    endpoint_url = normalize_endpoint_url(required_non_empty_string(value, "endpoint_url"))
    bucket = required_non_empty_string(value, "bucket")
    prefix = normalize_remote_prefix(optional_string(value, "prefix") or "")
    profile = optional_non_empty_string(value, "profile")
    region = optional_non_empty_string(value, "region")
    addressing_style = (optional_string(value, "addressing_style") or "auto").strip().lower()
    if addressing_style not in REMOTE_ROOT_ADDRESSING_STYLES:
        raise ValueError(
            "addressing_style must be one of: "
            + ", ".join(sorted(REMOTE_ROOT_ADDRESSING_STYLES))
        )
    return RemoteRootConfig(
        name=name,
        endpoint_url=endpoint_url,
        bucket=bucket,
        prefix=prefix,
        profile=profile,
        region=region,
        addressing_style=addressing_style,
    )


def remote_root_from_source_json(value: str) -> RemoteRootConfig:
    payload = json.loads(value or "{}")
    if not isinstance(payload, dict):
        raise ValueError("remote root source metadata must be an object")
    return normalize_remote_root_config(payload)


def normalize_endpoint_url(value: str) -> str:
    endpoint_url = value.strip().rstrip("/")
    parsed = urlsplit(endpoint_url)
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
        raise ValueError("endpoint_url must be an http(s) URL")
    return endpoint_url


def normalize_remote_prefix(value: str) -> str:
    prefix = value.strip().lstrip("/")
    if not prefix:
        return ""
    return prefix.rstrip("/") + "/"


def validate_remote_roots(roots: tuple[RemoteRootConfig, ...]) -> None:
    seen: dict[tuple[str, str, str], RemoteRootConfig] = {}
    for root in roots:
        identity = (root.endpoint_url, root.bucket, root.prefix)
        previous = seen.get(identity)
        if previous is not None:
            raise ValueError(
                f"remote_roots must not contain duplicate prefixes: "
                f"{remote_root_display_label(root)} duplicates "
                f"{remote_root_display_label(previous)}"
            )
        seen[identity] = root

    for index, parent in enumerate(roots):
        for child in roots[index + 1 :]:
            if parent.endpoint_url != child.endpoint_url or parent.bucket != child.bucket:
                continue
            if prefixes_are_nested(parent.prefix, child.prefix):
                raise ValueError(
                    "remote_roots must not contain nested prefixes: "
                    f"{remote_root_display_label(child)} conflicts with "
                    f"{remote_root_display_label(parent)}"
                )


def prefixes_are_nested(first: str, second: str) -> bool:
    if first == second:
        return True
    if not first or not second:
        return True
    return first.startswith(second) or second.startswith(first)


def canonical_s3_path(remote: RemoteRootConfig, key: str) -> str:
    bucket = quote(remote.bucket, safe="")
    quoted_key = quote(key, safe="/")
    if quoted_key:
        return f"s3+{remote.endpoint_url}/{bucket}/{quoted_key}"
    return f"s3+{remote.endpoint_url}/{bucket}/"


def remote_root_display_label(root: RemoteRootConfig) -> str:
    if root.name:
        return root.name
    return f"s3://{root.bucket}/{root.prefix}"


def root_source_label(path: str, kind: str, source_json: str) -> str:
    if kind == SOURCE_KIND_S3:
        try:
            return remote_root_display_label(remote_root_from_source_json(source_json))
        except Exception:
            return path
    name = Path(path).name
    if not name:
        return path
    if str(Path(path).parent) == Path(path).anchor:
        return path
    return f".../{name}"


def is_remote_path(path: str) -> bool:
    return path.startswith("s3+http://") or path.startswith("s3+https://")


def path_is_in_source(path: str, root_path: str, kind: str = SOURCE_KIND_LOCAL) -> bool:
    if kind == SOURCE_KIND_S3 or is_remote_path(path) or is_remote_path(root_path):
        normalized_root = root_path.rstrip("/")
        if path == normalized_root:
            return True
        return path.startswith(normalized_root + "/")
    try:
        return Path(path).is_relative_to(Path(root_path))
    except ValueError:
        return False


@lru_cache(maxsize=32)
def create_s3_client(remote: RemoteRootConfig) -> object:
    try:
        from botocore.config import Config
        from botocore.session import Session
    except ImportError as error:
        raise RuntimeError(
            "botocore is required for S3-compatible remote roots"
        ) from error

    session = Session(profile=remote.profile) if remote.profile else Session()
    return session.create_client(
        "s3",
        endpoint_url=remote.endpoint_url,
        region_name=remote.region,
        config=Config(s3={"addressing_style": remote.addressing_style}),
    )


def clear_s3_client_cache() -> None:
    create_s3_client.cache_clear()


MappingLike = dict[str, Any]


def required_non_empty_string(value: MappingLike, key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return item.strip()


def optional_string(value: MappingLike, key: str) -> str | None:
    item = value.get(key)
    if item is None:
        return None
    if not isinstance(item, str):
        raise ValueError(f"{key} must be a string")
    return item


def optional_non_empty_string(value: MappingLike, key: str) -> str | None:
    item = optional_string(value, key)
    if item is None:
        return None
    stripped = item.strip()
    return stripped or None
