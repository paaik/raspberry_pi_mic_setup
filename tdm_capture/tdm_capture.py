#!/usr/bin/env python3
"""
Capture 8-channel FPGA TDM microphone audio via ALSA.

Each hardware frame is eight S32_LE samples in this order (256 bits on the data line):

 1L, 1R, 2L, 2R, 3L, 3R, 4L, 4R

Prerequisites on the Pi:
  - Device tree: `dtoverlay=i2s1` when the Pi is I2S clock slave to the FPGA.
  - ALSA: define an 8-channel PCM (see `asoundrc.tdm8.example`), e.g. device name `tdm8`.
  - FPGA in TDM mode: `i2cset -y 1 0x20 0x0F` (or use `--fpga-mode` below).

Do not play back TDM / tagged test patterns to a normal DAC — record only.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from typing import Iterator

import numpy as np

try:
    import alsaaudio
except ImportError as e:
    print("Install pyalsaaudio: pip install pyalsaaudio", file=sys.stderr)
    raise SystemExit(1) from e

from wav_utils import write_wav_s32_le_interleaved

# Channel index → label (matches interleaved order from FPGA TDM).
CHANNEL_LABELS = ("1L", "1R", "2L", "2R", "3L", "3R", "4L", "4R")

N_CHANNELS = 8

# Preset values from project notes (I2C addr 0x20, bus 1).
FPGA_MODES = {
    "tdm": 0x0F,
    "pair1": 0x01,
    "tagged": 0x0F,  # same opcode as in notes; use raw --fpga-value if your bitstream differs
}


def fpga_write_control(i2c_bus: int, i2c_addr: int, value: int) -> None:
    cmd = ["i2cset", "-y", str(i2c_bus), f"0x{i2c_addr:02X}", f"0x{value:02X}"]
    subprocess.run(cmd, check=True)


def open_capture(
    device: str,
    sample_rate: int,
    *,
    period_frames: int,
) -> alsaaudio.PCM:
    pcm = alsaaudio.PCM(
        alsaaudio.PCM_CAPTURE,
        alsaaudio.PCM_NORMAL,
        channels=N_CHANNELS,
        rate=sample_rate,
        format=alsaaudio.PCM_FORMAT_S32_LE,
        periodsize=period_frames,
        device=device,
    )
    return pcm


def frames_from_bytes(chunk: bytes) -> np.ndarray:
    """Shape (n_frames, 8) int32."""
    if len(chunk) % (4 * N_CHANNELS):
        raise ValueError("chunk size not multiple of 32 bytes (8 × S32)")
    return np.frombuffer(chunk, dtype=np.int32).reshape(-1, N_CHANNELS)


def iter_captured_frames(
    pcm: alsaaudio.PCM,
) -> Iterator[tuple[bytes, int]]:
    while True:
        length, buf = pcm.read()
        if length < 0:
            raise OSError(f"ALSA overrun or read error (length={length})")
        if length == 0:
            continue
        yield buf, length


def stream_s32_frames(
    device: str,
    sample_rate: int,
    *,
    period_frames: int = 4096,
) -> Iterator[np.ndarray]:
    """
    Yield each captured ALSA period as a (n_frames, 8) int32 array.
    Order per frame: 1L, 1R, 2L, 2R, 3L, 3R, 4L, 4R.
    """
    pcm = open_capture(device, sample_rate, period_frames=period_frames)
    for buf, _ in iter_captured_frames(pcm):
        yield frames_from_bytes(buf)


def cmd_peek(args: argparse.Namespace) -> None:
    pcm = open_capture(args.device, args.rate, period_frames=args.period)
    total = 0
    stats = None
    for buf, _ in iter_captured_frames(pcm):
        fr = frames_from_bytes(buf)
        if stats is None:
            stats = {
                "min": fr.min(axis=0),
                "max": fr.max(axis=0),
                "sum": np.zeros(N_CHANNELS, dtype=np.int64),
                "sumsq": np.zeros(N_CHANNELS, dtype=np.int64),
            }
        stats["min"] = np.minimum(stats["min"], fr.min(axis=0))
        stats["max"] = np.maximum(stats["max"], fr.max(axis=0))
        stats["sum"] += fr.sum(axis=0, dtype=np.int64)
        stats["sumsq"] += (fr.astype(np.int64) ** 2).sum(axis=0)
        total += fr.shape[0]
        if total >= args.frames:
            break

    assert stats is not None
    mean = stats["sum"] / total
    # population variance of int32 samples
    var = stats["sumsq"] / total - mean**2
    rms = np.sqrt(np.maximum(var, 0.0))
    full_scale = 2**31
    dbfs = 20 * np.log10(np.maximum(rms, 1.0) / full_scale)

    print(f"Analyzed {total} frames @ {args.rate} Hz, device {args.device!r}")
    for i, lab in enumerate(CHANNEL_LABELS):
        print(
            f"  {lab}: min={int(stats['min'][i])} max={int(stats['max'][i])} "
            f"rms={rms[i]:.2f} (~{dbfs[i]:.1f} dBFS)"
        )

    if args.dump_hex > 0:
        pcm2 = open_capture(args.device, args.rate, period_frames=args.period)
        raw, _ = next(iter(iter_captured_frames(pcm2)))
        fr0 = frames_from_bytes(raw)[: args.dump_hex]
        for row_i, row in enumerate(fr0):
            hexs = " ".join(f"{int(x) & 0xFFFFFFFF:08x}" for x in row)
            print(f"  frame[{row_i}]: {hexs}")


def cmd_record(args: argparse.Namespace) -> None:
    pcm = open_capture(args.device, args.rate, period_frames=args.period)
    target_frames = int(args.duration * args.rate)
    collected: list[bytes] = []
    got = 0

    for buf, _ in iter_captured_frames(pcm):
        collected.append(buf)
        got += len(buf) // (4 * N_CHANNELS)
        if got >= target_frames:
            break

    blob = b"".join(collected)
    # trim to exact duration
    frame_bytes = 4 * N_CHANNELS
    n_frames = min(got, target_frames)
    blob = blob[: n_frames * frame_bytes]

    out_path = args.output
    with open(out_path, "wb") as f:
        write_wav_s32_le_interleaved(
            f,
            memoryview(blob),
            n_channels=N_CHANNELS,
            sample_rate=args.rate,
        )
    print(f"Wrote {n_frames} frames ({n_frames / args.rate:.3f} s) → {out_path}")


def cmd_raw_dump(args: argparse.Namespace) -> None:
    pcm = open_capture(args.device, args.rate, period_frames=args.period)
    target_bytes = args.bytes
    out = open(args.output, "wb")
    try:
        got = 0
        for buf, _ in iter_captured_frames(pcm):
            out.write(buf)
            got += len(buf)
            if got >= target_bytes:
                break
    finally:
        out.close()
    print(f"Wrote {got} bytes of raw S32_LE interleaved 8ch → {args.output}")


def build_parser() -> argparse.ArgumentParser:
    env_dev = os.environ.get("ALSA_TDM_DEVICE", "tdm8")
    p = argparse.ArgumentParser(description="ALSA 8-channel TDM mic capture (FPGA)")
    p.add_argument("--device", default=env_dev, help=f"ALSA PCM name (default {env_dev!r} or env ALSA_TDM_DEVICE)")
    p.add_argument("--rate", type=int, default=48000, help="Sample rate (Hz)")
    p.add_argument(
        "--period",
        type=int,
        default=4096,
        help="Frames per ALSA period (see buffer tuning in asoundrc example)",
    )

    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("peek", help="Print per-channel stats + optional hex rows (alignment / test pattern)")
    sp.add_argument("--frames", type=int, default=4800, help="Number of frames to analyze")
    sp.add_argument("--dump-hex", type=int, default=0, metavar="N", help="Print first N frames as hex per channel")
    sp.set_defaults(func=cmd_peek)

    sr = sub.add_parser("record", help="Save multichannel WAV (S32_LE)")
    sr.add_argument("-d", "--duration", type=float, default=5.0, help="Seconds")
    sr.add_argument("-o", "--output", default="tdm8.wav", help="Output path")
    sr.set_defaults(func=cmd_record)

    sx = sub.add_parser("raw", help="Dump raw interleaved S32 bytes (no WAV header)")
    sx.add_argument("--bytes", type=int, default=1_048_576, help="Max bytes to write")
    sx.add_argument("-o", "--output", default="tdm8_raw.s32", help="Output path")
    sx.set_defaults(func=cmd_raw_dump)

    p.add_argument(
        "--fpga-mode",
        choices=sorted(set(FPGA_MODES.keys())),
        default=None,
        help="If set, run i2cset before capture (preset control byte)",
    )
    p.add_argument(
        "--fpga-value",
        type=lambda x: int(x, 0),
        default=None,
        help="Raw control byte (overrides --fpga-mode), e.g. 0x0F",
    )
    p.add_argument("--i2c-bus", type=int, default=1, help="I2C bus number for i2cset -y")
    p.add_argument("--i2c-addr", type=lambda x: int(x, 0), default=0x20, help="FPGA I2C address")

    return p


def main() -> None:
    args = build_parser().parse_args()
    if args.fpga_value is not None:
        val = args.fpga_value
    elif args.fpga_mode:
        val = FPGA_MODES[args.fpga_mode]
    else:
        val = None

    if val is not None:
        try:
            fpga_write_control(args.i2c_bus, args.i2c_addr, val)
            print(f"FPGA control register ←0x{val:02X} (bus {args.i2c_bus}, addr 0x{args.i2c_addr:02X})")
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            print(f"Warning: could not write FPGA over I2C: {e}", file=sys.stderr)

    args.func(args)


if __name__ == "__main__":
    main()
