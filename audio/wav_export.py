"""Write 32-bit little-endian stereo PCM WAV files (stdlib `wave`)."""

from __future__ import annotations

import wave

import numpy as np


def write_s32le_stereo_wav(path: str, interleaved_lr: np.ndarray, sample_rate: int) -> None:
    """
    `interleaved_lr`: 1-D int32 array [L0,R0,L1,R1,...], length must be even.
    """
    x = np.asarray(interleaved_lr, dtype=np.int32).reshape(-1)
    if x.size % 2 != 0:
        raise ValueError("Stereo interleaved length must be even")
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(4)
        w.setframerate(int(sample_rate))
        w.writeframes(x.astype("<i4", copy=False).tobytes())
