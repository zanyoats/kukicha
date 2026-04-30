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
DEFAULT_TOAST_TIMEOUT_MS = 10000
DEFAULT_LINKED_TOAST_TIMEOUT_MS = 25000
DEFAULT_ACCENT_COLOR = "sienna"
PLAYER_CONFIG_FILENAME = "kukicha.toml"
PLAYER_DATABASE_FILENAME = "kukicha.sqlite"
PLAYER_CONFIG_KEY_ORDER = (
    "LogLevel",
    "DatabasePath",
    "FFmpegPath",
    "Host",
    "Port",
    "AccentColor",
    "ToastTimeoutMs",
    "LinkedToastTimeoutMs",
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
    host: str = DEFAULT_PLAYER_HOST
    port: int = DEFAULT_PLAYER_PORT
    log_level: str = DEFAULT_PLAYER_LOG_LEVEL
    accent_color: str = DEFAULT_ACCENT_COLOR
    toast_timeout_ms: int = DEFAULT_TOAST_TIMEOUT_MS
    linked_toast_timeout_ms: int = DEFAULT_LINKED_TOAST_TIMEOUT_MS
    album_artist_split_patterns: tuple[str, ...] = DEFAULT_ALBUM_ARTIST_SPLIT_PATTERNS


@dataclass(frozen=True, slots=True)
class PlayerConfigValue:
    key: str
    value: str
    source: str


@dataclass(frozen=True, slots=True)
class PlayerConfigSummary:
    path: Path
    status: str
    values: tuple[PlayerConfigValue, ...] = ()
    supported_keys: tuple[str, ...] = PLAYER_CONFIG_KEY_ORDER
    error: str = ""


CSS_NAMED_COLORS = frozenset(
    (
        "aliceblue", "antiquewhite", "aqua", "aquamarine", "azure",
        "beige", "bisque", "black", "blanchedalmond", "blue", "blueviolet",
        "brown", "burlywood", "cadetblue", "chartreuse", "chocolate",
        "coral", "cornflowerblue", "cornsilk", "crimson", "cyan",
        "darkblue", "darkcyan", "darkgoldenrod", "darkgray", "darkgreen",
        "darkgrey", "darkkhaki", "darkmagenta", "darkolivegreen",
        "darkorange", "darkorchid", "darkred", "darksalmon",
        "darkseagreen", "darkslateblue", "darkslategray", "darkslategrey",
        "darkturquoise", "darkviolet", "deeppink", "deepskyblue",
        "dimgray", "dimgrey", "dodgerblue", "firebrick", "floralwhite",
        "forestgreen", "fuchsia", "gainsboro", "ghostwhite", "gold",
        "goldenrod", "gray", "green", "greenyellow", "grey", "honeydew",
        "hotpink", "indianred", "indigo", "ivory", "khaki", "lavender",
        "lavenderblush", "lawngreen", "lemonchiffon", "lightblue",
        "lightcoral", "lightcyan", "lightgoldenrodyellow", "lightgray",
        "lightgreen", "lightgrey", "lightpink", "lightsalmon",
        "lightseagreen", "lightskyblue", "lightslategray",
        "lightslategrey", "lightsteelblue", "lightyellow", "lime",
        "limegreen", "linen", "magenta", "maroon", "mediumaquamarine",
        "mediumblue", "mediumorchid", "mediumpurple", "mediumseagreen",
        "mediumslateblue", "mediumspringgreen", "mediumturquoise",
        "mediumvioletred", "midnightblue", "mintcream", "mistyrose",
        "moccasin", "navajowhite", "navy", "oldlace", "olive",
        "olivedrab", "orange", "orangered", "orchid", "palegoldenrod",
        "palegreen", "paleturquoise", "palevioletred", "papayawhip",
        "peachpuff", "peru", "pink", "plum", "powderblue", "purple",
        "rebeccapurple", "red", "rosybrown", "royalblue", "saddlebrown",
        "salmon", "sandybrown", "seagreen", "seashell", "sienna",
        "silver", "skyblue", "slateblue", "slategray", "slategrey",
        "snow", "springgreen", "steelblue", "tan", "teal", "thistle",
        "tomato", "turquoise", "violet", "wheat", "white", "whitesmoke",
        "yellow", "yellowgreen",
    )
)


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
    host = parse_player_host(config.get("Host", DEFAULT_PLAYER_HOST))
    port = parse_player_port(config.get("Port", DEFAULT_PLAYER_PORT))
    accent_color = parse_accent_color(config.get("AccentColor", DEFAULT_ACCENT_COLOR))
    toast_timeout_ms = parse_positive_milliseconds(
        config.get("ToastTimeoutMs", DEFAULT_TOAST_TIMEOUT_MS),
        key="ToastTimeoutMs",
    )
    linked_toast_timeout_ms = parse_positive_milliseconds(
        config.get("LinkedToastTimeoutMs", DEFAULT_LINKED_TOAST_TIMEOUT_MS),
        key="LinkedToastTimeoutMs",
    )
    album_artist_split_patterns = parse_album_artist_split_patterns(
        config.get("AlbumArtistSplitPatterns", DEFAULT_ALBUM_ARTIST_SPLIT_PATTERNS)
    )

    return PlayerServerOptions(
        config_path=resolved_config_path,
        database=database,
        ffmpeg_path=ffmpeg_path,
        host=host,
        port=port,
        log_level=log_level,
        accent_color=accent_color,
        toast_timeout_ms=toast_timeout_ms,
        linked_toast_timeout_ms=linked_toast_timeout_ms,
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
    lines.extend(("", "AccentColor accepts any valid CSS named color."))
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
        "FFmpegPath": format_player_config_optional_path(options.ffmpeg_path),
        "Host": options.host,
        "Port": str(options.port),
        "AccentColor": options.accent_color,
        "ToastTimeoutMs": str(options.toast_timeout_ms),
        "LinkedToastTimeoutMs": str(options.linked_toast_timeout_ms),
        "AlbumArtistSplitPatterns": format_player_config_string_list(
            options.album_artist_split_patterns
        ),
    }
    return tuple(
        PlayerConfigValue(
            key=key,
            value=values[key],
            source=player_config_value_source(raw_config, key),
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
    if name not in CSS_NAMED_COLORS:
        raise PlayerConfigError(f"AccentColor must be a valid CSS named color: {value}")
    return name

def normalize_accent_color_name(value: str) -> str:
    return value.strip().lower()

def player_accent_color(name: str) -> str:
    color = normalize_accent_color_name(name)
    return color if color in CSS_NAMED_COLORS else DEFAULT_ACCENT_COLOR

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
