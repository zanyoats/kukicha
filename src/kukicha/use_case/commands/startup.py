from __future__ import annotations

from pathlib import Path

from ..database import connect_database


def prepare_player_database(database: Path) -> None:
    with connect_database(database):
        pass
