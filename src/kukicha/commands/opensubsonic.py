from __future__ import annotations

import argparse
import logging

from ..opensubsonic_web_adapter import serve_open_subsonic
from ..player_config import (
    DEFAULT_PLAYER_LOG_LEVEL,
    configure_player_logging,
    load_player_options,
)
from ..player_errors import PlayerConfigError


def run_open_subsonic(args: argparse.Namespace) -> int:
    configure_player_logging(DEFAULT_PLAYER_LOG_LEVEL)
    try:
        options = load_player_options(args.config)
    except PlayerConfigError as error:
        logging.getLogger("kukicha.player").error("%s", error)
        return 1

    if options.log_level != DEFAULT_PLAYER_LOG_LEVEL:
        configure_player_logging(options.log_level)
    return serve_open_subsonic(options)
