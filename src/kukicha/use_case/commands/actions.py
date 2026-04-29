from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from ..database import connect_database
from ...player_runtime import PlayerActionRecord


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_action_context(context: dict[str, object] | None) -> dict[str, object]:
    if not context:
        return {}

    normalized: dict[str, object] = {}
    for key, value in context.items():
        if not isinstance(key, str) or not key.strip():
            continue
        if value is None or isinstance(value, (str, int, float, bool)):
            normalized[key] = value
            continue
        normalized[key] = str(value)
    return normalized


def parse_action_context(raw: object) -> dict[str, object]:
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return normalize_action_context(parsed)


def record_player_action(
    database: Path,
    *,
    kind: str,
    status: str,
    message: str,
    context: dict[str, object] | None = None,
) -> PlayerActionRecord:
    created_at = utc_now_iso()
    action_context = normalize_action_context(context)
    connection = connect_database(database, create=False)
    try:
        cursor = connection.execute(
            """
            INSERT INTO player_actions (created_at, kind, status, message, context_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                created_at,
                kind,
                status,
                message,
                json.dumps(action_context, sort_keys=True),
            ),
        )
        connection.commit()
        action_id = int(cursor.lastrowid)
    finally:
        connection.close()

    return PlayerActionRecord(
        action_id=action_id,
        created_at=created_at,
        kind=kind,
        status=status,
        message=message,
        context=action_context,
    )


def list_player_actions(database: Path) -> tuple[PlayerActionRecord, ...]:
    connection = connect_database(database, create=False)
    try:
        rows = list(
            connection.execute(
                """
                SELECT action_id, created_at, kind, status, message, context_json
                FROM player_actions
                ORDER BY created_at DESC, action_id DESC
                """
            )
        )
    finally:
        connection.close()

    return tuple(
        PlayerActionRecord(
            action_id=int(row["action_id"]),
            created_at=str(row["created_at"]),
            kind=str(row["kind"]),
            status=str(row["status"]),
            message=str(row["message"]),
            context=parse_action_context(row["context_json"]),
        )
        for row in rows
    )
