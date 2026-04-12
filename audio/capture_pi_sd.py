"""
Raw 256-bit frame capture for FPGA `pi_sd` — not kernel I²S/TDM / ALSA.

Read a binary stream (FIFO, pipe, file, or ``-`` for stdin). Each **32-byte** chunk is one
frame: eight int32 samples (default **big-endian** words to match Lattice ``pi_tdm_serializer``
MSB-first output; see ``docs/fpga_pi_sd_framing.md``). Feeds the same ``read_block()`` shape as
:class:`AlsaI2SMicCapture` so the DSP pipeline is unchanged.

Wire-up on the Pi is project-specific (SPI slave, GPIO+buffer, etc.): this module only
consumes the **already framed** byte stream. Point ``--pi-sd-source`` at the endpoint
your bridge writes to (e.g. ``mkfifo /run/fpga_pi_sd``).
"""

from __future__ import annotations

import sys
import threading
from typing import Callable, Optional

import numpy as np

from audio.aln_gpio import aln_cleanup, aln_set, try_init_aln_output
from audio.pi_sd_decode import FRAME_BYTES, decode_frames
from audio.tdm_align import run_fpga_aln_alignment


class PiSdRawCapture:
    """
    Eight-channel int32 stream from raw 32-byte FPGA frames (``pi_sd`` databus).

    ``sample_rate`` is nominal (used for WAV export / UI); set it to your effective
    frame rate if you know it (depends on FPGA ``pi_sck`` and serializer).
    """

    def __init__(
        self,
        source_path: str,
        *,
        sample_rate: int = 48000,
        block_frames: int = 2048,
        aln_bcm: Optional[int] = None,
        frame_word_endian: str = "big",
    ) -> None:
        self.source_path = source_path.strip()
        self.sample_rate = sample_rate
        self.channels = 8
        self.block_frames = block_frames
        self.aln_bcm = aln_bcm
        self.frame_word_endian = frame_word_endian

        self.backend = "pi_sd"
        self.device = None

        self._fp = None
        self._stop_event = threading.Event()
        self._pending_raw = bytearray()
        self._aln_gpio_ready = False

        self._bytes_per_block = self.block_frames * FRAME_BYTES

    @property
    def source_label(self) -> str:
        return f"pi_sd:{self.source_path}"

    def start(self) -> None:
        if not self.source_path:
            raise RuntimeError("pi_sd source path is empty (use --pi-sd-source)")

        if self.source_path == "-":
            self._fp = sys.stdin.buffer
        else:
            self._fp = open(self.source_path, "rb")

        self._stop_event.clear()
        self._pending_raw.clear()

        if self.aln_bcm is not None:
            self._aln_gpio_ready = try_init_aln_output(self.aln_bcm)

    def run_aln_alignment_now(self, on_status: Optional[Callable[[str], None]] = None) -> None:
        """
        Same PI_ALN + byte-stream handshake as ALSA: FPGA must emit the same
        zero / sync patterns as 32-byte frames (see README / ``tdm_align``).
        """
        if self.aln_bcm is None:
            raise RuntimeError("ALN requires --aln-gpio (BCM)")
        if self._fp is None:
            raise RuntimeError("pi_sd capture is not running")
        if not self._aln_gpio_ready:
            self._aln_gpio_ready = try_init_aln_output(self.aln_bcm)
        if not self._aln_gpio_ready:
            raise RuntimeError(
                "ALN GPIO unavailable (install RPi.GPIO on the Pi: pip install RPi.GPIO)"
            )
        run_fpga_aln_alignment(
            take_bytes=self._take_bytes_exact,
            putback=self._putback_raw,
            channels=self.channels,
            block_bytes=self._bytes_per_block,
            aln_set=aln_set,
            on_status=on_status,
        )

    def stop(self) -> None:
        self._stop_event.set()
        if self._fp is not None and self.source_path != "-":
            try:
                self._fp.close()
            except OSError:
                pass
        self._fp = None
        self._pending_raw.clear()
        if self._aln_gpio_ready:
            aln_cleanup()
            self._aln_gpio_ready = False

    def _take_bytes_exact(self, n: int) -> bytes:
        while len(self._pending_raw) < n:
            if self._fp is None:
                return bytes(self._pending_raw[:n]) if self._pending_raw else b""
            need = n - len(self._pending_raw)
            chunk = self._fp.read(max(need, 4096))
            if not chunk:
                break
            self._pending_raw.extend(chunk)
        if len(self._pending_raw) < n:
            out = bytes(self._pending_raw)
            self._pending_raw.clear()
            return out
        out = bytes(self._pending_raw[:n])
        del self._pending_raw[:n]
        return out

    def _putback_raw(self, data: bytes) -> None:
        self._pending_raw[:0] = data

    def read_block(self) -> np.ndarray:
        """Shape (block_frames, 8) int32 — one 32-byte frame per audio frame."""
        if self._fp is None:
            return np.zeros((self.block_frames, self.channels), dtype=np.int32)

        if self._stop_event.is_set():
            return np.zeros((self.block_frames, self.channels), dtype=np.int32)

        raw = self._take_bytes_exact(self._bytes_per_block)
        if len(raw) != self._bytes_per_block:
            return np.zeros((self.block_frames, self.channels), dtype=np.int32)

        try:
            dec = decode_frames(raw, word_endian=self.frame_word_endian)
        except ValueError:
            return np.zeros((self.block_frames, self.channels), dtype=np.int32)

        if dec.shape[0] != self.block_frames:
            return np.zeros((self.block_frames, self.channels), dtype=np.int32)
        return dec.astype(np.int32, copy=False)
