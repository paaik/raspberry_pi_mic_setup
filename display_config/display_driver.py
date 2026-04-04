"""
Initialize the Waveshare 1.69\" ST7789 display (240×280) for use with Pillow.

Requires: Adafruit Blinka, adafruit-circuitpython-rgb-display, SPI enabled.
"""

from __future__ import annotations

import board
import digitalio
from adafruit_rgb_display import st7789

import config as cfg


def create_display() -> st7789.ST7789:
    spi = board.SPI()

    # On Raspberry Pi OS Bookworm+, GPIO8 (CE0) is owned by the SPI driver when
    # SPI is enabled, so DigitalInOut(board.CE0) raises lgpio "GPIO busy".
    # /dev/spidev0.0 already drives CE0 per transfer — no separate CS pin object.
    cs = None

    dc = digitalio.DigitalInOut(getattr(board, f"D{cfg.PIN_DC}"))
    dc.direction = digitalio.Direction.OUTPUT

    rst = digitalio.DigitalInOut(getattr(board, f"D{cfg.PIN_RST}"))
    rst.direction = digitalio.Direction.OUTPUT

    bl = digitalio.DigitalInOut(getattr(board, f"D{cfg.PIN_BL}"))
    bl.direction = digitalio.Direction.OUTPUT
    bl.value = True

    return st7789.ST7789(
        spi,
        width=cfg.WIDTH,
        height=cfg.HEIGHT,
        x_offset=cfg.X_OFFSET,
        y_offset=cfg.Y_OFFSET,
        rotation=cfg.ROTATION,
        cs=cs,
        dc=dc,
        rst=rst,
        baudrate=cfg.SPI_BAUDRATE,
    )


def show_pil_image(display: st7789.ST7789, image) -> None:
    """Send a PIL Image to the panel (resized to WIDTH×HEIGHT if needed)."""
    from PIL import Image

    if not isinstance(image, Image.Image):
        raise TypeError("Expected PIL.Image.Image")
    if image.size != (cfg.WIDTH, cfg.HEIGHT):
        try:
            resample = Image.Resampling.LANCZOS
        except AttributeError:
            resample = Image.LANCZOS
        image = image.resize((cfg.WIDTH, cfg.HEIGHT), resample)
    display.image(image.convert("RGB"))
