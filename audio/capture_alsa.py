import re
import subprocess
import threading
from dataclasses import dataclass
from typing import Optional

import numpy as np


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


class AlsaI2SMicCapture:
    """
    Capture mono I2S PCM from ALSA using `arecord` subprocess.

    For DMM-4026-B-I2S-R, we typically use:
      - 48 kHz
      - 32-bit word containers -> S32_LE
      - 1 channel (mono)
    """

    def __init__(
        self,
        device: Optional[AlsaDevice] = None,
        sample_rate: int = 48000,
        channels: int = 1,
        format_str: str = "S32_LE",
        block_frames: int = 2048,
    ) -> None:
        self.device = device
        self.sample_rate = sample_rate
        self.channels = channels
        self.format_str = format_str
        self.block_frames = block_frames

        self._proc: Optional[subprocess.Popen] = None
        self._stop_event = threading.Event()

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

    def stop(self) -> None:
        self._stop_event.set()
        if self._proc is not None:
            self._proc.terminate()
            self._proc = None

    def read_block(self) -> np.ndarray:
        """
        Returns int32 samples with shape (frames,).
        """
        if self._proc is None or self._proc.stdout is None:
            # If not started, return silence.
            return np.zeros((self.block_frames,), dtype=np.int32)

        if self._stop_event.is_set():
            return np.zeros((self.block_frames,), dtype=np.int32)

        raw = self._proc.stdout.read(self._bytes_per_block)
        if raw is None or len(raw) != self._bytes_per_block:
            # On underrun/termination, avoid crashing.
            return np.zeros((self.block_frames,), dtype=np.int32)

        # int32 little-endian
        data = np.frombuffer(raw, dtype="<i4")
        if data.shape[0] != self.block_frames * self.channels:
            data = data[: self.block_frames * self.channels]

        if self.channels > 1:
            data = data.reshape(-1, self.channels)[:, 0]

        return data.astype(np.int32, copy=False)

