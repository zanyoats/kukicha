from __future__ import annotations

from pathlib import Path
import sqlite3
from time import perf_counter
from typing import Any

from .player_runtime import PlayerJobCancelToken, PlayerJobResult, PlayerJobRecord, PlayerRuntime
from .scanner import normalize_playlist_resource


def start_playlist_file_update_job(
    runtime: PlayerRuntime,
    job: Any,
) -> PlayerJobRecord:
    context = playlist_file_update_context(job)
    return runtime.enqueue_job(
        kind="update_playlist_file",
        queued_message=f"Update playlist file queued for {job.playlist_name}.",
        running_message=f"Update playlist file running for {job.playlist_name}.",
        canceled_message=f"Update playlist file canceled for {job.playlist_name}.",
        failed_message=f"Update playlist file failed for {job.playlist_name}.",
        context=context,
        runner=lambda cancel_token: run_playlist_file_update_job(runtime, job, cancel_token),
    )


def run_playlist_file_update_job(
    runtime: PlayerRuntime,
    job: Any,
    cancel_token: PlayerJobCancelToken,
) -> PlayerJobResult:
    started_at = perf_counter()
    with runtime.playlist_file_lock:
        cancel_token.raise_if_canceled()
        update_playlist_file_for_membership(job)
    duration_seconds = perf_counter() - started_at
    return PlayerJobResult(
        message=f"Update playlist file completed for {job.playlist_name}.",
        context=playlist_file_update_context(
            job,
            duration_seconds=duration_seconds,
        ),
    )


def update_playlist_file_for_membership(job: Any) -> None:
    playlist_path = Path(job.playlist_path)
    if job.checked:
        append_tracked_song_to_playlist_file(
            playlist_path,
            {
                "path": job.track_path,
                "artist": job.artist,
                "album_artist": job.album_artist,
                "title": job.title,
                "duration_seconds": job.duration_seconds,
            },
        )
    else:
        remove_tracked_song_from_playlist_file(playlist_path, job.track_path)


def playlist_file_update_context(
    job: Any,
    *,
    duration_seconds: float | None = None,
    error: str | None = None,
) -> dict[str, object]:
    context: dict[str, object] = {
        "playlist_path": job.playlist_path,
        "playlist": job.playlist_name,
        "playlist_id": job.playlist_id,
        "track": job.title,
        "track_id": job.track_id,
        "operation": "Add track" if job.checked else "Remove track",
    }
    if duration_seconds is not None:
        context["duration_seconds"] = duration_seconds
    if error:
        context["error"] = error
    return context


def playlist_file_encoding(path: Path) -> str:
    return "ascii" if path.suffix.casefold() == ".m3u" else "utf-8"


def read_playlist_file_for_edit(path: Path) -> tuple[str, str]:
    encoding = playlist_file_encoding(path)
    data = path.read_bytes()
    try:
        return data.decode(encoding), encoding
    except UnicodeDecodeError as error:
        raise ValueError(f"playlist file is not valid {encoding}: {path}") from error


def write_playlist_file_for_edit(path: Path, text: str, encoding: str) -> None:
    try:
        with path.open("w", encoding=encoding, newline="") as handle:
            handle.write(text)
    except UnicodeEncodeError as error:
        raise ValueError(f"playlist file cannot be written as {encoding}: {path}") from error


def append_tracked_song_to_playlist_file(path: Path, track_row: sqlite3.Row) -> None:
    text, encoding = read_playlist_file_for_edit(path)
    newline = "\r\n" if "\r\n" in text else "\n"
    prefix = "" if not text or text.endswith(("\n", "\r")) else newline
    duration = playlist_extinf_duration(track_row["duration_seconds"])
    title = playlist_extinf_title(track_row)
    entry = f"{prefix}#EXTINF:{duration},{title}{newline}{track_row['path']}{newline}"
    write_playlist_file_for_edit(path, text + entry, encoding)


def remove_tracked_song_from_playlist_file(path: Path, track_path: str) -> None:
    text, encoding = read_playlist_file_for_edit(path)
    lines = text.splitlines(keepends=True)
    indexes_to_remove: set[int] = set()
    for index, line in enumerate(lines):
        if not playlist_resource_line_matches(line, path, track_path):
            continue
        start = index
        while start > 0 and is_playlist_item_metadata_line(lines[start - 1]):
            start -= 1
        indexes_to_remove.update(range(start, index + 1))

    if not indexes_to_remove:
        raise ValueError("track path was not found in playlist file")

    updated = "".join(
        line for index, line in enumerate(lines) if index not in indexes_to_remove
    )
    write_playlist_file_for_edit(path, updated, encoding)


def playlist_resource_line_matches(line: str, playlist_path: Path, track_path: str) -> bool:
    value = line.rstrip("\r\n").strip()
    if not value or value.startswith("#"):
        return False
    try:
        return normalize_playlist_resource(value, playlist_path) == str(
            Path(track_path).expanduser().resolve(strict=False)
        )
    except OSError:
        return value == track_path


def is_playlist_item_metadata_line(line: str) -> bool:
    value = line.strip().casefold()
    return value.startswith(
        (
            "#extinf:",
            "#extgenre:",
            "#extalbumarturl:",
        )
    )


def playlist_extinf_duration(value: object) -> str:
    try:
        duration = float(value)
    except (TypeError, ValueError):
        return "-1"
    if duration <= 0:
        return "-1"
    return str(round(duration))


def playlist_extinf_title(track_row: sqlite3.Row) -> str:
    title = str(track_row["title"] or Path(str(track_row["path"])).stem)
    artist = str(track_row["artist"] or track_row["album_artist"] or "").strip()
    return f"{artist} - {title}" if artist else title
