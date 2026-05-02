from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from ..database import connect_database
from ...player_runtime import PlayerJobRecord


TERMINAL_JOB_STATUSES = frozenset({"succeeded", "failed", "canceled"})


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_job_context(context: dict[str, object] | None) -> dict[str, object]:
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


def parse_job_context(raw: object) -> dict[str, object]:
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return normalize_job_context(parsed)


def row_to_player_job(row: sqlite3.Row) -> PlayerJobRecord:
    return PlayerJobRecord(
        job_id=int(row["job_id"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        started_at=str(row["started_at"]) if row["started_at"] is not None else None,
        finished_at=str(row["finished_at"]) if row["finished_at"] is not None else None,
        cancel_requested_at=str(row["cancel_requested_at"])
        if row["cancel_requested_at"] is not None
        else None,
        kind=str(row["kind"]),
        status=str(row["status"]),
        message=str(row["message"]),
        reason=str(row["reason"] or ""),
        context=parse_job_context(row["context_json"]),
    )


def create_player_job(
    database: Path,
    *,
    kind: str,
    message: str,
    context: dict[str, object] | None = None,
) -> PlayerJobRecord:
    created_at = utc_now_iso()
    job_context = normalize_job_context(context)
    connection = connect_database(database)
    try:
        cursor = connection.execute(
            """
            INSERT INTO player_jobs (
                created_at,
                updated_at,
                kind,
                status,
                message,
                context_json
            ) VALUES (?, ?, ?, 'queued', ?, ?)
            """,
            (
                created_at,
                created_at,
                kind,
                message,
                json.dumps(job_context, sort_keys=True),
            ),
        )
        connection.commit()
        return get_player_job_from_connection(connection, int(cursor.lastrowid))
    finally:
        connection.close()


def get_player_job(database: Path, job_id: int) -> PlayerJobRecord:
    connection = connect_database(database)
    try:
        return get_player_job_from_connection(connection, job_id)
    finally:
        connection.close()


def get_player_job_from_connection(
    connection: sqlite3.Connection,
    job_id: int,
) -> PlayerJobRecord:
    row = connection.execute(
        """
        SELECT
            job_id,
            created_at,
            updated_at,
            started_at,
            finished_at,
            cancel_requested_at,
            kind,
            status,
            message,
            reason,
            context_json
        FROM player_jobs
        WHERE job_id = ?
        """,
        (job_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"job does not exist: {job_id}")
    return row_to_player_job(row)


def update_player_job(
    database: Path,
    job_id: int,
    *,
    status: str | None = None,
    message: str | None = None,
    reason: str | None = None,
    context: dict[str, object] | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
    cancel_requested_at: str | None = None,
) -> PlayerJobRecord:
    updated_at = utc_now_iso()
    assignments = ["updated_at = ?"]
    values: list[object] = [updated_at]
    if status is not None:
        assignments.append("status = ?")
        values.append(status)
    if message is not None:
        assignments.append("message = ?")
        values.append(message)
    if reason is not None:
        assignments.append("reason = ?")
        values.append(reason)
    if context is not None:
        assignments.append("context_json = ?")
        values.append(json.dumps(normalize_job_context(context), sort_keys=True))
    if started_at is not None:
        assignments.append("started_at = COALESCE(started_at, ?)")
        values.append(started_at)
    if finished_at is not None:
        assignments.append("finished_at = COALESCE(finished_at, ?)")
        values.append(finished_at)
    if cancel_requested_at is not None:
        assignments.append("cancel_requested_at = COALESCE(cancel_requested_at, ?)")
        values.append(cancel_requested_at)
    values.append(job_id)

    connection = connect_database(database, create=False)
    try:
        connection.execute(
            f"""
            UPDATE player_jobs
            SET {", ".join(assignments)}
            WHERE job_id = ?
            """,
            values,
        )
        connection.commit()
        return get_player_job_from_connection(connection, job_id)
    finally:
        connection.close()


def request_cancel_player_job(
    database: Path,
    job_id: int,
    *,
    reason: str = "Canceled by user.",
    message: str | None = None,
) -> PlayerJobRecord:
    requested_at = utc_now_iso()
    connection = connect_database(database, create=False)
    try:
        job = get_player_job_from_connection(connection, job_id)
        if job.status in TERMINAL_JOB_STATUSES:
            return job
        if job.status == "queued":
            connection.execute(
                """
                UPDATE player_jobs
                SET status = 'canceled',
                    updated_at = ?,
                    finished_at = ?,
                    cancel_requested_at = COALESCE(cancel_requested_at, ?),
                    message = ?,
                    reason = ?
                WHERE job_id = ?
                """,
                (
                    requested_at,
                    requested_at,
                    requested_at,
                    message or f"{job_kind_label_for_message(job.kind)} canceled.",
                    reason,
                    job_id,
                ),
            )
        else:
            connection.execute(
                """
                UPDATE player_jobs
                SET updated_at = ?,
                    cancel_requested_at = COALESCE(cancel_requested_at, ?),
                    reason = ?
                WHERE job_id = ?
                """,
                (requested_at, requested_at, "Cancellation requested.", job_id),
            )
        connection.commit()
        return get_player_job_from_connection(connection, job_id)
    finally:
        connection.close()


def mark_stale_player_jobs_canceled(
    database: Path,
    *,
    reason: str = "Canceled because the player restarted.",
) -> tuple[PlayerJobRecord, ...]:
    canceled_at = utc_now_iso()
    connection = connect_database(database)
    try:
        rows = list(
            connection.execute(
                """
                SELECT job_id
                FROM player_jobs
                WHERE status IN ('queued', 'running')
                ORDER BY created_at, job_id
                """
            )
        )
        if not rows:
            return ()
        for row in rows:
            job = get_player_job_from_connection(connection, int(row["job_id"]))
            connection.execute(
                """
                UPDATE player_jobs
                SET status = 'canceled',
                    updated_at = ?,
                    finished_at = COALESCE(finished_at, ?),
                    cancel_requested_at = COALESCE(cancel_requested_at, ?),
                    message = ?,
                    reason = ?
                WHERE job_id = ?
                """,
                (
                    canceled_at,
                    canceled_at,
                    canceled_at,
                    f"{job_kind_label_for_message(job.kind)} canceled.",
                    reason,
                    job.job_id,
                ),
            )
        connection.commit()
        return tuple(
            get_player_job_from_connection(connection, int(row["job_id"]))
            for row in rows
        )
    finally:
        connection.close()


def list_player_jobs(database: Path) -> tuple[PlayerJobRecord, ...]:
    connection = connect_database(database, create=False)
    try:
        rows = list(
            connection.execute(
                """
                SELECT
                    job_id,
                    created_at,
                    updated_at,
                    started_at,
                    finished_at,
                    cancel_requested_at,
                    kind,
                    status,
                    message,
                    reason,
                    context_json
                FROM player_jobs
                ORDER BY created_at DESC, job_id DESC
                """
            )
        )
    finally:
        connection.close()

    return tuple(row_to_player_job(row) for row in rows)


def list_active_player_jobs(database: Path) -> tuple[PlayerJobRecord, ...]:
    connection = connect_database(database, create=False)
    try:
        rows = list(
            connection.execute(
                """
                SELECT
                    job_id,
                    created_at,
                    updated_at,
                    started_at,
                    finished_at,
                    cancel_requested_at,
                    kind,
                    status,
                    message,
                    reason,
                    context_json
                FROM player_jobs
                WHERE status IN ('queued', 'running')
                ORDER BY created_at, job_id
                """
            )
        )
    finally:
        connection.close()

    return tuple(row_to_player_job(row) for row in rows)


def job_kind_label_for_message(kind: str) -> str:
    labels = {
        "add_root": "Add and scan",
        "delete_root": "Delete",
        "edit_album": "Tag edit",
        "edit_album_musicbrainz": "MusicBrainz ID edit",
        "update_playlist_file": "Update playlist file",
        "rescan_library": "Rescan",
        "sync": "Sync",
    }
    return labels.get(kind, " ".join(part.capitalize() for part in kind.split("_") if part) or "Job")
