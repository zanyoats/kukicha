from __future__ import annotations

import signal
from typing import Any


def register_player_signal_handlers(
    stop_reason: dict[str, str],
) -> dict[int, Any]:
    previous_handlers: dict[int, Any] = {}

    def handle_signal(signum: int, _frame: object) -> None:
        signal_name = signal.strsignal(signum) or f"signal {signum}"
        stop_reason["value"] = f"received {signal_name}"
        raise KeyboardInterrupt

    for signum in (signal.SIGINT, signal.SIGTERM):
        previous_handlers[signum] = signal.getsignal(signum)
        signal.signal(signum, handle_signal)
    return previous_handlers


def restore_signal_handlers(previous_handlers: dict[int, Any]) -> None:
    for signum, handler in previous_handlers.items():
        signal.signal(signum, handler)
