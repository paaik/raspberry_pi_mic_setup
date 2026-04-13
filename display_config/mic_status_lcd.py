#!/usr/bin/env python3
"""
Show FPGA mic stream status on the Waveshare 1.69\" LCD (ST7789).

Polls the dashboard JSON from server.py and displays whether the 8-channel TDM
decode is **ACTIVE** (streaming + recent frames), **IDLE**, or **NOT LOCKED**
(alignment / decoder not in stream phase yet).

Run (typically in a second terminal while server.py runs):

  cd display_config
  python3 mic_status_lcd.py

Options override `MIC_STATUS_*` in config.py.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import config as cfg
from display_driver import create_display, show_pil_image
from show_text import render_text


def fetch_meters(url: str, timeout: float) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def build_lines(data: dict) -> tuple[list[str], str]:
    active = bool(data.get("mics_active"))
    streaming = bool(data.get("decoder_streaming"))
    frames = int(data.get("frames", 0))
    err = data.get("last_error")

    if active:
        status, fg = "Mics: ACTIVE", "#00dd88"
    elif streaming:
        status, fg = "Mics: IDLE (no frames)", "#ffaa33"
    else:
        status, fg = "Mics: NOT LOCKED", "#ff6655"

    lines = [status, f"Frames: {frames}", "8 ch · FPGA TDM"]
    channels = data.get("channels") or []
    if channels:
        peak_db = max(float(c.get("dbfs", -120.0)) for c in channels)
        lines.append(f"Peak: {peak_db:.1f} dBFS")
    if err:
        lines.append(str(err)[:48])
    return lines, fg


def main() -> None:
    p = argparse.ArgumentParser(description="Show mic ACTIVE/IDLE on ST7789 LCD")
    p.add_argument("--url", default=cfg.MIC_STATUS_API_URL, help="Meters JSON URL")
    p.add_argument(
        "--interval",
        type=float,
        default=cfg.MIC_STATUS_REFRESH_S,
        help="Refresh interval (s)",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=cfg.MIC_STATUS_HTTP_TIMEOUT_S,
        help="HTTP timeout (s)",
    )
    p.add_argument("--size", type=int, default=20, help="Font size")
    args = p.parse_args()

    display = create_display()
    bg = "#000044"

    while True:
        try:
            data = fetch_meters(args.url, args.timeout)
            lines, fg = build_lines(data)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as e:
            lines = ["Mics: UNKNOWN", "No dashboard?", str(e)[:42]]
            fg = "#aaaaaa"
        img = render_text(lines, args.size, fg, bg)
        show_pil_image(display, img)
        time.sleep(max(args.interval, 0.2))


if __name__ == "__main__":
    main()
