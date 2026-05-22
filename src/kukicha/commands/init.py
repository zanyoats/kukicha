from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
import sys

from .._compat import tomllib
from ..player_auth import hash_password
from ..player_config import (
    DEFAULT_AUTH_COOKIE_MAX_AGE,
    DEFAULT_AUTH_COOKIE_NAME,
    PLAYER_CONFIG_KEYS,
    default_player_config_path,
    read_player_config,
    resolve_path,
)
from ..player_errors import PlayerConfigError


def run_init(args: argparse.Namespace) -> int:
    config_path = resolve_path(args.config or default_player_config_path())
    try:
        initialize_player_config(config_path)
    except PlayerConfigError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    print(f"Initialized kukicha config: {config_path}")
    return 0


def run_auth_password(args: argparse.Namespace) -> int:
    config_path = resolve_path(args.config or default_player_config_path())
    try:
        reset_auth_password(config_path)
    except PlayerConfigError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    print(
        "Updated kukicha password hash. Existing browser login cookies "
        "for this config have been invalidated."
    )
    return 0


def initialize_player_config(config_path: Path) -> None:
    stdin_text = read_stdin_text()
    existing_text = read_existing_config_text(config_path)
    if existing_text is not None and stdin_text.strip():
        raise PlayerConfigError(
            "cannot merge stdin config into an existing config file; edit the file directly"
        )

    if existing_text is not None:
        existing_config = read_player_config(config_path, required=True)
        if "auth" in existing_config:
            raise PlayerConfigError(f"config already contains [auth]: {config_path}")
        base_text = existing_text
    else:
        validate_init_extra_config(stdin_text, source="stdin")
        base_text = stdin_text

    username, password = init_credentials()
    password_hash_file = config_path.parent / "password.hash"
    write_password_hash(password_hash_file, hash_password(password))
    write_config_with_auth(
        config_path,
        base_text,
        username=username,
        password_hash_file=password_hash_file,
    )


def reset_auth_password(config_path: Path) -> None:
    from ..player_config import load_player_options

    options = load_player_options(config_path, validate_credential_files=False)
    if options.auth is None:
        raise PlayerConfigError(f"config does not contain [auth]: {config_path}")
    password = new_password()
    write_password_hash(options.auth.password_hash_file, hash_password(password))


def read_stdin_text() -> str:
    if sys.stdin.isatty():
        return ""
    return sys.stdin.read()


def read_existing_config_text(config_path: Path) -> str | None:
    if not config_path.exists():
        return None
    try:
        return config_path.read_text(encoding="utf-8")
    except OSError as error:
        raise PlayerConfigError(f"failed to read existing config {config_path}: {error}") from error


def validate_init_extra_config(text: str, *, source: str) -> None:
    if not text.strip():
        return
    try:
        config = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise PlayerConfigError(f"invalid TOML from {source}: {error}") from error
    if "auth" in config:
        raise PlayerConfigError(f"{source} config must not contain an [auth] section")
    unknown_keys = sorted(set(config) - PLAYER_CONFIG_KEYS)
    if unknown_keys:
        raise PlayerConfigError(
            f"unsupported config key(s) from {source}: " + ", ".join(unknown_keys)
        )


def init_credentials() -> tuple[str, str]:
    env_username = os.environ.get("KUKICHA_USERNAME")
    env_password = os.environ.get("KUKICHA_PASSWORD")
    if env_username is not None or env_password is not None:
        if not env_username or not env_username.strip():
            raise PlayerConfigError("KUKICHA_USERNAME must be set for automation mode")
        if not env_password:
            raise PlayerConfigError("KUKICHA_PASSWORD must be set for automation mode")
        return env_username.strip(), env_password

    username = input("Username: ").strip()
    if not username:
        raise PlayerConfigError("username must not be empty")
    password = getpass.getpass("Password: ")
    if not password:
        raise PlayerConfigError("password must not be empty")
    confirm_password = getpass.getpass("Confirm password: ")
    if password != confirm_password:
        raise PlayerConfigError("passwords did not match")
    return username, password


def new_password() -> str:
    env_password = os.environ.get("KUKICHA_PASSWORD")
    if env_password is not None:
        if not env_password:
            raise PlayerConfigError("KUKICHA_PASSWORD must be set for automation mode")
        return env_password

    password = getpass.getpass("New password: ")
    if not password:
        raise PlayerConfigError("password must not be empty")
    confirm_password = getpass.getpass("Confirm password: ")
    if password != confirm_password:
        raise PlayerConfigError("passwords did not match")
    return password


def write_password_hash(path: Path, password_hash: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, f"{password_hash}\n".encode("utf-8"))
        finally:
            os.close(fd)
        path.chmod(0o600)
    except OSError as error:
        raise PlayerConfigError(f"failed to write password hash file {path}: {error}") from error

    if hasattr(os, "getuid"):
        stat_result = path.stat()
        if stat_result.st_uid != os.getuid():
            raise PlayerConfigError(f"password hash file must be owned by the current user: {path}")


def write_config_with_auth(
    path: Path,
    base_text: str,
    *,
    username: str,
    password_hash_file: Path,
) -> None:
    text = base_text.rstrip()
    auth_section = "\n".join(
        (
            "[auth]",
            f"username = {toml_string(username)}",
            f"password_hash_file = {toml_string(config_path_text(password_hash_file))}",
            f"cookie_max_age = {toml_string(DEFAULT_AUTH_COOKIE_MAX_AGE)}",
            f"cookie_name = {toml_string(DEFAULT_AUTH_COOKIE_NAME)}",
        )
    )
    output = f"{text}\n\n{auth_section}\n" if text else f"{auth_section}\n"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(output, encoding="utf-8")
    except OSError as error:
        raise PlayerConfigError(f"failed to write config file {path}: {error}") from error


def config_path_text(path: Path) -> str:
    home = Path.home().resolve(strict=False)
    resolved = path.expanduser().resolve(strict=False)
    try:
        relative = resolved.relative_to(home)
    except ValueError:
        return str(resolved)
    return str(Path("~") / relative)


def toml_string(value: str) -> str:
    return json.dumps(value)
