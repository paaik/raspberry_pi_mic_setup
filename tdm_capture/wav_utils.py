"""Minimal multichannel 32-bit PCM WAV writer (no SciPy)."""

from __future__ import annotations

import struct
from typing import BinaryIO


def write_wav_s32_le_interleaved(
    f: BinaryIO,
    samples_interleaved: memoryview,
    *,
    n_channels: int,
    sample_rate: int,
) -> None:
    """
    Write a standard PCM WAV with S32_LE samples.
    `samples_interleaved` is length n_frames * n_channels int32 little-endian bytes.
    """
    if n_channels < 1 or sample_rate < 1:
        raise ValueError("n_channels and sample_rate must be positive")
    nbytes = len(samples_interleaved)
    if nbytes % (4 * n_channels) != 0:
        raise ValueError("sample byte length must be a multiple of 4 * n_channels")
    n_frames = nbytes // (4 * n_channels)

    block_align = n_channels * 4
    byte_rate = sample_rate * block_align
    bits_per_sample = 32
    data_size = n_frames * block_align    # fmt chunk for WAVE_FORMAT_PCM (1), S32_LE
    fmt_chunk = struct.pack(
        "<HHIIHH",
        1,  # audio format PCM
        n_channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
    )

    riff_size = 4 + (8 + len(fmt_chunk)) + (8 + data_size)
    f.write(b"RIFF")
    f.write(struct.pack("<I", riff_size))
    f.write(b"WAVE")
    f.write(b"fmt ")
    f.write(struct.pack("<I", len(fmt_chunk)))
    f.write(fmt_chunk)
    f.write(b"data")
    f.write(struct.pack("<I", data_size))
    f.write(samples_interleaved)
