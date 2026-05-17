from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path
import sys

from ..player_config import (
    DEFAULT_OPEN_SUBSONIC_MOUNT_PREFIX,
    DEFAULT_OPEN_SUBSONIC_SECRET_FILENAME,
    default_player_config_path,
    load_player_options,
    parse_open_subsonic_mount_prefix,
    read_player_config,
    resolve_path,
)
from ..player_errors import PlayerConfigError
from .init import config_path_text, read_existing_config_text, toml_string


def run_open_subsonic_init(args: argparse.Namespace) -> int:
    config_path = resolve_path(args.config or default_player_config_path())
    try:
        initialize_open_subsonic_config(config_path)
    except PlayerConfigError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    print(f"Initialized kukicha OpenSubsonic config: {config_path}")
    return 0


def run_open_subsonic_password(args: argparse.Namespace) -> int:
    config_path = resolve_path(args.config or default_player_config_path())
    try:
        reset_open_subsonic_password(config_path)
    except PlayerConfigError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    print("Updated kukicha OpenSubsonic password.")
    return 0


def initialize_open_subsonic_config(config_path: Path) -> None:
    config = read_player_config(config_path, required=True)
    if "auth" not in config:
        raise PlayerConfigError("[auth] section is required; run `kukicha init`")
    if "opensubsonic" in config:
        raise PlayerConfigError(f"config already contains [opensubsonic]: {config_path}")
    load_player_options(config_path)

    existing_text = read_existing_config_text(config_path)
    if existing_text is None:
        raise PlayerConfigError(f"config file does not exist: {config_path}")

    mount_prefix, password = init_open_subsonic_credentials()
    secret_file = config_path.parent / DEFAULT_OPEN_SUBSONIC_SECRET_FILENAME
    write_open_subsonic_secret(secret_file, password)
    write_config_with_open_subsonic(
        config_path,
        existing_text,
        mount_prefix=mount_prefix,
        secret_file=secret_file,
    )


def reset_open_subsonic_password(config_path: Path) -> None:
    options = load_player_options(config_path)
    if options.opensubsonic is None:
        raise PlayerConfigError(f"config does not contain [opensubsonic]: {config_path}")
    write_open_subsonic_secret(options.opensubsonic.secret_file, new_open_subsonic_password())


def init_open_subsonic_credentials() -> tuple[str, str]:
    env_password = os.environ.get("OPENSUBSONIC_PASSWORD")
    env_mount = os.environ.get("OPENSUBSONIC_MOUNT")
    if env_password is not None or env_mount is not None:
        if env_mount is None:
            raise PlayerConfigError("OPENSUBSONIC_MOUNT must be set for automation mode")
        if env_password is None or not env_password:
            raise PlayerConfigError("OPENSUBSONIC_PASSWORD must be set for automation mode")
        return parse_open_subsonic_mount_prefix(env_mount), env_password

    mount_value = input(
        f"OpenSubsonic mount prefix [{DEFAULT_OPEN_SUBSONIC_MOUNT_PREFIX}]: "
    ).strip()
    mount_prefix = parse_open_subsonic_mount_prefix(
        mount_value or DEFAULT_OPEN_SUBSONIC_MOUNT_PREFIX
    )
    return mount_prefix, new_open_subsonic_password()


def new_open_subsonic_password() -> str:
    env_password = os.environ.get("OPENSUBSONIC_PASSWORD")
    if env_password is not None:
        if not env_password:
            raise PlayerConfigError("OPENSUBSONIC_PASSWORD must be set for automation mode")
        return env_password

    password = getpass.getpass("OpenSubsonic password: ")
    if not password:
        raise PlayerConfigError("password must not be empty")
    confirm_password = getpass.getpass("Confirm OpenSubsonic password: ")
    if password != confirm_password:
        raise PlayerConfigError("passwords did not match")
    return password


def write_open_subsonic_secret(path: Path, password: str) -> None:
    if not password:
        raise PlayerConfigError("password must not be empty")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, f"{password}\n".encode("utf-8"))
        finally:
            os.close(fd)
        path.chmod(0o600)
    except OSError as error:
        raise PlayerConfigError(
            f"failed to write OpenSubsonic secret file {path}: {error}"
        ) from error

    if hasattr(os, "getuid"):
        stat_result = path.stat()
        if stat_result.st_uid != os.getuid():
            raise PlayerConfigError(
                f"OpenSubsonic secret file must be owned by the current user: {path}"
            )


def write_config_with_open_subsonic(
    path: Path,
    base_text: str,
    *,
    mount_prefix: str,
    secret_file: Path,
) -> None:
    text = base_text.rstrip()
    open_subsonic_section = "\n".join(
        (
            "[opensubsonic]",
            f"mount_prefix = {toml_string(mount_prefix)}",
            f"secret_file = {toml_string(config_path_text(secret_file))}",
        )
    )
    output = (
        f"{text}\n\n{open_subsonic_section}\n"
        if text
        else f"{open_subsonic_section}\n"
    )
    try:
        path.write_text(output, encoding="utf-8")
    except OSError as error:
        raise PlayerConfigError(f"failed to write config file {path}: {error}") from error
