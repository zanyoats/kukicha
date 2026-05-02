from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from pathlib import Path
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
from .player_errors import PlayerConfigError
from .player_navigation import album_art_url, album_summary_text, album_url
from .use_case import prepare_player_database

DEFAULT_PLAYER_LOG_LEVEL = "DEBUG"
DEFAULT_PLAYER_HOST = "127.0.0.1"
DEFAULT_PLAYER_PORT = 65042
DEFAULT_TOAST_TIMEOUT_MS = 5000
DEFAULT_ACCENT_COLOR = "warm-brown"
DEFAULT_APPEARANCE = "light"
PLAYER_CONFIG_FILENAME = "kukicha.toml"
PLAYER_DATABASE_FILENAME = "kukicha.sqlite"
PLAYER_CONFIG_KEY_ORDER = (
    "LogLevel",
    "DatabasePath",
    "Roots",
    "FFmpegPath",
    "Host",
    "Port",
    "Appearance",
    "AccentColor",
    "ToastTimeoutMs",
    "AlbumArtistSplitPatterns",
)
PLAYER_CONFIG_KEYS = frozenset(PLAYER_CONFIG_KEY_ORDER)
LOGGER = logging.getLogger("kukicha.player")

class _MaxLevelFilter(logging.Filter):
    def __init__(self, max_level: int) -> None:
        super().__init__()
        self.max_level = max_level

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno < self.max_level

@dataclass(frozen=True, slots=True)
class PlayerServerOptions:
    config_path: Path
    database: Path
    ffmpeg_path: Path | None
    roots: tuple[Path, ...] = ()
    host: str = DEFAULT_PLAYER_HOST
    port: int = DEFAULT_PLAYER_PORT
    log_level: str = DEFAULT_PLAYER_LOG_LEVEL
    accent_color: str = DEFAULT_ACCENT_COLOR
    appearance: str = DEFAULT_APPEARANCE
    toast_timeout_ms: int = DEFAULT_TOAST_TIMEOUT_MS
    album_artist_split_patterns: tuple[str, ...] = DEFAULT_ALBUM_ARTIST_SPLIT_PATTERNS


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
    supported_keys: tuple[str, ...] = PLAYER_CONFIG_KEY_ORDER
    error: str = ""


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


def default_player_config_dir() -> Path:
    config_home = os.environ.get("XDG_CONFIG_HOME")
    if config_home:
        return Path(config_home).expanduser() / "kukicha"
    return Path.home() / ".config" / "kukicha"

def default_player_config_path() -> Path:
    return default_player_config_dir() / PLAYER_CONFIG_FILENAME

def load_player_options(config_path: str | Path | None = None) -> PlayerServerOptions:
    resolved_config_path, config_required = resolve_player_config_path(config_path)
    config_dir = resolved_config_path.parent
    config = read_player_config(resolved_config_path, required=config_required)

    log_level = parse_player_log_level(config.get("LogLevel", DEFAULT_PLAYER_LOG_LEVEL))
    database = parse_config_path(
        config.get("DatabasePath"),
        key="DatabasePath",
        base_dir=config_dir,
        default=config_dir / PLAYER_DATABASE_FILENAME,
    )
    ffmpeg_path = parse_optional_config_path(
        config.get("FFmpegPath", ""),
        key="FFmpegPath",
        base_dir=config_dir,
    )
    roots = parse_config_path_list(
        config.get("Roots", ()),
        key="Roots",
        base_dir=config_dir,
    )
    host = parse_player_host(config.get("Host", DEFAULT_PLAYER_HOST))
    port = parse_player_port(config.get("Port", DEFAULT_PLAYER_PORT))
    accent_color = parse_accent_color(config.get("AccentColor", DEFAULT_ACCENT_COLOR))
    appearance = parse_appearance(config.get("Appearance", DEFAULT_APPEARANCE))
    toast_timeout_ms = parse_positive_milliseconds(
        config.get("ToastTimeoutMs", DEFAULT_TOAST_TIMEOUT_MS),
        key="ToastTimeoutMs",
    )
    album_artist_split_patterns = parse_album_artist_split_patterns(
        config.get("AlbumArtistSplitPatterns", DEFAULT_ALBUM_ARTIST_SPLIT_PATTERNS)
    )

    return PlayerServerOptions(
        config_path=resolved_config_path,
        database=database,
        roots=roots,
        ffmpeg_path=ffmpeg_path,
        host=host,
        port=port,
        log_level=log_level,
        accent_color=accent_color,
        appearance=appearance,
        toast_timeout_ms=toast_timeout_ms,
        album_artist_split_patterns=album_artist_split_patterns,
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
                    for key in PLAYER_CONFIG_KEY_ORDER
                ),
            )
        )

    lines.extend(("", "Supported keys:"))
    lines.extend(f"  {key}" for key in summary.supported_keys)
    lines.extend(("", "Appearance accepts these values:"))
    lines.extend(f"  {name}" for name in APPEARANCE_THEMES)
    lines.extend(("", "AccentColor accepts these palette names or matching hex codes:"))
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
    values = {
        "LogLevel": options.log_level,
        "DatabasePath": str(options.database),
        "Roots": format_player_config_path_list(options.roots),
        "FFmpegPath": format_player_config_optional_path(options.ffmpeg_path),
        "Host": options.host,
        "Port": str(options.port),
        "AccentColor": options.accent_color,
        "Appearance": options.appearance,
        "ToastTimeoutMs": str(options.toast_timeout_ms),
        "AlbumArtistSplitPatterns": format_player_config_string_list(
            options.album_artist_split_patterns
        ),
    }
    value_items = {
        "Roots": tuple(str(root) for root in options.roots),
        "AlbumArtistSplitPatterns": options.album_artist_split_patterns,
    }
    return tuple(
        PlayerConfigValue(
            key=key,
            value=values[key],
            source=player_config_value_source(raw_config, key),
            items=value_items.get(key, ()),
        )
        for key in PLAYER_CONFIG_KEY_ORDER
    )

def player_config_status_label(path: Path, *, required: bool) -> str:
    if path.exists():
        return "found"
    if required:
        return "missing (startup would fail)"
    return "missing (defaults in effect)"

def player_config_value_source(config: dict[str, object], key: str) -> str:
    return "configured" if key in config else "default"

def format_player_config_optional_path(path: Path | None) -> str:
    return str(path) if path is not None else "<unset>"

def format_player_config_string_list(values: tuple[str, ...]) -> str:
    return "[" + ", ".join(repr(value) for value in values) + "]"

def format_player_config_path_list(values: tuple[Path, ...]) -> str:
    return "[" + ", ".join(repr(str(value)) for value in values) + "]"

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
        return resolve_path(default_player_config_path()), False
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
    return config

def parse_player_log_level(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlayerConfigError("LogLevel must be a non-empty string")
    level_name = value.strip().upper()
    levels = logging.getLevelNamesMapping()
    if level_name not in levels:
        raise PlayerConfigError(f"unsupported LogLevel: {value}")
    return str(logging.getLevelName(levels[level_name]))

def parse_player_host(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlayerConfigError("Host must be a non-empty string")
    return value.strip()

def parse_player_port(value: object) -> int:
    if not isinstance(value, int):
        raise PlayerConfigError("Port must be an integer")
    if value < 1 or value > 65535:
        raise PlayerConfigError("Port must be between 1 and 65535")
    return value

def parse_accent_color(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlayerConfigError("AccentColor must be a non-empty string")
    name = normalize_accent_color_name(value)
    if name not in ACCENT_COLOR_CODES:
        raise PlayerConfigError(f"AccentColor must be a supported palette color: {value}")
    return name

def parse_appearance(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlayerConfigError("Appearance must be a non-empty string")
    appearance = value.strip().lower()
    if appearance not in APPEARANCE_THEMES:
        raise PlayerConfigError(f"Appearance must be one of: {', '.join(APPEARANCE_THEMES)}")
    return appearance

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
    return APPEARANCE_THEMES.get(name.strip().lower(), APPEARANCE_THEMES[DEFAULT_APPEARANCE])

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
        raise PlayerConfigError("AlbumArtistSplitPatterns must be an array of strings")
    for item in value:
        if not isinstance(item, str):
            raise PlayerConfigError("AlbumArtistSplitPatterns must be an array of strings")
    return normalize_album_artist_split_patterns(value)

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
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise PlayerConfigError(f"{key} must be an array of non-empty strings")
        path = resolve_path(Path(item.strip()).expanduser(), base_dir=base_dir)
        key_value = str(path)
        if key_value in seen:
            raise PlayerConfigError(f"{key} must not contain duplicate paths: {path}")
        seen.add(key_value)
        paths.append(path)
    return tuple(paths)

def resolve_path(path: Path, *, base_dir: Path | None = None) -> Path:
    resolved = path
    if not resolved.is_absolute():
        resolved = (base_dir or Path.cwd()) / resolved
    return resolved.resolve(strict=False)

def validate_player_startup(options: PlayerServerOptions) -> None:
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
    environment.filters["album_art_url"] = album_art_url
    environment.filters["album_summary"] = album_summary_text
    environment.filters["display_album_title"] = display_album_title
    return environment
