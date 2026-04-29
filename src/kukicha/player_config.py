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

from .display import display_album_title
from .player_errors import PlayerConfigError
from .player_navigation import album_art_url, album_summary_text, album_url
from .use_case import prepare_player_database

DEFAULT_PLAYER_LOG_LEVEL = "DEBUG"
DEFAULT_PLAYER_HOST = "127.0.0.1"
DEFAULT_PLAYER_PORT = 65042
PLAYER_CONFIG_FILENAME = "kukicha.toml"
PLAYER_DATABASE_FILENAME = "kukicha.sqlite"
PLAYER_CONFIG_KEY_ORDER = (
    "LogLevel",
    "DatabasePath",
    "FFmpegPath",
    "Host",
    "Port",
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

    return PlayerServerOptions(
        config_path=resolved_config_path,
        database=database,
        ffmpeg_path=ffmpeg_path,
        host=host,
        port=port,
        log_level=log_level,
    )

def player_config_help_text(config_path: str | Path | None = None) -> str:
    resolved_config_path, config_required = resolve_player_config_path(config_path)
    raw_config: dict[str, object] = {}
    lines = [
        "Config file:",
        f"  status: {player_config_status_label(resolved_config_path, required=config_required)}",
        f"  path: {resolved_config_path}",
    ]

    try:
        if config_required and not resolved_config_path.exists():
            raise PlayerConfigError(f"config file does not exist: {resolved_config_path}")
        raw_config = read_player_config(resolved_config_path, required=False)
        options = load_player_options(resolved_config_path if config_path is not None else None)
    except PlayerConfigError as error:
        lines.append(f"  error: {error}")
    else:
        lines.extend(
            (
                "",
                "Current values:",
                f"  LogLevel: {options.log_level} ({player_config_value_source(raw_config, 'LogLevel')})",
                f"  DatabasePath: {options.database} ({player_config_value_source(raw_config, 'DatabasePath')})",
                f"  FFmpegPath: {format_player_config_optional_path(options.ffmpeg_path)} ({player_config_value_source(raw_config, 'FFmpegPath')})",
                f"  Host: {options.host} ({player_config_value_source(raw_config, 'Host')})",
                f"  Port: {options.port} ({player_config_value_source(raw_config, 'Port')})",
            )
        )

    lines.extend(("", "Supported keys:"))
    lines.extend(f"  {key}" for key in PLAYER_CONFIG_KEY_ORDER)
    return "\n".join(lines)

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
