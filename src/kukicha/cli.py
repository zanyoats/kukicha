from __future__ import annotations

import argparse
from pathlib import Path
import sys
from textwrap import dedent
from typing import Sequence

from .commands.player import run_player
from .commands.tools import non_empty_string, run_bulk_tag_edit
from .commands.youtube_audio import add_youtube_download_audio_parser
from .player_config import player_config_help_text


PLAYER_HELP = dedent(
    """\
    Usage patterns:
      kukicha                             Serve the built-in local player playlist.
      kukicha tools bulk-tag-edit         Rewrite album tags below a folder.
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
    parser.set_defaults(func=run_player)
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
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
    add_youtube_download_audio_parser(tools_subparsers)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser(arguments)
    args = parser.parse_args(arguments)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
