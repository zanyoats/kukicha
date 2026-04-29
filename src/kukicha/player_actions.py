from __future__ import annotations

from datetime import datetime, timezone

from .use_case import library_root_filter_label
from .player_runtime import PlayerActionRecord


def action_kind_label(kind: str) -> str:
    labels = {
        "add_root": "Add and Scan",
        "delete_root": "Delete Root",
        "edit_album": "Edit Tags",
        "edit_album_musicbrainz": "Edit MusicBrainz IDs",
        "update_playlist_file": "Update Playlist File",
        "rescan_root": "Rescan Root",
    }
    return labels.get(kind, " ".join(part.capitalize() for part in kind.split("_") if part))


def action_status_label(status: str) -> str:
    labels = {
        "accepted": "Accepted",
        "succeeded": "Succeeded",
        "failed": "Failed",
    }
    return labels.get(status, " ".join(part.capitalize() for part in status.split("_") if part))


def parse_action_timestamp(created_at: str) -> datetime | None:
    try:
        return datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        return None


def format_action_timestamp(created_at: str) -> str:
    parsed = parse_action_timestamp(created_at)
    if parsed is None:
        return created_at
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def humanize_action_context_key(key: str) -> str:
    labels = {
        "album": "Album",
        "album_artist": "Album Artist",
        "operation": "Operation",
        "path": "Root",
        "playlist": "Playlist",
        "playlist_path": "Playlist File",
        "track": "Track",
        "root_position": "Root",
        "tracks_updated": "Tracks",
        "tracks_scanned": "Tracks",
        "albums_scanned": "Albums",
        "files_missing_required_tags": "Missing Tags",
        "duration_seconds": "Duration",
        "error": "Error",
    }
    return labels.get(key, " ".join(part.capitalize() for part in key.split("_") if part))


def format_action_context_value(key: str, value: object) -> str:
    if key == "path":
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


def action_context_items(context: dict[str, object]) -> list[dict[str, str]]:
    preferred_order = (
        "path",
        "playlist_path",
        "playlist",
        "track",
        "operation",
        "album",
        "album_artist",
        "tracks_updated",
        "tracks_scanned",
        "albums_scanned",
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
        rendered = format_action_context_value(key, value)
        if not rendered:
            continue
        items.append(
            {
                "label": humanize_action_context_key(key),
                "value": rendered,
            }
        )
    return items


def action_message_text(action: PlayerActionRecord) -> str:
    if action.kind not in {"add_root", "delete_root", "rescan_root"}:
        return action.message

    root_path = action.context.get("path")
    if not isinstance(root_path, str) or not root_path:
        return action.message

    root_label = library_root_filter_label(root_path)
    if action.kind == "add_root":
        if action.message.startswith("Add and scan accepted for "):
            return f"Add and scan accepted for {root_label}."
        if action.message.startswith("Add and scan completed for "):
            return f"Add and scan completed for {root_label}."
        if action.message.startswith("Add and scan failed for "):
            return f"Add and scan failed for {root_label}."
        if action.message.startswith("Add and scan could not start for "):
            return f"Add and scan could not start for {root_label}."
    if action.kind == "delete_root":
        if action.message.startswith("Delete accepted for "):
            return f"Delete accepted for {root_label}."
        if action.message.startswith("Delete completed for "):
            return f"Delete completed for {root_label}."
        if action.message.startswith("Delete failed for "):
            return f"Delete failed for {root_label}."
        if action.message.startswith("Delete could not start for "):
            return f"Delete could not start for {root_label}."
    if action.kind == "rescan_root":
        if action.message.startswith("Rescan accepted for "):
            return f"Rescan accepted for {root_label}."
        if action.message.startswith("Rescan completed for "):
            return f"Rescan completed for {root_label}."
        if action.message.startswith("Rescan failed for "):
            return f"Rescan failed for {root_label}."
        if action.message.startswith("Rescan could not start for "):
            return f"Rescan could not start for {root_label}."
    return action.message


def action_payload(action: PlayerActionRecord) -> dict[str, object]:
    return {
        "action_id": action.action_id,
        "created_at": action.created_at,
        "created_at_label": format_action_timestamp(action.created_at),
        "kind": action.kind,
        "kind_label": action_kind_label(action.kind),
        "status": action.status,
        "status_label": action_status_label(action.status),
        "message": action_message_text(action),
        "context_items": action_context_items(action.context),
    }


def notification_day_key_and_label(created_at: str) -> tuple[str, str]:
    parsed = parse_action_timestamp(created_at)
    if parsed is None:
        fallback = created_at.strip() or "Unknown Date"
        return fallback, fallback

    local_time = parsed.astimezone()
    return (
        local_time.date().isoformat(),
        f"{local_time.strftime('%A, %B')} {local_time.day}, {local_time.year}",
    )


def group_notification_payloads_by_day(
    notifications: list[dict[str, object]],
) -> list[dict[str, object]]:
    groups: list[dict[str, object]] = []
    for notification in notifications:
        created_at = notification.get("created_at")
        day_key, day_label = notification_day_key_and_label(
            created_at if isinstance(created_at, str) else ""
        )
        if not groups or groups[-1].get("day_key") != day_key:
            groups.append(
                {
                    "day_key": day_key,
                    "day_label": day_label,
                    "notifications": [notification],
                }
            )
            continue

        day_notifications = groups[-1].get("notifications")
        if isinstance(day_notifications, list):
            day_notifications.append(notification)
    return groups
