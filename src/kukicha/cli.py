from __future__ import annotations

import argparse
from pathlib import Path
import sys
from textwrap import dedent
from typing import Sequence

from .commands.init import run_auth_password, run_init
from .commands.opensubsonic import (
    run_open_subsonic_init,
    run_open_subsonic_password,
)
from .commands.player import run_player
from .commands.tools import non_empty_string, run_bulk_tag_edit, run_copy_to_remote
from .commands.youtube_audio import add_youtube_download_audio_parser
from .app_metadata import kukicha_version
from .player_config import player_config_help_text


PLAYER_HELP = dedent(
    """\
    Usage patterns:
      kukicha                             Serve the built-in local player playlist.
      kukicha init                        Create or upgrade the player config with auth.
      kukicha auth password               Update the configured auth password.
      kukicha opensubsonic init           Mount OpenSubsonic endpoints on the player server.
      kukicha opensubsonic password       Update the OpenSubsonic password.
      kukicha tools bulk-tag-edit         Rewrite album tags below a folder.
      kukicha tools copy-to-remote        Copy a folder to a configured remote root.
      kukicha tools yt-download-audio     Download YouTube audio files.
    """
)


def build_player_help(argv: Sequence[str] | None) -> str:
    config_path = player_config_path_from_argv(argv)
    return f"{PLAYER_HELP.rstrip()}\n\n{player_config_help_text(config_path)}"


def player_config_path_from_argv(argv: Sequence[str] | None) -> Path | None:
    if not argv:
        return None

    tokens = list(argv)
    for index, token in enumerate(tokens):
        if token in {"-c", "--config"} and index + 1 < len(tokens):
            return Path(tokens[index + 1])
        if token.startswith("--config="):
            return Path(token.split("=", 1)[1])
        if token.startswith("-c") and token != "-c":
            return Path(token[2:])
    return None


def build_parser(argv: Sequence[str] | None = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kukicha",
        description="Serve the built-in local player playlist.",
        epilog=build_player_help(argv),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        help="Path to the TOML config file. Default: $XDG_CONFIG_HOME/kukicha/kukicha.toml or ~/.config/kukicha/kukicha.toml",
    )
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"%(prog)s {kukicha_version()}",
    )
    parser.set_defaults(func=run_player)
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
    init_parser = subparsers.add_parser(
        "init",
        help="Create or upgrade the player config with auth.",
    )
    init_parser.set_defaults(func=run_init)
    auth_parser = subparsers.add_parser(
        "auth",
        help="Manage Kukicha authentication.",
    )
    auth_subparsers = auth_parser.add_subparsers(dest="auth_command", metavar="COMMAND")
    auth_subparsers.required = True
    auth_password_parser = auth_subparsers.add_parser(
        "password",
        help="Update the configured auth password.",
    )
    auth_password_parser.set_defaults(func=run_auth_password)
    open_subsonic_parser = subparsers.add_parser(
        "opensubsonic",
        help="Manage OpenSubsonic integration.",
    )
    open_subsonic_subparsers = open_subsonic_parser.add_subparsers(
        dest="opensubsonic_command",
        metavar="COMMAND",
    )
    open_subsonic_subparsers.required = True
    open_subsonic_init_parser = open_subsonic_subparsers.add_parser(
        "init",
        help="Mount OpenSubsonic endpoints on the player server.",
    )
    open_subsonic_init_parser.set_defaults(func=run_open_subsonic_init)
    open_subsonic_password_parser = open_subsonic_subparsers.add_parser(
        "password",
        help="Update the OpenSubsonic password.",
    )
    open_subsonic_password_parser.set_defaults(func=run_open_subsonic_password)
    tools_parser = subparsers.add_parser(
        "tools",
        help="Run library maintenance tools.",
    )
    tools_subparsers = tools_parser.add_subparsers(dest="tools_command", metavar="COMMAND")
    tools_subparsers.required = True
    bulk_tag_edit_parser = tools_subparsers.add_parser(
        "bulk-tag-edit",
        help="Update album artist, album title, and genre tags below a folder.",
    )
    bulk_tag_edit_parser.add_argument(
        "--folder",
        type=Path,
        required=True,
        help="Folder to recurse for supported music files.",
    )
    bulk_tag_edit_parser.add_argument(
        "--album-artist",
        type=non_empty_string,
        required=True,
        help="Album artist tag value to write.",
    )
    bulk_tag_edit_parser.add_argument(
        "--album",
        type=non_empty_string,
        required=True,
        help="Album title tag value to write.",
    )
    bulk_tag_edit_parser.add_argument(
        "--genre",
        type=non_empty_string,
        required=True,
        help="Genre tag value to write.",
    )
    bulk_tag_edit_parser.set_defaults(func=run_bulk_tag_edit)
    copy_to_remote_parser = tools_subparsers.add_parser(
        "copy-to-remote",
        help="Copy a folder to a configured remote root.",
    )
    copy_to_remote_parser.add_argument(
        "--remote",
        type=non_empty_string,
        required=True,
        help="Name of the configured remote root to upload to.",
    )
    copy_to_remote_parser.add_argument(
        "--source",
        type=Path,
        required=True,
        help="Folder to upload.",
    )
    copy_to_remote_parser.add_argument(
        "--source-children",
        action="store_true",
        help="Upload each immediate child of --source under the remote prefix.",
    )
    copy_to_remote_parser.add_argument(
        "--delete-source",
        action="store_true",
        help="Delete successfully uploaded source folders or children.",
    )
    copy_to_remote_parser.add_argument(
        "--remote-workers",
        type=positive_integer,
        help="Number of parallel remote upload workers. Default: auto.",
    )
    copy_to_remote_parser.set_defaults(func=run_copy_to_remote)
    add_youtube_download_audio_parser(tools_subparsers)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser(arguments)
    args = parser.parse_args(arguments)
    return args.func(args)


def positive_integer(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("value must be an integer") from error
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than 0")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
