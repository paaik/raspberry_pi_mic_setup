"""
FPGA TDM alignment handshaking via PI_ALN + PCM stream (see README).

Sequence (8-channel S32_LE):
  1. PI_ALN=1 → FPGA sends 0x00000000 on all 8 channels; discard PCM until a full block is all zeros.
  2. PI_ALN=0 → FPGA sends exactly one frame of 0xFFFFFFFF on all 8 channels, then live TDM.
  3. Discard PCM until that sync frame is seen; leave following bytes in the pending buffer.
"""

from __future__ import annotations

from typing import Callable, Optional

import numpy as np


def _report_status(cb: Optional[Callable[[str], None]], msg: str) -> None:
    if cb is None:
        return
    try:
        cb(msg)
    except Exception:
        pass


def _frame_bytes(channels: int) -> int:
    return channels * 4


def find_all_minus_one_frame(buf: bytes, channels: int) -> int:
    """
    Return index **after** the first full interleaved frame of int32(-1) for `channels`,
    or -1 if not found. Scans every byte offset (handles unknown byte alignment).
    """
    fb = _frame_bytes(channels)
    if len(buf) < fb:
        return -1
    limit = len(buf) - fb + 1
    for i in range(limit):
        chunk = buf[i : i + fb]
        arr = np.frombuffer(chunk, dtype="<i4")
        if arr.shape[0] == channels and bool(np.all(arr == -1)):
            return i + fb
    return -1


def run_fpga_aln_alignment(
    *,
    take_bytes: Callable[[int], bytes],
    putback: Callable[[bytes], None],
    channels: int,
    block_bytes: int,
    aln_set: callable,
    max_discard_bytes: int = 50_000_000,
    on_status: Optional[Callable[[str], None]] = None,
) -> None:
    """
    `take_bytes(n)` reads exactly n bytes from the PCM stream (pending + arecord stdout).
    `putback(data)` prepends bytes to the stream for subsequent `read_block` calls.
    """
    if channels < 2:
        return

    fb = _frame_bytes(channels)
    discarded = 0

    # 1) PI_ALN = 1 — discard until a full block is all zeros (flush stale data)
    _report_status(
        on_status,
        "PI_ALN = 1 (high): FPGA should send zeros — discarding PCM until one full block is all 0…",
    )
    aln_set(True)
    while discarded < max_discard_bytes:
        raw = take_bytes(block_bytes)
        if len(raw) < block_bytes:
            raise RuntimeError("ALN align: short read while waiting for zeros (ALN=1)")
        arr = np.frombuffer(raw, dtype="<i4")
        discarded += len(raw)
        if arr.size == block_bytes // 4 and np.all(arr == 0):
            break
    else:
        raise RuntimeError("ALN align: timed out waiting for an all-zero block")

    _report_status(on_status, "All-zero block OK — PI_ALN = 0 (low); FPGA sends one sync frame then live TDM…")
    # 2) PI_ALN = 0 — one frame of 0xFFFFFFFF then TDM
    aln_set(False)

    buf = bytearray()
    chunk_size = min(4096, max(block_bytes, fb * 4))
    while discarded < max_discard_bytes:
        raw = take_bytes(chunk_size)
        if len(raw) < chunk_size:
            raise RuntimeError("ALN align: short read while waiting for sync frame")
        buf.extend(raw)
        discarded += len(raw)
        b = bytes(buf)
        end = find_all_minus_one_frame(b, channels)
        if end >= 0:
            _report_status(on_status, "Sync frame found — alignment done; resuming normal capture.")
            putback(b[end:])
            return
        if len(buf) > 256 * 1024:
            del buf[:- (fb * 8)]

    raise RuntimeError("ALN align: sync frame (all 0xFFFFFFFF) not found")
