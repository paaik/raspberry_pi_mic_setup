"""
Waveshare 1.69\" LCD Module — SKU 24382
Resolution: 240×280, driver: ST7789V2, 4-wire SPI.

Pin map (BCM / physical) matches Waveshare wiki:
  https://www.waveshare.net/wiki/1.69inch_LCD_Module
"""

# Display geometry
WIDTH = 240
HEIGHT = 280

# SPI (SPI0, CE0) — use board.SPI() on Pi; MOSI/SCLK/CE0 are fixed by header.
SPI_BAUDRATE = 50_000_000  # Lower to 32_000_000 if you see glitches.

# GPIO (BCM numbers) — must match your wiring
PIN_DC = 25   # Data/Command — wiki: Pi phys pin 22
PIN_RST = 27  # Reset — wiki: Pi phys pin 13
PIN_BL = 18   # Backlight (PWM capable on 18) — wiki: Pi phys pin 12

# ST7789 internal RAM is 240×320; this panel is 240×280. If image is shifted
# vertically, try y_offset 0, 20, or 40 (see README).
X_OFFSET = 0
Y_OFFSET = 0

# 0 = portrait (240 wide × 280 tall). Try 90, 180, 270 if text is sideways.
ROTATION = 0
