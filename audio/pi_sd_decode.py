"""
Decode FPGA 256-bit serial frames into eight int32 mic samples (no I²S / ALSA framing).

See ``docs/fpga_pi_sd_framing.md`` for how this maps to the Lattice ``Past_Lattice 4 Avril``
RTL (``superframe_loader`` + ``pi_tdm_serializer``).

The stream is a contiguous sequence of **32-byte frames** (256 bits each). Each frame holds
eight **32-bit signed** channel samples in order **ch0…ch7** (1L, 1R, … 4R).

**Default word endian: big** (``>i4``). The FPGA shifts **MSB first**; ``frame_reg`` packs
``{lane0, lane1, lane2, lane3}`` with lane0 in the **MSBs** of the 256-bit word, so the first
byte on the wire is the **MSB of ch0** — i.e. **big-endian** representation of each int32.
Use ``word_endian="little"`` only for bring-up tests or if your bridge byteswaps.
"""

from __future__ import annotations

import numpy as np

# 8 channels × 32 bits = 256 bits per frame
FRAME_BYTES = 32


def _dtype_word(word_endian: str) -> str:
    e = word_endian.lower().strip()
    if e in ("big", "be"):
        return ">i4"
    if e in ("little", "le"):
        return "<i4"
    raise ValueError(f"word_endian must be 'big' or 'little', not {word_endian!r}")


def decode_frames(raw: bytes, *, word_endian: str = "big") -> np.ndarray:
    """
    Decode raw bytes into shape (n_frames, 8) int32.

    ``word_endian`` — per-channel 32-bit word byte order in the 32-byte frame:
    ``"big"`` (default, matches Past Lattice MSB-first serializer) or ``"little"``.

    Raises ValueError if len(raw) is not a multiple of FRAME_BYTES.
    """
    if len(raw) % FRAME_BYTES != 0:
        raise ValueError(f"raw length {len(raw)} is not a multiple of {FRAME_BYTES} bytes per frame")
    if len(raw) == 0:
        return np.zeros((0, 8), dtype=np.int32)
    n = len(raw) // FRAME_BYTES
    dtype = _dtype_word(word_endian)
    return np.frombuffer(raw, dtype=dtype).reshape(n, 8)


def decode_single_frame(raw32: bytes, *, word_endian: str = "big") -> np.ndarray:
    """Exactly one 256-bit frame → shape (8,) int32."""
    if len(raw32) != FRAME_BYTES:
        raise ValueError(f"expected {FRAME_BYTES} bytes, got {len(raw32)}")
    dtype = _dtype_word(word_endian)
    return np.frombuffer(raw32, dtype=dtype).copy()
