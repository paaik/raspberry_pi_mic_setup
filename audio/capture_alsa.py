import re
import subprocess
import threading
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

from audio.aln_gpio import aln_cleanup, aln_set, try_init_aln_output
from audio.tdm_align import run_fpga_aln_alignment


@dataclass(frozen=True)
class AlsaDevice:
    card: int
    device: int

    @property
    def hw_string(self) -> str:
        return f"hw:{self.card},{self.device}"


def _list_arecord_devices() -> list[AlsaDevice]:
    """
    Parse `arecord -l` output into a list of (card,device) pairs.
    """
    try:
        proc = subprocess.run(
            ["arecord", "-l"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return []

    text = (proc.stdout or "") + "\n" + (proc.stderr or "")
    # Examples we expect (format varies):
    #   card 1: ALSA [some name], device 0: ...
    #   card 2: Headset [..], device 0: ...
    card_re = re.compile(r"card\s+(?P<card>\d+):.*?\n\s*device\s+(?P<dev>\d+):", re.S)
    devices: list[AlsaDevice] = []

    # More robust approach: scan line-by-line for "card X:" and "device Y:" blocks.
    current_card: Optional[int] = None
    for line in text.splitlines():
        m_card = re.search(r"\bcard\s+(\d+)\b", line)
        if m_card:
            current_card = int(m_card.group(1))
            continue
        if current_card is not None:
            m_dev = re.search(r"\bdevice\s+(\d+)\b", line)
            if m_dev:
                devices.append(AlsaDevice(card=current_card, device=int(m_dev.group(1))))
                current_card = None

    return devices


FPGA_ALSA_CHANNELS = 8


class AlsaI2SMicCapture:
    """
    Capture 8-channel interleaved S32_LE PCM from ALSA (`arecord`) — FPGA TDM / eight mics.
    """

    def __init__(
        self,
        device: Optional[AlsaDevice] = None,
        sample_rate: int = 48000,
        format_str: str = "S32_LE",
        block_frames: int = 2048,
        aln_bcm: Optional[int] = None,
    ) -> None:
        self.device = device
        self.sample_rate = sample_rate
        self.channels = FPGA_ALSA_CHANNELS
        self.format_str = format_str
        self.block_frames = block_frames
        self.aln_bcm = aln_bcm

        self._proc: Optional[subprocess.Popen] = None
        self._stop_event = threading.Event()
        self._pending_raw = bytearray()
        self._aln_gpio_ready = False

        # 4 bytes per S32_LE sample
        self._bytes_per_block = self.block_frames * self.channels * 4

    def start(self) -> None:
        if self.device is None:
            devices = _list_arecord_devices()
            # Pick the first available device; user can override using CLI.
            self.device = devices[0] if devices else None

        if self.device is None:
            raise RuntimeError(
                "No ALSA capture device found. Run `arecord -l` on the Pi and pass --alsa-card/--alsa-device."
            )

        cmd = [
            "arecord",
            "-D",
            self.device.hw_string,
            "-c",
            str(self.channels),
            "-r",
            str(self.sample_rate),
            "-f",
            self.format_str,
            "-t",
            "raw",
            "-q",
            "-",
        ]

        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._stop_event.clear()
        self._pending_raw.clear()

        if self.aln_bcm is not None:
            self._aln_gpio_ready = try_init_aln_output(self.aln_bcm)

    def run_aln_alignment_now(self, on_status: Optional[Callable[[str], None]] = None) -> None:
        """
        Run PI_ALN + PCM discard/sync sequence (call only with arecord running on this object).
        """
        if self.aln_bcm is None:
            raise RuntimeError("ALN requires --aln-gpio (BCM)")
        if self._proc is None or self._proc.stdout is None:
            raise RuntimeError("Capture process is not running")
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
        if self._proc is not None:
            self._proc.terminate()
            self._proc = None
        self._pending_raw.clear()
        if self._aln_gpio_ready:
            aln_cleanup()
            self._aln_gpio_ready = False

    def _take_bytes_exact(self, n: int) -> bytes:
        """Read exactly `n` bytes from pending buffer + arecord stdout."""
        while len(self._pending_raw) < n:
            if self._proc is None or self._proc.stdout is None:
                return bytes(self._pending_raw[:n]) if self._pending_raw else b""
            need = n - len(self._pending_raw)
            chunk = self._proc.stdout.read(max(need, 4096))
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
        """Returns int32 shape (frames, 8) interleaved."""
        if self._proc is None or self._proc.stdout is None:
            return np.zeros((self.block_frames, self.channels), dtype=np.int32)

        if self._stop_event.is_set():
            return np.zeros((self.block_frames, self.channels), dtype=np.int32)

        raw = self._take_bytes_exact(self._bytes_per_block)
        if raw is None or len(raw) != self._bytes_per_block:
            return np.zeros((self.block_frames, self.channels), dtype=np.int32)

        data = np.frombuffer(raw, dtype="<i4")
        need = self.block_frames * self.channels
        if data.shape[0] < need:
            return np.zeros((self.block_frames, self.channels), dtype=np.int32)
        data = data[:need]

        return data.reshape(self.block_frames, self.channels).astype(np.int32, copy=False)

