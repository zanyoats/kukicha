from __future__ import annotations

from datetime import timezone
import logging

UTC = timezone.utc

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


def logging_level_names_mapping() -> dict[str, int]:
    get_mapping = getattr(logging, "getLevelNamesMapping", None)
    if get_mapping is not None:
        return get_mapping()
    return dict(logging._nameToLevel)
