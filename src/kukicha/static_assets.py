from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from hashlib import sha256
from importlib.resources import files


HTML_CACHE_CONTROL = "private, no-cache"
STATIC_ASSET_CACHE_CONTROL = "public, max-age=31536000, immutable"
STATIC_COMPAT_CACHE_CONTROL = "public, max-age=60"
STATIC_CONTENT_TYPES = {
    "player.css": "text/css; charset=utf-8",
    "player.js": "application/javascript; charset=utf-8",
    "favicon.svg": "image/svg+xml",
}


@dataclass(frozen=True, slots=True)
class StaticAsset:
    name: str
    fingerprinted_name: str
    content_type: str
    data: bytes


def static_asset_url(name: str) -> str:
    return f"/static/{static_asset(name).fingerprinted_name}"


def resolve_static_asset_request(name: str) -> tuple[StaticAsset, bool] | None:
    return static_assets_by_request_name().get(name)


@cache
def static_assets_by_request_name() -> dict[str, tuple[StaticAsset, bool]]:
    assets: dict[str, tuple[StaticAsset, bool]] = {}
    for name in STATIC_CONTENT_TYPES:
        asset = static_asset(name)
        assets[name] = (asset, False)
        assets[asset.fingerprinted_name] = (asset, True)
    return assets


@cache
def static_asset(name: str) -> StaticAsset:
    content_type = STATIC_CONTENT_TYPES.get(name)
    if content_type is None:
        raise KeyError(f"unknown static asset: {name}")
    data = files("kukicha").joinpath("static", name).read_bytes()
    digest = sha256(data).hexdigest()[:12]
    return StaticAsset(
        name=name,
        fingerprinted_name=f"{asset_stem(name)}.{digest}.{asset_extension(name)}",
        content_type=content_type,
        data=data,
    )


def asset_stem(name: str) -> str:
    return name.rsplit(".", 1)[0]


def asset_extension(name: str) -> str:
    return name.rsplit(".", 1)[1]
