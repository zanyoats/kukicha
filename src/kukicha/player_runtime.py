from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from queue import Queue
from threading import Lock


@dataclass(slots=True)
class PlayerQueueState:
    track_ids: list[int]
    position: int = 0
    loaded_track_id: int | None = None
    paused: bool = True


@dataclass(frozen=True, slots=True)
class PlayerActionRecord:
    action_id: int
    created_at: str
    kind: str
    status: str
    message: str
    context: dict[str, object]


class PlayerRuntime:
    """Framework-neutral player state and behavior shared by HTTP adapters."""

    def __init__(
        self,
        options_or_database: object,
    ) -> None:
        database = getattr(options_or_database, "database", None)
        if database is not None:
            self.options = options_or_database
            self.database = Path(database)
        else:
            self.options = None
            self.database = Path(options_or_database)
        self.queue_state = PlayerQueueState(track_ids=[])
        self.queue_lock = Lock()
        self.missing_artwork_keys: set[tuple[int, int]] = set()
        self.notification_lock = Lock()
        self.notification_subscribers: set[Queue[dict[str, object]]] = set()
        self.playlist_file_lock = Lock()
        self.job_lock = Lock()
        self.active_library_job: str | None = None

    def publish_notification(self, notification: PlayerActionRecord) -> None:
        from .player_actions import action_payload

        payload = action_payload(notification)
        with self.notification_lock:
            subscribers = tuple(self.notification_subscribers)
        for subscriber in subscribers:
            subscriber.put_nowait(payload)

    def subscribe_notifications(self, subscriber: Queue[dict[str, object]]) -> None:
        with self.notification_lock:
            self.notification_subscribers.add(subscriber)

    def unsubscribe_notifications(self, subscriber: Queue[dict[str, object]]) -> None:
        with self.notification_lock:
            self.notification_subscribers.discard(subscriber)

    def begin_library_job(self, job_kind: str) -> bool:
        with self.job_lock:
            if self.active_library_job is not None:
                return False
            self.active_library_job = job_kind
            return True

    def finish_library_job(self) -> None:
        with self.job_lock:
            self.active_library_job = None

    def queue_state_copy(self) -> PlayerQueueState:
        with self.queue_lock:
            return PlayerQueueState(
                track_ids=list(self.queue_state.track_ids),
                position=self.queue_state.position,
                loaded_track_id=self.queue_state.loaded_track_id,
                paused=self.queue_state.paused,
            )

    def reset_queue_state(self) -> None:
        from .player_presenters import reset_queue_state

        with self.queue_lock:
            reset_queue_state(self.queue_state)

    def notification_payloads(self) -> list[dict[str, object]]:
        from .player_actions import action_payload
        from .use_case import list_player_actions

        return [action_payload(action) for action in list_player_actions(self.database)]
