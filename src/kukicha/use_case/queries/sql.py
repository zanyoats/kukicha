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


def album_paths_sql(root_positions: tuple[int, ...]) -> str:
    root_sql, _ = root_scope_clause("library_tracks", root_positions)
    return f"""
        SELECT path
        FROM library_tracks
        WHERE album_id = ?{root_sql}
        ORDER BY path COLLATE NOCASE, track_id
        """


def album_paths_params(album_id: str, root_positions: tuple[int, ...]) -> list[object]:
    _, root_params = root_scope_clause("library_tracks", root_positions)
    return [album_id, *root_params]
