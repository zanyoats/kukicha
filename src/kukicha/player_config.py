from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from pathlib import Path
import re
import sqlite3
import sys
import tomllib
from typing import Any

from jinja2 import Environment, PackageLoader, select_autoescape

from .album_artists import (
    DEFAULT_ALBUM_ARTIST_SPLIT_PATTERNS,
    normalize_album_artist_split_patterns,
)
from .display import display_album_title
from .library_sources import (
    RemoteRootConfig,
    normalize_remote_root_config,
    remote_root_display_label,
    validate_remote_roots,
)
from .player_common import format_compact_count
from .player_errors import PlayerConfigError
from .player_navigation import (
    album_art_url,
    album_artist_links,
    album_artist_url,
    album_summary_text,
    album_url,
)
from .use_case import prepare_player_database

DEFAULT_PLAYER_LOG_LEVEL = "DEBUG"
DEFAULT_PLAYER_HOST = "127.0.0.1"
DEFAULT_PLAYER_PORT = 4533
DEFAULT_OPEN_SUBSONIC_MOUNT_PREFIX = "/"
DEFAULT_OPEN_SUBSONIC_SECRET_FILENAME = "opensubsonic.secret"
DEFAULT_TOAST_TIMEOUT_MS = 5000
DEFAULT_ACCENT_COLOR = "warm-brown"
SYSTEM_APPEARANCE = "system"
SYSTEM_LIGHT_APPEARANCE = "light"
SYSTEM_DARK_APPEARANCE = "dim"
DEFAULT_APPEARANCE = SYSTEM_APPEARANCE
DEFAULT_PREFER_MUSICBRAINZ_ENGLISH_ALIASES = True
DEFAULT_AUTH_COOKIE_MAX_AGE = "180d"
DEFAULT_AUTH_COOKIE_NAME = "kukicha_cookie"
PLAYER_CONFIG_FILENAME = "kukicha.toml"
PLAYER_DATABASE_FILENAME = "kukicha.sqlite"
PLAYER_CONFIG_KEY_ORDER = (
    "log_level",
    "database_path",
    "roots",
    "remote_roots",
    "ffmpeg_path",
    "youtube_download_path",
    "prefer_musicbrainz_english_aliases",
    "host",
    "port",
    "appearance",
    "accent_color",
    "toast_timeout_ms",
    "album_artist_split_patterns",
)
AUTH_CONFIG_KEY_ORDER = (
    "username",
    "password_hash_file",
    "cookie_max_age",
    "cookie_name",
)
OPEN_SUBSONIC_CONFIG_KEY_ORDER = (
    "mount_prefix",
    "secret_file",
)
PLAYER_CONFIG_DISPLAY_KEY_ORDER = (
    *PLAYER_CONFIG_KEY_ORDER,
    *(f"auth.{key}" for key in AUTH_CONFIG_KEY_ORDER),
    *(f"opensubsonic.{key}" for key in OPEN_SUBSONIC_CONFIG_KEY_ORDER),
)
PLAYER_CONFIG_KEYS = frozenset((*PLAYER_CONFIG_KEY_ORDER, "auth", "opensubsonic"))
AUTH_CONFIG_KEYS = frozenset(AUTH_CONFIG_KEY_ORDER)
OPEN_SUBSONIC_CONFIG_KEYS = frozenset(OPEN_SUBSONIC_CONFIG_KEY_ORDER)
AUTH_COOKIE_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
AUTH_COOKIE_MAX_AGE_RE = re.compile(r"^(?P<days>[1-9][0-9]*)d$")
LOGGER = logging.getLogger("kukicha.player")

class _MaxLevelFilter(logging.Filter):
    def __init__(self, max_level: int) -> None:
        super().__init__()
        self.max_level = max_level

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno < self.max_level

@dataclass(frozen=True, slots=True)
class OpenSubsonicOptions:
    mount_prefix: str
    secret_file: Path


@dataclass(frozen=True, slots=True)
class PlayerAuthOptions:
    username: str
    password_hash_file: Path
    cookie_max_age: str = DEFAULT_AUTH_COOKIE_MAX_AGE
    cookie_max_age_seconds: int = 180 * 24 * 60 * 60
    cookie_name: str = DEFAULT_AUTH_COOKIE_NAME


@dataclass(frozen=True, slots=True)
class PlayerServerOptions:
    config_path: Path
    database: Path
    ffmpeg_path: Path | None
    roots: tuple[Path, ...] = ()
    remote_roots: tuple[RemoteRootConfig, ...] = ()
    host: str = DEFAULT_PLAYER_HOST
    port: int = DEFAULT_PLAYER_PORT
    log_level: str = DEFAULT_PLAYER_LOG_LEVEL
    accent_color: str = DEFAULT_ACCENT_COLOR
    appearance: str = DEFAULT_APPEARANCE
    toast_timeout_ms: int = DEFAULT_TOAST_TIMEOUT_MS
    album_artist_split_patterns: tuple[str, ...] = DEFAULT_ALBUM_ARTIST_SPLIT_PATTERNS
    youtube_download_path: Path | None = None
    prefer_musicbrainz_english_aliases: bool = DEFAULT_PREFER_MUSICBRAINZ_ENGLISH_ALIASES
    auth: PlayerAuthOptions | None = None
    opensubsonic: OpenSubsonicOptions | None = None


@dataclass(frozen=True, slots=True)
class PlayerConfigValue:
    key: str
    value: str
    source: str
    items: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PlayerConfigSummary:
    path: Path
    status: str
    values: tuple[PlayerConfigValue, ...] = ()
    supported_keys: tuple[str, ...] = PLAYER_CONFIG_DISPLAY_KEY_ORDER
    error: str = ""


@dataclass(frozen=True, slots=True)
class ResolvedConfigPath:
    source: Path
    resolved: Path


ACCENT_COLOR_CODES = {
    "red": "#dc2626",
    "dark-red": "#b91c1c",
    "bright-red": "#ef4444",
    "rose": "#e11d48",
    "dark-rose": "#be123c",
    "pink": "#ec4899",
    "dark-pink": "#db2777",
    "orange": "#f97316",
    "dark-orange": "#ea580c",
    "amber": "#f59e0b",
    "dark-amber": "#d97706",
    "yellow": "#eab308",
    "dark-yellow": "#ca8a04",
    "lime": "#84cc16",
    "dark-lime": "#65a30d",
    "green": "#22c55e",
    "dark-green": "#16a34a",
    "emerald": "#059669",
    "dark-emerald": "#047857",
    "teal": "#14b8a6",
    "dark-teal": "#0f766e",
    "cyan": "#06b6d4",
    "dark-cyan": "#0891b2",
    "sky-blue": "#0ea5e9",
    "dark-sky-blue": "#0284c7",
    "blue": "#3b82f6",
    "dark-blue": "#2563eb",
    "indigo": "#4f46e5",
    "dark-indigo": "#4338ca",
    "violet": "#8b5cf6",
    "dark-violet": "#7c3aed",
    "purple": "#a855f7",
    "dark-purple": "#9333ea",
    "fuchsia": "#d946ef",
    "dark-fuchsia": "#c026d3",
    "gray": "#71717a",
    "dark-gray": "#52525b",
    "cool-gray": "#9ca3af",
    "medium-gray": "#6b7280",
    "brown": "#a16207",
    "dark-brown": "#854d0e",
    "warm-brown": "#8b5e3c",
    "taupe": "#766b65",
    "warm-taupe": "#b09f95",
}
ACCENT_COLOR_NAMES_BY_CODE = {code: name for name, code in ACCENT_COLOR_CODES.items()}
ACCENT_COLOR_STRONG_MINIMUM_CONTRAST = 4.5
CONTROL_ACCENT_MINIMUM_CONTRAST = 3.0
ACCENT_FOREGROUND_DARK = "#111827"
ACCENT_FOREGROUND_LIGHT = "#ffffff"


@dataclass(frozen=True, slots=True)
class PlayerAccentTheme:
    name: str
    accent: str
    accent_strong: str
    accent_soft: str
    accent_foreground: str


@dataclass(frozen=True, slots=True)
class PlayerAppearanceTheme:
    name: str
    color_scheme: str
    bg: str
    surface: str
    surface_alt: str
    surface_hover: str
    surface_overlay: str
    surface_overlay_hover: str
    text: str
    muted: str
    line: str
    track_row_highlight: str
    track_row_highlight_text: str


APPEARANCE_THEMES = {
    "light": PlayerAppearanceTheme(
        name="light",
        color_scheme="light",
        bg="#f4f4f5",
        surface="#ffffff",
        surface_alt="#eeeeee",
        surface_hover="#e1e1e1",
        surface_overlay="rgba(255, 255, 255, 0.94)",
        surface_overlay_hover="rgba(238, 238, 238, 0.97)",
        text="#18181b",
        muted="#5c5c5c",
        line="#e4e4e7",
        track_row_highlight="var(--accent-soft)",
        track_row_highlight_text="var(--text)",
    ),
    "dark": PlayerAppearanceTheme(
        name="dark",
        color_scheme="dark",
        bg="#18181b",
        surface="#27272a",
        surface_alt="#3f3f46",
        surface_hover="#52525b",
        surface_overlay="rgba(39, 39, 42, 0.94)",
        surface_overlay_hover="rgba(63, 63, 70, 0.97)",
        text="#f4f4f5",
        muted="#a1a1aa",
        line="#3f3f46",
        track_row_highlight="#3f3f46",
        track_row_highlight_text="#f4f4f5",
    ),
    "dim": PlayerAppearanceTheme(
        name="dim",
        color_scheme="dark",
        bg="#1e293b",
        surface="#475569",
        surface_alt="#64748b",
        surface_hover="#708199",
        surface_overlay="rgba(71, 85, 105, 0.94)",
        surface_overlay_hover="rgba(100, 116, 139, 0.97)",
        text="#f4f4f5",
        muted="#cbd5e1",
        line="#64748b",
        track_row_highlight="#334155",
        track_row_highlight_text="#f4f4f5",
    ),
}
APPEARANCE_NAMES = (*APPEARANCE_THEMES, SYSTEM_APPEARANCE)


def default_player_config_dir() -> Path:
    config_home = os.environ.get("XDG_CONFIG_HOME")
    if config_home:
        return Path(config_home).expanduser() / "kukicha"
    return Path.home() / ".config" / "kukicha"

def default_player_config_path() -> Path:
    return default_player_config_dir() / PLAYER_CONFIG_FILENAME

def load_player_options(
    config_path: str | Path | None = None,
    *,
    require_auth: bool = True,
) -> PlayerServerOptions:
    resolved_config_path, config_required = resolve_player_config_path(config_path)
    config_dir = resolved_config_path.parent
    config = read_player_config(resolved_config_path, required=config_required)

    log_level = parse_player_log_level(config.get("log_level", DEFAULT_PLAYER_LOG_LEVEL))
    database = parse_config_path(
        config.get("database_path"),
        key="database_path",
        base_dir=config_dir,
        default=config_dir / PLAYER_DATABASE_FILENAME,
    )
    ffmpeg_path = parse_optional_config_path(
        config.get("ffmpeg_path", ""),
        key="ffmpeg_path",
        base_dir=config_dir,
    )
    youtube_download_path = parse_optional_config_path(
        config.get("youtube_download_path", ""),
        key="youtube_download_path",
        base_dir=config_dir,
    )
    roots = parse_config_path_list(
        config.get("roots", ()),
        key="roots",
        base_dir=config_dir,
    )
    remote_roots = parse_remote_roots(config.get("remote_roots", ()))
    host = parse_player_host(config.get("host", DEFAULT_PLAYER_HOST))
    port = parse_player_port(config.get("port", DEFAULT_PLAYER_PORT))
    accent_color = parse_accent_color(config.get("accent_color", DEFAULT_ACCENT_COLOR))
    appearance = parse_appearance(config.get("appearance", DEFAULT_APPEARANCE))
    toast_timeout_ms = parse_positive_milliseconds(
        config.get("toast_timeout_ms", DEFAULT_TOAST_TIMEOUT_MS),
        key="toast_timeout_ms",
    )
    album_artist_split_patterns = parse_album_artist_split_patterns(
        config.get("album_artist_split_patterns", DEFAULT_ALBUM_ARTIST_SPLIT_PATTERNS)
    )
    prefer_musicbrainz_english_aliases = parse_config_bool(
        config.get(
            "prefer_musicbrainz_english_aliases",
            DEFAULT_PREFER_MUSICBRAINZ_ENGLISH_ALIASES,
        ),
        key="prefer_musicbrainz_english_aliases",
    )
    auth = parse_player_auth_options(
        config.get("auth"),
        base_dir=config_dir,
        require_auth=require_auth,
    )
    opensubsonic = parse_open_subsonic_options(
        config.get("opensubsonic"),
        base_dir=config_dir,
    )
    if opensubsonic is not None and auth is None:
        raise PlayerConfigError("[opensubsonic] requires [auth]; run `kukicha init`")

    return PlayerServerOptions(
        config_path=resolved_config_path,
        database=database,
        roots=roots,
        remote_roots=remote_roots,
        ffmpeg_path=ffmpeg_path,
        youtube_download_path=youtube_download_path,
        host=host,
        port=port,
        log_level=log_level,
        accent_color=accent_color,
        appearance=appearance,
        toast_timeout_ms=toast_timeout_ms,
        album_artist_split_patterns=album_artist_split_patterns,
        prefer_musicbrainz_english_aliases=prefer_musicbrainz_english_aliases,
        auth=auth,
        opensubsonic=opensubsonic,
    )

def player_config_help_text(config_path: str | Path | None = None) -> str:
    summary = player_config_summary(config_path)
    lines = [
        "Config file:",
        f"  status: {summary.status}",
        f"  path: {summary.path}",
    ]

    if summary.error:
        lines.append(f"  error: {summary.error}")
    else:
        values = {item.key: item for item in summary.values}
        lines.extend(
            (
                "",
                "Current values:",
                *(
                    f"  {key}: {values[key].value} ({values[key].source})"
                    for key in PLAYER_CONFIG_DISPLAY_KEY_ORDER
                ),
            )
        )

    lines.extend(("", "Supported keys:"))
    lines.extend(f"  {key}" for key in summary.supported_keys)
    lines.extend(("", "appearance accepts these values:"))
    lines.extend(f"  {name}" for name in APPEARANCE_NAMES)
    lines.extend(("", "accent_color accepts these palette names or matching hex codes:"))
    lines.append(f"  {' '.join(ACCENT_COLOR_CODES)}")
    return "\n".join(lines)

def player_config_summary(
    config_path: str | Path | None = None,
    *,
    options: PlayerServerOptions | None = None,
) -> PlayerConfigSummary:
    if options is None:
        resolved_config_path, config_required = resolve_player_config_path(config_path)
        try:
            if config_required and not resolved_config_path.exists():
                raise PlayerConfigError(f"config file does not exist: {resolved_config_path}")
            raw_config = read_player_config(resolved_config_path, required=False)
            resolved_options = load_player_options(
                resolved_config_path if config_path is not None else None
            )
        except PlayerConfigError as error:
            return PlayerConfigSummary(
                path=resolved_config_path,
                status=player_config_status_label(
                    resolved_config_path,
                    required=config_required,
                ),
                error=str(error),
            )
        return PlayerConfigSummary(
            path=resolved_config_path,
            status=player_config_status_label(
                resolved_config_path,
                required=config_required,
            ),
            values=player_config_values(resolved_options, raw_config),
        )

    raw_config = read_player_config(options.config_path, required=False)
    return PlayerConfigSummary(
        path=options.config_path,
        status=player_config_status_label(options.config_path, required=False),
        values=player_config_values(options, raw_config),
    )

def player_config_values(
    options: PlayerServerOptions,
    raw_config: dict[str, object],
) -> tuple[PlayerConfigValue, ...]:
    auth = options.auth
    opensubsonic = options.opensubsonic
    values = {
        "log_level": options.log_level,
        "database_path": str(options.database),
        "roots": format_player_config_path_list(options.roots),
        "remote_roots": format_player_config_remote_roots(options.remote_roots),
        "ffmpeg_path": format_player_config_optional_path(options.ffmpeg_path),
        "youtube_download_path": format_player_config_optional_path(
            options.youtube_download_path
        ),
        "prefer_musicbrainz_english_aliases": format_player_config_bool(
            options.prefer_musicbrainz_english_aliases
        ),
        "host": options.host,
        "port": str(options.port),
        "accent_color": options.accent_color,
        "appearance": options.appearance,
        "toast_timeout_ms": str(options.toast_timeout_ms),
        "album_artist_split_patterns": format_player_config_string_list(
            options.album_artist_split_patterns
        ),
        "auth.username": auth.username if auth is not None else "<unset>",
        "auth.password_hash_file": (
            str(auth.password_hash_file) if auth is not None else "<unset>"
        ),
        "auth.cookie_max_age": (
            auth.cookie_max_age if auth is not None else DEFAULT_AUTH_COOKIE_MAX_AGE
        ),
        "auth.cookie_name": (
            auth.cookie_name if auth is not None else DEFAULT_AUTH_COOKIE_NAME
        ),
        "opensubsonic.mount_prefix": (
            opensubsonic.mount_prefix if opensubsonic is not None else "<unset>"
        ),
        "opensubsonic.secret_file": (
            str(opensubsonic.secret_file) if opensubsonic is not None else "<unset>"
        ),
    }
    value_items = {
        "roots": tuple(str(root) for root in options.roots),
        "remote_roots": tuple(remote_root_display_label(root) for root in options.remote_roots),
        "album_artist_split_patterns": options.album_artist_split_patterns,
    }
    return tuple(
        PlayerConfigValue(
            key=key,
            value=values[key],
            source=player_config_value_source(raw_config, key),
            items=value_items.get(key, ()),
        )
        for key in PLAYER_CONFIG_DISPLAY_KEY_ORDER
    )

def player_config_status_label(path: Path, *, required: bool) -> str:
    if path.exists():
        return "found"
    if required:
        return "missing (startup would fail)"
    return "missing (defaults in effect)"

def player_config_value_source(config: dict[str, object], key: str) -> str:
    if key.startswith("auth."):
        auth = config.get("auth")
        if not isinstance(auth, dict):
            return "default"
        auth_key = key.split(".", 1)[1]
        return "configured" if auth_key in auth else "default"
    if key.startswith("opensubsonic."):
        opensubsonic = config.get("opensubsonic")
        if not isinstance(opensubsonic, dict):
            return "default"
        opensubsonic_key = key.split(".", 1)[1]
        return "configured" if opensubsonic_key in opensubsonic else "default"
    return "configured" if key in config else "default"

def format_player_config_optional_path(path: Path | None) -> str:
    return str(path) if path is not None else "<unset>"

def format_player_config_string_list(values: tuple[str, ...]) -> str:
    return "[" + ", ".join(repr(value) for value in values) + "]"

def format_player_config_bool(value: bool) -> str:
    return "true" if value else "false"

def format_player_config_path_list(values: tuple[Path, ...]) -> str:
    return "[" + ", ".join(repr(str(value)) for value in values) + "]"

def format_player_config_remote_roots(values: tuple[RemoteRootConfig, ...]) -> str:
    return "[" + ", ".join(repr(remote_root_display_label(value)) for value in values) + "]"

def configure_player_logging(log_level: str) -> None:
    level_name = parse_player_log_level(log_level)
    level = logging.getLevelNamesMapping()[level_name]

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(level)
    stdout_handler.addFilter(_MaxLevelFilter(logging.WARNING))
    stdout_handler.setFormatter(formatter)

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(max(level, logging.WARNING))
    stderr_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(level)
    root_logger.addHandler(stdout_handler)
    root_logger.addHandler(stderr_handler)

def resolve_player_config_path(config_path: str | Path | None) -> tuple[Path, bool]:
    if config_path is None:
        return resolve_path(default_player_config_path()), True
    return resolve_path(Path(config_path).expanduser()), True

def read_player_config(path: Path, *, required: bool) -> dict[str, object]:
    if not path.exists():
        if required:
            raise PlayerConfigError(f"config file does not exist: {path}")
        return {}

    try:
        with path.open("rb") as handle:
            config = tomllib.load(handle)
    except tomllib.TOMLDecodeError as error:
        raise PlayerConfigError(f"invalid TOML in config file {path}: {error}") from error
    except OSError as error:
        raise PlayerConfigError(f"failed to read config file {path}: {error}") from error

    unknown_keys = sorted(set(config) - PLAYER_CONFIG_KEYS)
    if unknown_keys:
        keys = ", ".join(unknown_keys)
        raise PlayerConfigError(f"unsupported config key(s) in {path}: {keys}")
    auth_config = config.get("auth")
    if auth_config is not None:
        if not isinstance(auth_config, dict):
            raise PlayerConfigError(f"auth must be a table in {path}")
        unknown_auth_keys = sorted(set(auth_config) - AUTH_CONFIG_KEYS)
        if unknown_auth_keys:
            keys = ", ".join(unknown_auth_keys)
            raise PlayerConfigError(f"unsupported auth key(s) in {path}: {keys}")
    open_subsonic_config = config.get("opensubsonic")
    if open_subsonic_config is not None:
        if not isinstance(open_subsonic_config, dict):
            raise PlayerConfigError(f"opensubsonic must be a table in {path}")
        unknown_open_subsonic_keys = sorted(
            set(open_subsonic_config) - OPEN_SUBSONIC_CONFIG_KEYS
        )
        if unknown_open_subsonic_keys:
            keys = ", ".join(unknown_open_subsonic_keys)
            raise PlayerConfigError(
                f"unsupported opensubsonic key(s) in {path}: {keys}"
            )
    return config

def parse_player_log_level(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlayerConfigError("log_level must be a non-empty string")
    level_name = value.strip().upper()
    levels = logging.getLevelNamesMapping()
    if level_name not in levels:
        raise PlayerConfigError(f"unsupported log_level: {value}")
    return str(logging.getLevelName(levels[level_name]))

def parse_player_host(value: object) -> str:
    return parse_config_non_empty_string(value, key="host")

def parse_player_port(value: object) -> int:
    return parse_config_tcp_port(value, key="port")

def parse_config_tcp_port(value: object, *, key: str) -> int:
    if not isinstance(value, int):
        raise PlayerConfigError(f"{key} must be an integer")
    if value < 1 or value > 65535:
        raise PlayerConfigError(f"{key} must be between 1 and 65535")
    return value

def parse_config_non_empty_string(value: object, *, key: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlayerConfigError(f"{key} must be a non-empty string")
    return value.strip()

def parse_accent_color(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlayerConfigError("accent_color must be a non-empty string")
    name = normalize_accent_color_name(value)
    if name not in ACCENT_COLOR_CODES:
        raise PlayerConfigError(f"accent_color must be a supported palette color: {value}")
    return name

def parse_appearance(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlayerConfigError("appearance must be a non-empty string")
    appearance = normalize_appearance_name(value)
    if appearance not in APPEARANCE_NAMES:
        raise PlayerConfigError(f"appearance must be one of: {', '.join(APPEARANCE_NAMES)}")
    return appearance

def normalize_appearance_name(value: str) -> str:
    return value.strip().lower()

def normalize_accent_color_name(value: str) -> str:
    color = value.strip().lower()
    return ACCENT_COLOR_NAMES_BY_CODE.get(color, color)

def player_accent_color(name: str) -> str:
    return player_accent_theme(name).accent

def player_accent_theme(name: str) -> PlayerAccentTheme:
    color_name = normalize_accent_color_name(name)
    if color_name not in ACCENT_COLOR_CODES:
        color_name = DEFAULT_ACCENT_COLOR
    accent = ACCENT_COLOR_CODES[color_name]
    accent_soft = derived_accent_soft(accent)
    return PlayerAccentTheme(
        name=color_name,
        accent=accent,
        accent_strong=derived_accent_strong(accent, accent_soft),
        accent_soft=accent_soft,
        accent_foreground=derived_accent_foreground(accent),
    )

def player_appearance_theme(name: str) -> PlayerAppearanceTheme:
    appearance = normalize_appearance_name(name)
    if appearance == SYSTEM_APPEARANCE:
        appearance = SYSTEM_LIGHT_APPEARANCE
    return APPEARANCE_THEMES.get(appearance, APPEARANCE_THEMES[SYSTEM_LIGHT_APPEARANCE])

def player_theme_context(accent_color: str, appearance: str) -> dict[str, object]:
    accent_theme = player_accent_theme(accent_color)
    appearance_name = normalize_appearance_name(appearance)
    if appearance_name not in APPEARANCE_NAMES:
        appearance_name = DEFAULT_APPEARANCE
    appearance_theme = player_appearance_theme(appearance_name)
    context: dict[str, object] = {
        "accent_color": accent_theme.accent,
        "accent_theme": accent_theme,
        "appearance": appearance_name,
        "appearance_theme": appearance_theme,
        "control_accent": derived_control_accent(accent_theme.accent, appearance_theme),
        "system_appearance_theme": None,
        "system_control_accent": "",
    }
    if appearance_name == SYSTEM_APPEARANCE:
        system_appearance_theme = APPEARANCE_THEMES[SYSTEM_DARK_APPEARANCE]
        context["system_appearance_theme"] = system_appearance_theme
        context["system_control_accent"] = derived_control_accent(
            accent_theme.accent,
            system_appearance_theme,
        )
    return context

def derived_accent_strong(accent: str, accent_soft: str) -> str:
    accent_rgb = hex_to_rgb(accent)
    black_rgb = (0, 0, 0)
    for accent_percent in range(70, 19, -5):
        candidate = rgb_to_hex(mix_rgb(accent_rgb, black_rgb, accent_percent / 100))
        if contrast_ratio(candidate, accent_soft) >= ACCENT_COLOR_STRONG_MINIMUM_CONTRAST:
            return candidate
    return ACCENT_FOREGROUND_DARK

def derived_accent_soft(accent: str) -> str:
    return rgb_to_hex(mix_rgb(hex_to_rgb(accent), (255, 255, 255), 0.12))

def derived_accent_foreground(accent: str) -> str:
    light_contrast = contrast_ratio(ACCENT_FOREGROUND_LIGHT, accent)
    dark_contrast = contrast_ratio(ACCENT_FOREGROUND_DARK, accent)
    if light_contrast >= dark_contrast:
        return ACCENT_FOREGROUND_LIGHT
    return ACCENT_FOREGROUND_DARK

def derived_control_accent(accent: str, appearance: PlayerAppearanceTheme) -> str:
    if contrast_ratio(accent, appearance.surface) >= CONTROL_ACCENT_MINIMUM_CONTRAST:
        return accent
    accent_rgb = hex_to_rgb(accent)
    text_rgb = hex_to_rgb(appearance.text)
    for accent_percent in range(90, 0, -10):
        candidate = rgb_to_hex(mix_rgb(accent_rgb, text_rgb, accent_percent / 100))
        if contrast_ratio(candidate, appearance.surface) >= CONTROL_ACCENT_MINIMUM_CONTRAST:
            return candidate
    return appearance.text

def hex_to_rgb(value: str) -> tuple[int, int, int]:
    hex_value = value.removeprefix("#")
    return (
        int(hex_value[0:2], 16),
        int(hex_value[2:4], 16),
        int(hex_value[4:6], 16),
    )

def rgb_to_hex(value: tuple[int, int, int]) -> str:
    return "#" + "".join(f"{channel:02x}" for channel in value)

def mix_rgb(
    foreground: tuple[int, int, int],
    background: tuple[int, int, int],
    foreground_weight: float,
) -> tuple[int, int, int]:
    background_weight = 1 - foreground_weight
    return tuple(
        int(
            foreground_channel * foreground_weight
            + background_channel * background_weight
            + 0.5
        )
        for foreground_channel, background_channel in zip(foreground, background)
    )

def contrast_ratio(first: str, second: str) -> float:
    first_luminance = relative_luminance(first)
    second_luminance = relative_luminance(second)
    lighter = max(first_luminance, second_luminance)
    darker = min(first_luminance, second_luminance)
    return (lighter + 0.05) / (darker + 0.05)

def relative_luminance(value: str) -> float:
    red, green, blue = hex_to_rgb(value)
    return (
        0.2126 * linearized_rgb_channel(red)
        + 0.7152 * linearized_rgb_channel(green)
        + 0.0722 * linearized_rgb_channel(blue)
    )

def linearized_rgb_channel(value: int) -> float:
    normalized = value / 255
    if normalized <= 0.04045:
        return normalized / 12.92
    return ((normalized + 0.055) / 1.055) ** 2.4

def parse_positive_milliseconds(value: object, *, key: str) -> int:
    if type(value) is not int:
        raise PlayerConfigError(f"{key} must be an integer")
    if value <= 0:
        raise PlayerConfigError(f"{key} must be greater than 0")
    return value

def parse_album_artist_split_patterns(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise PlayerConfigError("album_artist_split_patterns must be an array of strings")
    for item in value:
        if not isinstance(item, str):
            raise PlayerConfigError("album_artist_split_patterns must be an array of strings")
    return normalize_album_artist_split_patterns(value)

def parse_config_bool(value: object, *, key: str) -> bool:
    if not isinstance(value, bool):
        raise PlayerConfigError(f"{key} must be true or false")
    return value

def parse_player_auth_options(
    value: object,
    *,
    base_dir: Path,
    require_auth: bool,
) -> PlayerAuthOptions | None:
    if value is None:
        if require_auth:
            raise PlayerConfigError("[auth] section is required; run `kukicha init`")
        return None
    if not isinstance(value, dict):
        raise PlayerConfigError("auth must be a table")

    missing_keys = [key for key in ("username", "password_hash_file") if key not in value]
    if missing_keys:
        raise PlayerConfigError(
            "[auth] missing required key(s): " + ", ".join(missing_keys)
        )

    username = parse_config_non_empty_string(value["username"], key="auth.username")
    password_hash_file = parse_config_path(
        value["password_hash_file"],
        key="auth.password_hash_file",
        base_dir=base_dir,
        default=base_dir / "password.hash",
    )
    validate_password_hash_file(password_hash_file)
    cookie_max_age = parse_auth_cookie_max_age(
        value.get("cookie_max_age", DEFAULT_AUTH_COOKIE_MAX_AGE)
    )
    cookie_name = parse_auth_cookie_name(
        value.get("cookie_name", DEFAULT_AUTH_COOKIE_NAME)
    )
    return PlayerAuthOptions(
        username=username,
        password_hash_file=password_hash_file,
        cookie_max_age=cookie_max_age,
        cookie_max_age_seconds=auth_cookie_max_age_seconds(cookie_max_age),
        cookie_name=cookie_name,
    )

def parse_open_subsonic_options(
    value: object,
    *,
    base_dir: Path,
) -> OpenSubsonicOptions | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise PlayerConfigError("opensubsonic must be a table")

    missing_keys = [
        key for key in ("mount_prefix", "secret_file") if key not in value
    ]
    if missing_keys:
        raise PlayerConfigError(
            "[opensubsonic] missing required key(s): " + ", ".join(missing_keys)
        )

    mount_prefix = parse_open_subsonic_mount_prefix(value["mount_prefix"])
    secret_file = parse_config_path(
        value["secret_file"],
        key="opensubsonic.secret_file",
        base_dir=base_dir,
        default=base_dir / DEFAULT_OPEN_SUBSONIC_SECRET_FILENAME,
    )
    validate_open_subsonic_secret_file(secret_file)
    return OpenSubsonicOptions(
        mount_prefix=mount_prefix,
        secret_file=secret_file,
    )

def parse_open_subsonic_mount_prefix(value: object) -> str:
    mount_prefix = parse_config_non_empty_string(
        value,
        key="opensubsonic.mount_prefix",
    )
    if not mount_prefix.startswith("/"):
        raise PlayerConfigError("opensubsonic.mount_prefix must start with /")
    normalized = "/" + mount_prefix.strip("/")
    if normalized == "/" or mount_prefix == "/":
        return "/"
    return normalized

def read_open_subsonic_secret(path: Path) -> str:
    try:
        secret = path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise PlayerConfigError(
            f"failed to read opensubsonic.secret_file {path}: {error}"
        ) from error
    if not secret:
        raise PlayerConfigError(f"opensubsonic.secret_file is empty: {path}")
    return secret

def validate_open_subsonic_secret_file(path: Path) -> None:
    try:
        stat_result = path.stat()
    except FileNotFoundError as error:
        raise PlayerConfigError(f"opensubsonic.secret_file does not exist: {path}") from error
    except OSError as error:
        raise PlayerConfigError(
            f"failed to inspect opensubsonic.secret_file {path}: {error}"
        ) from error

    if not path.is_file():
        raise PlayerConfigError(f"opensubsonic.secret_file is not a file: {path}")

    if hasattr(os, "getuid") and stat_result.st_uid != os.getuid():
        raise PlayerConfigError(
            f"opensubsonic.secret_file must be owned by the current user: {path}"
        )

    if os.name != "nt" and stat_result.st_mode & 0o077:
        raise PlayerConfigError(f"opensubsonic.secret_file permissions must be 600: {path}")

    read_open_subsonic_secret(path)

def parse_auth_cookie_max_age(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlayerConfigError("auth.cookie_max_age must be a duration like 180d")
    cleaned = value.strip().lower()
    if AUTH_COOKIE_MAX_AGE_RE.fullmatch(cleaned) is None:
        raise PlayerConfigError("auth.cookie_max_age must be a positive day duration like 180d")
    return cleaned

def auth_cookie_max_age_seconds(value: str) -> int:
    match = AUTH_COOKIE_MAX_AGE_RE.fullmatch(value)
    if match is None:
        raise PlayerConfigError("auth.cookie_max_age must be a positive day duration like 180d")
    return int(match.group("days")) * 24 * 60 * 60

def parse_auth_cookie_name(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlayerConfigError("auth.cookie_name must be a non-empty string")
    name = value.strip()
    if AUTH_COOKIE_NAME_RE.fullmatch(name) is None:
        raise PlayerConfigError("auth.cookie_name must be a valid HTTP cookie name")
    return name

def validate_password_hash_file(path: Path) -> None:
    try:
        stat_result = path.stat()
    except FileNotFoundError as error:
        raise PlayerConfigError(f"auth.password_hash_file does not exist: {path}") from error
    except OSError as error:
        raise PlayerConfigError(f"failed to inspect auth.password_hash_file {path}: {error}") from error

    if not path.is_file():
        raise PlayerConfigError(f"auth.password_hash_file is not a file: {path}")

    if hasattr(os, "getuid") and stat_result.st_uid != os.getuid():
        raise PlayerConfigError(f"auth.password_hash_file must be owned by the current user: {path}")

    if os.name != "nt" and stat_result.st_mode & 0o077:
        raise PlayerConfigError(f"auth.password_hash_file permissions must be 600: {path}")

    try:
        password_hash = path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise PlayerConfigError(f"failed to read auth.password_hash_file {path}: {error}") from error
    if not password_hash:
        raise PlayerConfigError(f"auth.password_hash_file is empty: {path}")
    if not password_hash.startswith("$argon2id$"):
        raise PlayerConfigError(
            f"auth.password_hash_file must contain an Argon2id password hash: {path}"
        )

def parse_config_path(
    value: object,
    *,
    key: str,
    base_dir: Path,
    default: Path,
) -> Path:
    if value is None:
        return resolve_path(default)
    if not isinstance(value, str) or not value.strip():
        raise PlayerConfigError(f"{key} must be a non-empty string")
    return resolve_path(Path(value.strip()).expanduser(), base_dir=base_dir)

def parse_optional_config_path(
    value: object,
    *,
    key: str,
    base_dir: Path,
) -> Path | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise PlayerConfigError(f"{key} must be a string")
    stripped = value.strip()
    if not stripped:
        return None
    return resolve_path(Path(stripped).expanduser(), base_dir=base_dir)

def parse_config_path_list(
    value: object,
    *,
    key: str,
    base_dir: Path,
) -> tuple[Path, ...]:
    if not isinstance(value, (list, tuple)):
        raise PlayerConfigError(f"{key} must be an array of strings")

    paths: list[Path] = []
    source_paths: list[Path] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise PlayerConfigError(f"{key} must be an array of non-empty strings")
        source_path = absolute_path(Path(item.strip()).expanduser(), base_dir=base_dir)
        path = source_path.resolve(strict=False)
        source_paths.append(source_path)
        paths.append(path)
    validate_config_path_list(paths, key=key, source_paths=source_paths)
    return tuple(paths)

def parse_remote_roots(value: object) -> tuple[RemoteRootConfig, ...]:
    if not isinstance(value, (list, tuple)):
        raise PlayerConfigError("remote_roots must be an array of tables")

    roots: list[RemoteRootConfig] = []
    for item in value:
        if not isinstance(item, dict):
            raise PlayerConfigError("remote_roots must be an array of tables")
        secret_keys = sorted(
            key
            for key in item
            if key in {"access_key_id", "secret_access_key", "session_token"}
        )
        if secret_keys:
            raise PlayerConfigError(
                "remote_roots must not contain inline credentials: "
                + ", ".join(secret_keys)
            )
        supported_keys = {
            "name",
            "endpoint_url",
            "bucket",
            "prefix",
            "profile",
            "region",
            "addressing_style",
        }
        unknown_keys = sorted(set(item) - supported_keys)
        if unknown_keys:
            raise PlayerConfigError(
                "unsupported remote_roots key(s): " + ", ".join(unknown_keys)
            )
        try:
            roots.append(normalize_remote_root_config(item))
        except ValueError as error:
            raise PlayerConfigError(f"invalid remote_roots entry: {error}") from error

    remote_roots = tuple(roots)
    try:
        validate_remote_roots(remote_roots)
    except ValueError as error:
        raise PlayerConfigError(str(error)) from error
    return remote_roots

def validate_config_path_list(
    paths: list[Path] | tuple[Path, ...],
    *,
    key: str,
    source_paths: list[Path] | tuple[Path, ...] | None = None,
) -> None:
    source_values = tuple(source_paths) if source_paths is not None else tuple(paths)
    entries = tuple(
        ResolvedConfigPath(
            source=absolute_path(source_path),
            resolved=resolve_path(path),
        )
        for source_path, path in zip(source_values, paths, strict=True)
    )
    seen: dict[str, ResolvedConfigPath] = {}
    for entry in entries:
        key_value = str(entry.resolved)
        previous = seen.get(key_value)
        if previous is not None:
            reason = config_path_resolution_reason(previous, entry)
            raise PlayerConfigError(
                f"{key} must not contain duplicate paths{reason}: "
                f"{format_resolved_config_path(entry)} duplicates "
                f"{format_resolved_config_path(previous)}"
            )
        seen[key_value] = entry

    for index, parent in enumerate(entries):
        for child in entries[index + 1 :]:
            if (
                child.resolved != parent.resolved
                and child.resolved.is_relative_to(parent.resolved)
            ):
                raise_nested_config_path_error(key, child=child, parent=parent)
            if (
                parent.resolved != child.resolved
                and parent.resolved.is_relative_to(child.resolved)
            ):
                raise_nested_config_path_error(key, child=parent, parent=child)

def raise_nested_config_path_error(
    key: str,
    *,
    child: ResolvedConfigPath,
    parent: ResolvedConfigPath,
) -> None:
    reason = config_path_resolution_reason(child, parent)
    raise PlayerConfigError(
        f"{key} must not contain nested paths{reason}: "
        f"{format_resolved_config_path(child)} is inside "
        f"{format_resolved_config_path(parent)}"
    )

def config_path_resolution_reason(*entries: ResolvedConfigPath) -> str:
    if any(entry.source != entry.resolved for entry in entries):
        return " after resolving symbolic links"
    return ""

def format_resolved_config_path(entry: ResolvedConfigPath) -> str:
    if entry.source == entry.resolved:
        return str(entry.resolved)
    return f"{entry.source} (resolves to {entry.resolved})"

def absolute_path(path: Path, *, base_dir: Path | None = None) -> Path:
    expanded = path.expanduser()
    if not expanded.is_absolute():
        expanded = (base_dir or Path.cwd()) / expanded
    return Path(os.path.abspath(expanded))

def resolve_path(path: Path, *, base_dir: Path | None = None) -> Path:
    return absolute_path(path, base_dir=base_dir).resolve(strict=False)

def validate_player_startup(options: PlayerServerOptions) -> None:
    validate_config_path_list(options.roots, key="roots")
    if options.auth is not None:
        validate_password_hash_file(options.auth.password_hash_file)

    try:
        prepare_player_database(options.database)
    except OSError as error:
        raise PlayerConfigError(f"failed to prepare database {options.database}: {error}") from error
    except sqlite3.Error as error:
        raise PlayerConfigError(f"failed to open database {options.database}: {error}") from error

    if options.ffmpeg_path is None:
        return
    if not options.ffmpeg_path.exists():
        raise PlayerConfigError(f"ffmpeg path does not exist: {options.ffmpeg_path}")
    if not options.ffmpeg_path.is_file():
        raise PlayerConfigError(f"ffmpeg path is not a file: {options.ffmpeg_path}")
    if not os.access(options.ffmpeg_path, os.X_OK):
        raise PlayerConfigError(f"ffmpeg path is not executable: {options.ffmpeg_path}")

def build_template_environment() -> Environment:
    environment = Environment(
        loader=PackageLoader("kukicha", "templates"),
        autoescape=select_autoescape(("html", "xml")),
    )
    environment.filters["album_url"] = album_url
    environment.filters["album_artist_links"] = album_artist_links
    environment.filters["album_artist_url"] = album_artist_url
    environment.filters["album_art_url"] = album_art_url
    environment.filters["album_summary"] = album_summary_text
    environment.filters["compact_count"] = format_compact_count
    environment.filters["display_album_title"] = display_album_title
    return environment
