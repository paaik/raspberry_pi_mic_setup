"""256-bit superframe alignment and unpack (8 x signed 32-bit BE words)."""

from __future__ import annotations

import struct
import threading
from enum import Enum, auto
from typing import Iterable, List, Tuple

from . import config as cfg

# Human labels matching FPGA mic_sd lanes and I2S L/R slots
CHANNEL_LABELS: Tuple[str, ...] = (
    "Lane1 mic_sd1 · Left",
    "Lane1 mic_sd1 · Right",
    "Lane2 mic_sd2 · Left",
    "Lane2 mic_sd2 · Right",
    "Lane3 mic_sd3 · Left",
    "Lane3 mic_sd3 · Right",
    "Lane4 mic_sd4 · Left",
    "Lane4 mic_sd4 · Right",
)


class _Phase(Enum):
    """PI_ALN-driven alignment (see audio/config.py)."""

    FLUSH_IDLE_ZEROS = auto()  # PI_ALN=1: discard until consecutive zero superframes
    WAIT_MARKER = auto()  # PI_ALN=0: drop bytes until one MARKER, then stream
    STREAM = auto()  # TDM superframes; PI_ALN must stay 0 per Verilog


def unpack_superframe(frame32: bytes) -> Tuple[int, ...]:
    if len(frame32) != cfg.FRAME_BYTES:
        raise ValueError(f"expected {cfg.FRAME_BYTES} bytes, got {len(frame32)}")
    return struct.unpack(">8i", frame32)


class PiSdDecoder:
    """
    Push raw bytes from pi_sd.

    Alignment protocol:
      1. Host sets PI_ALN=1, calls alignment_begin(). Incoming data is discarded
         until ALIGN_MIN_CONSECUTIVE_ZERO_FRAMES full zero superframes are seen.
      2. Host sets PI_ALN=0, calls alignment_aln_went_low(). No TDM is emitted
         until exactly one MARKER superframe (32x0xFF) is found and consumed.
      3. Subsequent32-byte words are unpacked as signed big-endian channels until
         alignment_begin() / resync() runs again.
    """

    def __init__(self, max_buffer: int = 1 << 20) -> None:
        self._lock = threading.Lock()
        self._buf: bytearray = bytearray()
        self._max_buffer = max_buffer
        self._phase = _Phase.FLUSH_IDLE_ZEROS
        self._consecutive_zero_frames = 0
        self._flush_satisfied = False
        self._marker_consumed = False

    def alignment_begin(self) -> None:
        """Call with PI_ALN=1 (or just before raising ALN). Clears state for flush."""
        with self._lock:
            self._buf.clear()
            self._phase = _Phase.FLUSH_IDLE_ZEROS
            self._consecutive_zero_frames = 0
            self._flush_satisfied = False
            self._marker_consumed = False

    def alignment_aln_went_low(self) -> None:
        """Call after host sets PI_ALN=0; wait for MARKER before emitting TDM."""
        with self._lock:
            self._phase = _Phase.WAIT_MARKER
            self._marker_consumed = False

    @property
    def flush_satisfied(self) -> bool:
        with self._lock:
            return self._flush_satisfied

    @property
    def marker_consumed(self) -> bool:
        with self._lock:
            return self._marker_consumed

    @property
    def is_streaming(self) -> bool:
        with self._lock:
            return self._phase == _Phase.STREAM

    def resync(self) -> None:
        """Abort decode; next step should be alignment_begin() + PI_ALN sequence."""
        self.alignment_begin()

    def push(self, data: bytes) -> List[Tuple[int, ...]]:
        if not data:
            return []
        with self._lock:
            self._buf.extend(data)
            if len(self._buf) > self._max_buffer:
                del self._buf[: len(self._buf) - self._max_buffer]

            if self._phase == _Phase.FLUSH_IDLE_ZEROS:
                self._consume_flush_phase()
                return []

            if self._phase == _Phase.WAIT_MARKER:
                self._consume_wait_marker()
                if self._phase != _Phase.STREAM:
                    return []

            out: List[Tuple[int, ...]] = []
            self._drain_stream_frames(out)
            return out

    def _consume_flush_phase(self) -> None:
        """Strip input until we see enough consecutive zero superframes."""
        b = self._buf
        need = cfg.ALIGN_MIN_CONSECUTIVE_ZERO_FRAMES

        while len(b) >= cfg.FRAME_BYTES:
            frame = bytes(b[: cfg.FRAME_BYTES])
            if frame == cfg.ZERO_FRAME:
                del b[: cfg.FRAME_BYTES]
                self._consecutive_zero_frames += 1
                if self._consecutive_zero_frames >= need:
                    self._flush_satisfied = True
                continue

            self._consecutive_zero_frames = 0
            idx = b.find(cfg.ZERO_FRAME)
            if idx > 0:
                del b[:idx]
                continue
            if idx < 0:
                del b[:1]
                continue

    def _consume_wait_marker(self) -> None:
        b = self._buf
        idx = b.find(cfg.MARKER)
        if idx < 0:
            if len(b) > len(cfg.MARKER):
                del b[: len(b) - len(cfg.MARKER) + 1]
            return
        del b[: idx + cfg.FRAME_BYTES]
        self._phase = _Phase.STREAM
        self._marker_consumed = True

    def _drain_stream_frames(self, out: List[Tuple[int, ...]]) -> None:
        b = self._buf
        i = 0
        n = len(b)

        while i + cfg.FRAME_BYTES <= n:
            frame = bytes(b[i : i + cfg.FRAME_BYTES])
            if frame == cfg.MARKER:
                i += cfg.FRAME_BYTES
                continue
            out.append(unpack_superframe(frame))
            i += cfg.FRAME_BYTES

        if i:
            del b[:i]


def iter_file_chunks(path: str, chunk: int = 8192) -> Iterable[bytes]:
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            yield block
