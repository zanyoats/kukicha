from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from queue import Queue
from threading import Event, Lock, Thread
from typing import TYPE_CHECKING
import logging

from .album_artists import DEFAULT_ALBUM_ARTIST_SPLIT_PATTERNS

LOGGER = logging.getLogger("kukicha.player")
LIBRARY_FILTER_OPTIONS_INVALIDATING_JOB_KINDS = frozenset(
    {
        "rescan_library",
        "sync",
    }
)

if TYPE_CHECKING:
    from .use_case import LibraryFilterOptions


@dataclass(slots=True)
class PlayerQueueState:
    track_ids: list[int]
    position: int = 0
    loaded_track_id: int | None = None
    paused: bool = True
    errored_track_ids: list[int] = field(default_factory=list)
    unavailable_track_ids: list[int] = field(default_factory=list)
    snapshots: list[dict[str, object]] = field(default_factory=list)


class PlayerJobCanceled(Exception):
    """Raised by cooperative jobs when cancellation is requested."""


class PlayerJobCancelToken:
    def __init__(self) -> None:
        self._event = Event()

    def request_cancel(self) -> None:
        self._event.set()

    @property
    def canceled(self) -> bool:
        return self._event.is_set()

    def raise_if_canceled(self) -> None:
        if self.canceled:
            raise PlayerJobCanceled("Canceled by user.")


@dataclass(frozen=True, slots=True)
class PlayerJobRecord:
    job_id: int
    created_at: str
    updated_at: str
    started_at: str | None
    finished_at: str | None
    cancel_requested_at: str | None
    kind: str
    status: str
    message: str
    reason: str
    context: dict[str, object]


@dataclass(frozen=True, slots=True)
class PlayerJobResult:
    message: str
    context: dict[str, object] | None = None


@dataclass(slots=True)
class QueuedPlayerJob:
    record: PlayerJobRecord
    running_message: str
    canceled_message: str
    failed_message: str
    runner: Callable[[PlayerJobCancelToken], PlayerJobResult]
    cancel_token: PlayerJobCancelToken


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
        self.job_event_lock = Lock()
        self.job_event_subscribers: set[Queue[dict[str, object]]] = set()
        self.job_lock = Lock()
        self.job_queue: deque[QueuedPlayerJob] = deque()
        self.job_worker_thread: Thread | None = None
        self.job_cancel_tokens: dict[int, PlayerJobCancelToken] = {}
        self.library_filter_options_lock = Lock()
        self._library_filter_options: LibraryFilterOptions | None = None

    def enqueue_job(
        self,
        *,
        kind: str,
        queued_message: str,
        running_message: str,
        canceled_message: str,
        failed_message: str,
        context: dict[str, object],
        runner: Callable[[PlayerJobCancelToken], PlayerJobResult],
    ) -> PlayerJobRecord:
        from .use_case import create_player_job

        record = create_player_job(
            self.database,
            kind=kind,
            message=queued_message,
            context=context,
        )
        token = PlayerJobCancelToken()
        queued = QueuedPlayerJob(
            record=record,
            running_message=running_message,
            canceled_message=canceled_message,
            failed_message=failed_message,
            runner=runner,
            cancel_token=token,
        )
        with self.job_lock:
            self.job_queue.append(queued)
            self.job_cancel_tokens[record.job_id] = token
            thread_to_start = self.ensure_job_worker_locked()
        self.publish_job(record)
        if thread_to_start is not None:
            thread_to_start.start()
        return record

    def ensure_job_worker_locked(self) -> Thread | None:
        if self.job_worker_thread is not None:
            return None
        self.job_worker_thread = Thread(target=self.run_job_worker, daemon=True)
        return self.job_worker_thread

    def run_job_worker(self) -> None:
        while True:
            with self.job_lock:
                if not self.job_queue:
                    self.job_worker_thread = None
                    return
                queued = self.job_queue.popleft()
            self.run_queued_job(queued)

    def run_queued_job(self, queued: QueuedPlayerJob) -> None:
        from .use_case import get_player_job, update_player_job
        from .use_case.commands.jobs import utc_now_iso

        job_id = queued.record.job_id
        try:
            current = get_player_job(self.database, job_id)
        except Exception:
            LOGGER.exception("failed to load queued job %s", job_id)
            self.discard_job_token(job_id)
            return

        if current.status == "canceled" or current.cancel_requested_at:
            self.discard_job_token(job_id)
            return

        try:
            running_job = update_player_job(
                self.database,
                job_id,
                status="running",
                message=queued.running_message,
                started_at=utc_now_iso(),
            )
            self.publish_job(running_job)
            result = queued.runner(queued.cancel_token)
            succeeded_job = update_player_job(
                self.database,
                job_id,
                status="succeeded",
                message=result.message,
                context=result.context if result.context is not None else running_job.context,
                finished_at=utc_now_iso(),
            )
            if queued.record.kind in LIBRARY_FILTER_OPTIONS_INVALIDATING_JOB_KINDS:
                self.invalidate_library_filter_options()
            self.publish_job(succeeded_job)
        except PlayerJobCanceled as error:
            canceled_job = update_player_job(
                self.database,
                job_id,
                status="canceled",
                message=queued.canceled_message,
                reason=str(error) or "Canceled by user.",
                finished_at=utc_now_iso(),
            )
            self.publish_job(canceled_job)
        except Exception as error:
            LOGGER.exception("job %s failed", job_id)
            failed_job = update_player_job(
                self.database,
                job_id,
                status="failed",
                message=queued.failed_message,
                reason=brief_error_reason(error),
                finished_at=utc_now_iso(),
            )
            self.publish_job(failed_job)
        finally:
            self.discard_job_token(job_id)

    def discard_job_token(self, job_id: int) -> None:
        with self.job_lock:
            self.job_cancel_tokens.pop(job_id, None)

    def publish_job(self, job: PlayerJobRecord) -> None:
        from .player_jobs import job_payload

        payload = job_payload(job)
        with self.job_event_lock:
            subscribers = tuple(self.job_event_subscribers)
        for subscriber in subscribers:
            subscriber.put_nowait(payload)

    def subscribe_jobs(self, subscriber: Queue[dict[str, object]]) -> None:
        with self.job_event_lock:
            self.job_event_subscribers.add(subscriber)

    def unsubscribe_jobs(self, subscriber: Queue[dict[str, object]]) -> None:
        with self.job_event_lock:
            self.job_event_subscribers.discard(subscriber)

    def cancel_job(self, job_id: int) -> PlayerJobRecord:
        from .use_case import request_cancel_player_job

        canceled_message = None
        with self.job_lock:
            token = self.job_cancel_tokens.get(job_id)
            if token is not None:
                token.request_cancel()
            for queued in self.job_queue:
                if queued.record.job_id == job_id:
                    canceled_message = queued.canceled_message
                    break
        job = request_cancel_player_job(
            self.database,
            job_id,
            message=canceled_message,
        )
        self.publish_job(job)
        return job

    @property
    def album_artist_split_patterns(self) -> tuple[str, ...]:
        return tuple(
            getattr(
                self.options,
                "album_artist_split_patterns",
                DEFAULT_ALBUM_ARTIST_SPLIT_PATTERNS,
            )
        )

    @property
    def prefer_musicbrainz_english_aliases(self) -> bool:
        return bool(getattr(self.options, "prefer_musicbrainz_english_aliases", True))

    def queue_state_copy(self) -> PlayerQueueState:
        from .use_case.commands.player import load_queue_state_database

        with self.queue_lock:
            self.queue_state = load_queue_state_database(self.database)
            return PlayerQueueState(
                track_ids=list(self.queue_state.track_ids),
                position=self.queue_state.position,
                loaded_track_id=self.queue_state.loaded_track_id,
                paused=self.queue_state.paused,
                errored_track_ids=list(self.queue_state.errored_track_ids),
                unavailable_track_ids=list(self.queue_state.unavailable_track_ids),
                snapshots=[dict(snapshot) for snapshot in self.queue_state.snapshots],
            )

    def library_filter_options(self) -> "LibraryFilterOptions":
        with self.library_filter_options_lock:
            if self._library_filter_options is None:
                from .use_case import LibraryQueries

                self._library_filter_options = LibraryQueries(
                    self.database
                ).filter_options()
            return self._library_filter_options

    def invalidate_library_filter_options(self) -> None:
        with self.library_filter_options_lock:
            self._library_filter_options = None

    def reset_queue_state(self) -> None:
        from .use_case.commands.player import clear_queue_database

        with self.queue_lock:
            self.queue_state = clear_queue_database(self.database)

    def job_payloads(self) -> list[dict[str, object]]:
        from .player_jobs import job_payload
        from .use_case import list_player_jobs

        return [job_payload(job) for job in list_player_jobs(self.database)]

    def active_job_payloads(self) -> list[dict[str, object]]:
        from .player_jobs import job_payload
        from .use_case import list_active_player_jobs

        return [job_payload(job) for job in list_active_player_jobs(self.database)]


def brief_error_reason(error: BaseException) -> str:
    reason = str(error).strip()
    if not reason:
        return error.__class__.__name__
    return reason.splitlines()[0][:240]
