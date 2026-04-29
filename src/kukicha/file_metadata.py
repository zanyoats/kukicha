from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import sys


def file_created_at(path: Path) -> str | None:
    try:
        stat_result = path.stat()
    except OSError:
        return None

    timestamp = getattr(stat_result, "st_birthtime", None)
    if timestamp is None and sys.platform == "win32":
        timestamp = getattr(stat_result, "st_ctime", None)
    if timestamp is None:
        return None

    try:
        return datetime.fromtimestamp(float(timestamp), UTC).isoformat()
    except (OSError, OverflowError, ValueError):
        return None
