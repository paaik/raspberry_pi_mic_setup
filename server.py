#!/usr/bin/env python3
"""
Web dashboard for 8-channel FPGA TDM levels (dBFS).

Run on Raspberry Pi (after starting pigpiod for BSC capture):
  python3 server.py

Mock mode (no hardware):
  python3 server.py --mock-file path/to/raw.bin
"""

from __future__ import annotations

import argparse
import math
import threading
import time
from typing import Any, Dict, Optional

from flask import Flask, jsonify, render_template

from audio import config as audio_cfg
from audio.aln_sync import run_pi_aln_alignment
from audio.capture_bsc import PigpioBscSpiCapture
from audio.capture_mock import MockFileCapture
from audio.dsp import dbfs_from_peak, smooth_peak
from audio.pi_sd_decode import CHANNEL_LABELS, PiSdDecoder

app = Flask(__name__, static_folder="web/static", template_folder="web/templates")

_state_lock = threading.Lock()
_state: Dict[str, Any] = {
    "channels": [
        {
            "id": i,
            "label": CHANNEL_LABELS[i],
            "dbfs": -120.0,
            "peak_linear": 0,
        }
        for i in range(8)
    ],
    "frames": 0,
    "last_error": None,
    "last_frame_at": None,  # time.monotonic() after last decoded superframe batch
}

_capture: Optional[Any] = None
_decoder = PiSdDecoder()
_peak_hold = [0.0] * 8
_smooth_coeff = 0.92


def _on_raw_bytes(chunk: bytes) -> None:
    try:
        frames = _decoder.push(chunk)
    except Exception as exc:  # noqa: BLE001
        with _state_lock:
            _state["last_error"] = str(exc)
        return

    if not frames:
        return

    peaks = [0] * 8
    for frame in frames:
        for i, s in enumerate(frame):
            a = abs(int(s))
            if a > peaks[i]:
                peaks[i] = a

    with _state_lock:
        _state["last_frame_at"] = time.monotonic()
        _state["frames"] += len(frames)
        for i in range(8):
            _peak_hold[i] = smooth_peak(_peak_hold[i], float(peaks[i]), _smooth_coeff)
            sm = int(_peak_hold[i])
            db = dbfs_from_peak(sm)
            ch = _state["channels"][i]
            ch["dbfs"] = db if math.isfinite(db) else -120.0
            ch["peak_linear"] = sm


def start_capture(mock_file: Optional[str]) -> None:
    global _capture
    _decoder.alignment_begin()
    if mock_file:
        _capture = MockFileCapture(mock_file, _on_raw_bytes)
    else:
        _capture = PigpioBscSpiCapture(_on_bytes=_on_raw_bytes)
    _capture.start()
    time.sleep(0.05)
    run_pi_aln_alignment(_capture, _decoder)


def stop_capture() -> None:
    global _capture
    if _capture is not None:
        _capture.stop()
        _capture = None


@app.route("/")
def index() -> str:
    return render_template("index.html")


def _mics_active_now() -> bool:
    """True when TDM is streaming and superframes arrived recently."""
    if not _decoder.is_streaming:
        return False
    with _state_lock:
        t = _state["last_frame_at"]
    if t is None:
        return False
    return (time.monotonic() - t) < audio_cfg.MIC_ACTIVE_STALE_AFTER_S


@app.route("/api/meters")
def api_meters() -> Any:
    mics_active = _mics_active_now()
    decoder_streaming = _decoder.is_streaming
    with _state_lock:
        payload = {
            "channels": [dict(c) for c in _state["channels"]],
            "frames": _state["frames"],
            "last_error": _state["last_error"],
            "mics_active": mics_active,
            "decoder_streaming": decoder_streaming,
        }
    return jsonify(payload)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Mic array level dashboard")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument(
        "--mock-file",
        default=None,
        help="Replay raw pi_sd bytes from file instead of pigpio BSC capture.",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    start_capture(args.mock_file)
    try:
        app.run(host=args.host, port=args.port, threaded=True)
    finally:
        stop_capture()


if __name__ == "__main__":
    main()
