"""
Capture pi_sd using pigpio's BSC SPI slave (external clock from FPGA).

Requires pigpiod. This path does not use SPI0 (display) or SPI1 /dev/spidev.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from . import config as cfg

try:
    import pigpio
except ImportError:
    pigpio = None  # type: ignore


class PigpioBscSpiCapture:
    def __init__(
        self,
        on_bytes: Callable[[bytes], None],
        bsc_ctrl: int | None = None,
        pi_aln_pin: int | None = None,
    ) -> None:
        if pigpio is None:
            raise RuntimeError("pigpio is not installed (pip install pigpio)")
        self._on_bytes = on_bytes
        self._bsc_ctrl = bsc_ctrl if bsc_ctrl is not None else cfg.BSC_SPI_CTRL_RX
        self._pi_aln_pin = pi_aln_pin if pi_aln_pin is not None else cfg.PIN_PI_ALN
        self._pi: Optional["pigpio.pi"] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._pi = pigpio.pi()
        if not self._pi.connected:
            raise RuntimeError("pigpio daemon not reachable (sudo pigpiod)")

        self._pi.set_mode(self._pi_aln_pin, pigpio.OUTPUT)
        self._pi.write(self._pi_aln_pin, 1)  # PI_ALN=1 idle before alignment completes

        # Enable BSC SPI slave; empty TX, RX only in practice.
        self._pi.bsc_xfer(self._bsc_ctrl, b"")
        self._stop.clear()

        def poll_loop() -> None:
            assert self._pi is not None
            while not self._stop.is_set():
                _status, count, data = self._pi.bsc_xfer(self._bsc_ctrl, b"")
                if count and count > 0 and data:
                    self._on_bytes(bytes(data))
                time.sleep(0.0005)

        self._thread = threading.Thread(target=poll_loop, daemon=True)
        self._thread.start()

    def set_pi_aln(self, level: int) -> None:
        if self._pi is None:
            return
        self._pi.write(self._pi_aln_pin, 1 if level else 0)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        if self._pi is not None:
            self._pi.bsc_xfer(0, b"")
            self._pi.stop()
            self._pi = None
