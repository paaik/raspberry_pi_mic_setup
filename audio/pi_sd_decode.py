"""
Decode FPGA 256-bit serial frames into eight int32 mic samples (no I²S / ALSA framing).

The stream is treated as a contiguous sequence of **32-byte frames** (256 bits each).
Default layout matches eight **little-endian int32** values in channel order:

  ch0..7  →  mic pair order 1L, 1R, 2L, 2R, 3L, 3R, 4L, 4R (same as the rest of this project).

If your Verilog serializer uses different bit/byte order, add a conversion step or adjust
`decode_frames` (e.g. per-word byte swap).
"""

from __future__ import annotations

import numpy as np

# 8 channels × 32 bits = 256 bits per frame
FRAME_BYTES = 32


def decode_frames(raw: bytes) -> np.ndarray:
    """
    Decode raw bytes into shape (n_frames, 8) int32.

    Raises ValueError if len(raw) is not a multiple of FRAME_BYTES.
    """
    if len(raw) % FRAME_BYTES != 0:
        raise ValueError(f"raw length {len(raw)} is not a multiple of {FRAME_BYTES} bytes per frame")
    if len(raw) == 0:
        return np.zeros((0, 8), dtype=np.int32)
    n = len(raw) // FRAME_BYTES
    return np.frombuffer(raw, dtype="<i4").reshape(n, 8)


def decode_single_frame(raw32: bytes) -> np.ndarray:
    """Exactly one 256-bit frame → shape (8,) int32."""
    if len(raw32) != FRAME_BYTES:
        raise ValueError(f"expected {FRAME_BYTES} bytes, got {len(raw32)}")
    return np.frombuffer(raw32, dtype="<i4").copy()
