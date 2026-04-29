from __future__ import annotations

from collections.abc import Iterable


TRACK_COLUMNS = """
    track_id,
    album_id,
    root_position,
    path,
    file_type,
    scan_error,
    artist,
    album_artist,
    composer,
    album,
    title,
    work,
    grouping,
    movement_name,
    is_compilation,
    track_number,
    disc_number,
    date,
    duration_seconds,
    bitrate
"""


def placeholders_for(values: Iterable[object]) -> str:
    return ", ".join("?" for _ in values)


def root_scope_clause(
    track_alias: str,
    root_positions: tuple[int, ...],
) -> tuple[str, list[object]]:
    if not root_positions:
        return "", []
    placeholders = placeholders_for(root_positions)
    return f" AND {track_alias}.root_position IN ({placeholders})", list(root_positions)
