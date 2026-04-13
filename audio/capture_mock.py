"""Replay a raw capture file for UI / decoder testing (no hardware)."""

from __future__ import annotations

import threading
import time
from typing import Callable

from . import config as cfg


class MockFileCapture:
    def __init__(self, path: str, on_bytes: Callable[[bytes], None], rate_hz: float = 3750.0) -> None:
        self._path = path
        self._on_bytes = on_bytes
        self._rate_hz = rate_hz
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._stop.clear()

        def run() -> None:
            with open(self._path, "rb") as f:
                data = f.read()
            if not data:
                return
            if cfg.MARKER not in data:
                data = cfg.ZERO_FRAME * 8 + cfg.MARKER + data
            # Approximate real-time assuming back-to-back superframes (32 B each)
            frame_period = 1.0 / max(self._rate_hz, 1.0)
            chunk = cfg.FRAME_BYTES * 4
            offset = 0
            while not self._stop.is_set():
                if offset >= len(data):
                    offset = 0
                end = min(offset + chunk, len(data))
                self._on_bytes(data[offset:end])
                offset = end
                time.sleep(frame_period)

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()

    def set_pi_aln(self, _level: int) -> None:
        """No-op: mock stream has no real pi_aln line."""

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
