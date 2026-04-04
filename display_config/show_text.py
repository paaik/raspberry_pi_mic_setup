#!/usr/bin/env python3
"""
Draw text on the Waveshare 1.69\" 240×280 LCD (ST7789V2, SKU 24382).

  python3 show_text.py "Hello from Pi"
  python3 show_text.py --lines "Line one" "Line two" --size 24

Run from the display_config directory (or set PYTHONPATH). Prefer sudo only if
your user is not in the spi and gpio groups.
"""

from __future__ import annotations

import argparse
import os
import sys

from PIL import Image, ImageDraw, ImageFont

# Allow running as `python3 display_config/show_text.py` from repo root
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import config as cfg
from display_driver import create_display, show_pil_image


def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for path in candidates:
        if os.path.isfile(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def render_text(lines: list[str], font_size: int, fg: str, bg: str):
    img = Image.new("RGB", (cfg.WIDTH, cfg.HEIGHT), bg)
    draw = ImageDraw.Draw(img)
    font = load_font(font_size)

    y = 8
    for line in lines:
        draw.text((8, y), line, font=font, fill=fg)
        y += font_size + 6
        if y > cfg.HEIGHT - font_size:
            break
    return img


def main() -> None:
    p = argparse.ArgumentParser(description="Show text on Waveshare 1.69\" ST7789 LCD")
    p.add_argument("text", nargs="*", help="Single-line text (if no --lines)")
    p.add_argument("--lines", nargs="+", help="Multiple lines of text")
    p.add_argument("--size", type=int, default=22, help="Font size (default 22)")
    p.add_argument("--fg", default="#ffffff", help="Foreground hex color")
    p.add_argument("--bg", default="#000044", help="Background hex color")
    args = p.parse_args()

    if args.lines:
        lines = args.lines
    elif args.text:
        lines = [" ".join(args.text)]
    else:
        lines = ["Waveshare 1.69\"", "240×280 ST7789", "Ready."]

    display = create_display()
    img = render_text(lines, args.size, args.fg, args.bg)
    show_pil_image(display, img)


if __name__ == "__main__":
    main()
