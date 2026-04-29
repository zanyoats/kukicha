from __future__ import annotations

from pathlib import Path
import signal
import subprocess
import sys
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

def root_picker_supported() -> bool:
    return sys.platform == "darwin"

def choose_directory_path() -> str | None:
    if not root_picker_supported():
        raise NotImplementedError("folder picker is currently only supported on macOS")

    result = subprocess.run(
        [
            "osascript",
            "-e",
            'set selectedFolder to choose folder with prompt "Select a library root"',
            "-e",
            "POSIX path of selectedFolder",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        selected = result.stdout.strip()
        if not selected:
            return None
        return str(Path(selected).resolve(strict=False))

    error_text = f"{result.stderr}\n{result.stdout}".casefold()
    if "-128" in error_text or "user canceled" in error_text or "cancelled" in error_text:
        return None
    raise OSError(result.stderr.strip() or result.stdout.strip() or "failed to open folder picker")
