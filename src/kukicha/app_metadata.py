from __future__ import annotations

from importlib.metadata import version


def kukicha_version() -> str:
    return version("kukicha")
