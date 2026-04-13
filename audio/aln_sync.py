"""Blocking PI_ALN alignment helper (Pi drives GPIO; decoder follows phases)."""

from __future__ import annotations

import time
from typing import Protocol

from . import config as cfg
from .pi_sd_decode import PiSdDecoder


class _AlnCapture(Protocol):
    def set_pi_aln(self, level: int) -> None: ...


def run_pi_aln_alignment(capture: _AlnCapture, decoder: PiSdDecoder) -> None:
    """
    1) PI_ALN=1 — decoder discards until consecutive zero superframes.
    2) PI_ALN=0 — decoder waits for one MARKER frame, then streams TDM.
    Leaves PI_ALN at 0 for continuous capture (serializer ST_RUN).
    """
    capture.set_pi_aln(1)
    decoder.alignment_begin()

    t0 = time.monotonic()
    while not decoder.flush_satisfied:
        if time.monotonic() - t0 > cfg.ALIGN_FLUSH_TIMEOUT_S:
            break
        time.sleep(0.001)

    capture.set_pi_aln(0)
    decoder.alignment_aln_went_low()

    t1 = time.monotonic()
    while not decoder.marker_consumed:
        if time.monotonic() - t1 > cfg.ALIGN_WAIT_MARKER_TIMEOUT_S:
            break
        time.sleep(0.001)
