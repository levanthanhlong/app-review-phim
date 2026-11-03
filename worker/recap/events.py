"""JSON Lines protocol (worker -> app). One JSON object per stdout line.

stdout carries only protocol events. Raw/human logs go to stderr and, while a
stage is running, also to logs/<stage>.log.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import IO, Any


def take_protocol_stdout() -> IO[str]:
    """Reserve the real stdout for protocol events and point fd 1 at stderr.

    Libraries (e.g. mlx_whisper prints "Detected language: ...") and child processes
    then write to stderr instead of corrupting the JSON Lines stream. Call once at startup.
    """
    sys.stdout.flush()
    protocol = os.fdopen(os.dup(1), "w", buffering=1, encoding="utf-8")
    os.dup2(2, 1)
    return protocol


class Emitter:
    def __init__(self, stream: IO[str] | None = None, raw_stream: IO[str] | None = None) -> None:
        self._stream = stream or sys.stdout
        self._raw = raw_stream or sys.stderr
        self._log_file: IO[str] | None = None

    # ---- protocol events -------------------------------------------------
    def emit(self, event: dict[str, Any]) -> None:
        self._stream.write(json.dumps(event, ensure_ascii=False) + "\n")
        self._stream.flush()

    def stage_start(self, stage: str, total: float | None = None) -> None:
        self.emit({"type": "stage_start", "stage": stage, "total": total})

    def progress(self, stage: str, done: float, total: float, message: str = "") -> None:
        self.emit({"type": "progress", "stage": stage, "done": done, "total": total, "message": message})

    def artifact(self, stage: str, path: str) -> None:
        self.emit({"type": "artifact", "stage": stage, "path": path})

    def stage_end(self, stage: str, status: str, elapsed_sec: float, skipped: bool = False) -> None:
        self.emit(
            {"type": "stage_end", "stage": stage, "status": status, "elapsed_sec": elapsed_sec, "skipped": skipped}
        )

    def error(self, stage: str | None, code: str, message: str, retryable: bool) -> None:
        self.raw(f"ERROR [{code}] {message}")
        self.emit({"type": "error", "stage": stage, "code": code, "message": message, "retryable": retryable})

    def result(self, command: str, data: dict[str, Any]) -> None:
        self.emit({"type": "result", "command": command, "data": data})

    def log(self, level: str, message: str) -> None:
        self.raw(f"{level.upper()}: {message}")
        self.emit({"type": "log", "level": level, "message": message})

    # ---- raw logs --------------------------------------------------------
    def raw(self, line: str) -> None:
        """Write a raw log line to stderr and the active stage log file (not stdout)."""
        text = line.rstrip("\n")
        self._raw.write(text + "\n")
        self._raw.flush()
        if self._log_file is not None:
            self._log_file.write(f"{datetime.now().isoformat(timespec='seconds')} {text}\n")
            self._log_file.flush()

    @contextmanager
    def stage_log(self, path: Path) -> Iterator[IO[str]]:
        """Append raw logs to `path` for the duration of the block. Yields the file handle."""
        path.parent.mkdir(parents=True, exist_ok=True)
        previous = self._log_file
        with path.open("a", encoding="utf-8") as fh:
            self._log_file = fh
            try:
                yield fh
            finally:
                self._log_file = previous
