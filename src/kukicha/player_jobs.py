from __future__ import annotations

from datetime import datetime, timezone

from .player_runtime import PlayerJobRecord


def job_kind_label(kind: str) -> str:
    labels = {
        "add_root": "Add and Scan",
        "delete_root": "Delete Root",
        "edit_album": "Edit Tags",
        "edit_album_musicbrainz": "Edit MusicBrainz IDs",
        "update_playlist_file": "Update Playlist File",
        "rescan_library": "Rescan",
    }
    return labels.get(kind, " ".join(part.capitalize() for part in kind.split("_") if part))


def job_status_label(status: str) -> str:
    labels = {
        "queued": "Queued",
        "running": "Running",
        "succeeded": "Succeeded",
        "failed": "Failed",
        "canceled": "Canceled",
    }
    return labels.get(status, " ".join(part.capitalize() for part in status.split("_") if part))


def parse_job_timestamp(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def format_job_timestamp(value: str | None) -> str:
    if not value:
        return ""
    parsed = parse_job_timestamp(value)
    if parsed is None:
        return value
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def humanize_job_context_key(key: str) -> str:
    labels = {
        "album": "Album",
        "album_artist": "Album Artist",
        "operation": "Operation",
        "path": "Root",
        "playlist": "Playlist",
        "playlist_path": "Playlist File",
        "track": "Track",
        "root_position": "Root",
        "roots_scanned": "Roots",
        "tracks_updated": "Tracks",
        "tracks_scanned": "Tracks",
        "albums_scanned": "Albums",
        "playlists_scanned": "Playlists",
        "files_missing_required_tags": "Missing Tags",
        "duration_seconds": "Duration",
        "error": "Error",
    }
    return labels.get(key, " ".join(part.capitalize() for part in key.split("_") if part))


def format_job_context_value(key: str, value: object) -> str:
    if key == "path":
        from .use_case import library_root_filter_label

        return library_root_filter_label(str(value))
    if key == "root_position":
        try:
            return str(int(value) + 1)
        except (TypeError, ValueError):
            return str(value)
    if key == "duration_seconds":
        try:
            return f"{float(value):.2f} seconds"
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def job_context_items(context: dict[str, object]) -> list[dict[str, str]]:
    preferred_order = (
        "path",
        "playlist_path",
        "playlist",
        "track",
        "operation",
        "album",
        "album_artist",
        "roots_scanned",
        "tracks_updated",
        "tracks_scanned",
        "albums_scanned",
        "playlists_scanned",
        "files_missing_required_tags",
        "duration_seconds",
        "error",
    )
    keys = [key for key in preferred_order if key in context]
    keys.extend(
        sorted(
            key for key in context
            if key not in preferred_order and key != "root_position"
        )
    )
    items: list[dict[str, str]] = []
    for key in keys:
        value = context[key]
        if value is None:
            continue
        rendered = format_job_context_value(key, value)
        if not rendered:
            continue
        items.append(
            {
                "label": humanize_job_context_key(key),
                "value": rendered,
            }
        )
    return items


def job_message_text(job: PlayerJobRecord) -> str:
    if job.kind not in {"add_root", "delete_root", "rescan_root"}:
        return job.message

    root_path = job.context.get("path")
    if not isinstance(root_path, str) or not root_path:
        return job.message

    from .use_case import library_root_filter_label

    root_label = library_root_filter_label(root_path)
    replacements = {
        "Add and scan queued for ": f"Add and scan queued for {root_label}.",
        "Add and scan running for ": f"Add and scan running for {root_label}.",
        "Add and scan completed for ": f"Add and scan completed for {root_label}.",
        "Add and scan failed for ": f"Add and scan failed for {root_label}.",
        "Add and scan canceled for ": f"Add and scan canceled for {root_label}.",
        "Delete queued for ": f"Delete queued for {root_label}.",
        "Delete running for ": f"Delete running for {root_label}.",
        "Delete completed for ": f"Delete completed for {root_label}.",
        "Delete failed for ": f"Delete failed for {root_label}.",
        "Delete canceled for ": f"Delete canceled for {root_label}.",
        "Rescan queued for ": f"Rescan queued for {root_label}.",
        "Rescan running for ": f"Rescan running for {root_label}.",
        "Rescan completed for ": f"Rescan completed for {root_label}.",
        "Rescan failed for ": f"Rescan failed for {root_label}.",
        "Rescan canceled for ": f"Rescan canceled for {root_label}.",
    }
    for prefix, replacement in replacements.items():
        if job.message.startswith(prefix):
            return replacement
    return job.message


def job_payload(job: PlayerJobRecord) -> dict[str, object]:
    return {
        "job_id": job.job_id,
        "created_at": job.created_at,
        "created_at_label": format_job_timestamp(job.created_at),
        "updated_at": job.updated_at,
        "updated_at_label": format_job_timestamp(job.updated_at),
        "started_at": job.started_at or "",
        "started_at_label": format_job_timestamp(job.started_at),
        "finished_at": job.finished_at or "",
        "finished_at_label": format_job_timestamp(job.finished_at),
        "cancel_requested_at": job.cancel_requested_at or "",
        "kind": job.kind,
        "kind_label": job_kind_label(job.kind),
        "status": job.status,
        "status_label": job_status_label(job.status),
        "message": job_message_text(job),
        "reason": job.reason,
        "context_items": job_context_items(job.context),
    }


def job_day_key_and_label(created_at: str) -> tuple[str, str]:
    parsed = parse_job_timestamp(created_at)
    if parsed is None:
        fallback = created_at.strip() or "Unknown Date"
        return fallback, fallback

    local_time = parsed.astimezone()
    return (
        local_time.date().isoformat(),
        f"{local_time.strftime('%A, %B')} {local_time.day}, {local_time.year}",
    )


def group_job_payloads_by_day(
    jobs: list[dict[str, object]],
) -> list[dict[str, object]]:
    groups: list[dict[str, object]] = []
    for job in jobs:
        created_at = job.get("created_at")
        day_key, day_label = job_day_key_and_label(
            created_at if isinstance(created_at, str) else ""
        )
        if not groups or groups[-1].get("day_key") != day_key:
            groups.append(
                {
                    "day_key": day_key,
                    "day_label": day_label,
                    "jobs": [job],
                }
            )
            continue

        day_jobs = groups[-1].get("jobs")
        if isinstance(day_jobs, list):
            day_jobs.append(job)
    return groups
