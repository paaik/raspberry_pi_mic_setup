# Waveshare 1.69″ LCD (SKU **24382**) — 240×280 ST7789V2

Configuration and a small Python example to **show text** on the IPS SPI module.

- **Product:** [Waveshare 1.69inch LCD Module](https://www.waveshare.com/1.69inch-LCD-Module.htm)  
- **Wiki:** [1.69inch LCD Module](https://www.waveshare.net/wiki/1.69inch_LCD_Module)

## Hardware wiring (8-pin cable → Raspberry Pi)

| LCD (GH1.25) | Signal | Raspberry Pi (BCM) | Physical pin |
|--------------|--------|--------------------|--------------|
| VCC          | Power  | 3.3V               | 3.3V         |
| GND          | Ground | GND                | GND          |
| DIN          | MOSI   | GPIO10 (MOSI)      | **19**       |
| CLK          | SCLK   | GPIO11 (SCLK)      | **23**       |
| CS           | CE0    | GPIO8 (CE0)        | **24**       |
| DC           | D/C    | **GPIO25**         | **22**       |
| RST          | Reset  | **GPIO27**         | **13**       |
| BL           | Backlight | **GPIO12** | **32**       |

Use **3.3 V** logic (module has level shifting; still avoid 5 V on GPIO).

**Backlight:** Waveshare’s wiki uses GPIO18 (phys 12). This repo defaults to **GPIO12** (phys 32) so **GPIO18** can stay on **I2S** (e.g. microphone BCLK). Connect the LCD **BL** lead to the Pi pin for your chosen BCM number and set `PIN_BL` in `config.py` to match.

## Enable SPI on Raspberry Pi OS

```bash
sudo raspi-config
# Interface Options → SPI → Enable
sudo reboot
```

Check:

```bash
ls /dev/spidev0.*
```

You should see `spidev0.0` (and possibly `spidev0.1`).

## Permissions (avoid `sudo` for Python)

```bash
sudo usermod -aG spi,gpio "$USER"
# log out and back in
```

## Python dependencies

From the **`display_config`** folder (or your venv):

```bash
cd /path/to/raspberrypi_micsetup/display_config
pip3 install -r requirements-display.txt
```

On **Bookworm**, if pip refuses system installs, use a venv:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-display.txt
```

Install a font (optional but recommended):

```bash
sudo apt-get install -y fonts-dejavu-core
```

## Show text

```bash
cd /path/to/raspberrypi_micsetup/display_config
python3 show_text.py "Hello from Raspberry Pi"
python3 show_text.py --lines "Line 1" "Line 2" --size 20 --fg "#00ff88" --bg "#101018"
```

## Tuning `config.py`

| Setting        | If something looks wrong |
|----------------|---------------------------|
| `Y_OFFSET`     | Image shifted up/down — try `0`, `20`, or `40` (ST7789 RAM is taller than the panel). |
| `X_OFFSET`   | Rarely needed; try small values if columns are clipped. |
| `ROTATION`   | `0`, `90`, `180`, or `270` if the panel is sideways. |
| `SPI_BAUDRATE` | Flicker or garbage — lower to `32_000_000` or `16_000_000`. |

## Files in this folder

| File | Purpose |
|------|---------|
| `config.py` | Resolution, GPIO BCM numbers, SPI speed, offsets. |
| `display_driver.py` | Create `ST7789` object (Blinka + `adafruit_rgb_display`). |
| `show_text.py` | CLI: render multiline text with Pillow and push to the LCD. |
| `requirements-display.txt` | Pip packages for the display stack. |

## Official Waveshare examples

Waveshare ships C / Python demos in **`LCD_Module_RPI_code.zip`** (see wiki). This folder is a **minimal, pip-based** alternative aligned with their pin table.

## Troubleshooting

- **`No module named 'board'`** — install **Adafruit-Blinka** (`requirements-display.txt`).
- **`Permission denied` on `/dev/spidev0.0`** — add user to **`spi`** group (see above).
- **Blank screen, wiring OK** — confirm backlight GPIO18 is high (script sets it); try lowering `SPI_BAUDRATE`.
- **Pi 5** — use current Bookworm + Blinka; if GPIO names differ, set pins in `config.py` and map them in `display_driver.py` using the names shown by `pinout` on the Pi.
