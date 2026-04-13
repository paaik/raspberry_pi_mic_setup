# ALSA Mic Setup (Raspberry Pi + FPGA TDM + ST7789 LCD)

This repo targets a **Raspberry Pi** wired to a **Lattice FPGA** aggregator (8-channel I²S / TDM microphones) and optionally a **Waveshare 1.69″ ST7789** display on **SPI0**.

Use **`display_config/`** for the LCD and **`tdm_capture/`** for ALSA capture of the8 × 32-bit TDM frame.

---

## Pin assignments (BCM GPIO unless noted)

Physical (**phys**) numbers are for the **40-pin** header on a full-size Pi.

### Waveshare 1.69″ LCD (SPI0)

Same mapping as in [`display_config/README.md`](display_config/README.md); backlight is moved off **GPIO18** so that pin can stay on **I²S** if needed.

| LCD (cable) | Signal | Pi (BCM) | Phys |
|-------------|--------|----------|------|
| VCC |3.3 V | 3.3 V | 3.3 V |
| GND | GND | GND | GND |
| DIN | MOSI | **GPIO10** | 19 |
| CLK | SCLK | **GPIO11** | 23 |
| CS | CE0 | **GPIO8** | 24 |
| DC | D/C | **GPIO25** | 22 |
| RST | Reset | **GPIO27** | 13 |
| BL | Backlight | **GPIO12** *(see note)* | 32 |

**Note:** Waveshare’s wiki often uses **GPIO18** for backlight. This project defaults to **GPIO12** so **GPIO18** remains free for **I²S BCLK** when the Pi is clock-slave to the FPGA. Match **`PIN_BL`** in `display_config/config.py` to how you wire **BL**.

---

### FPGA — I²S / TDM to the Pi (`dtoverlay=i2s1`, Pi as clock **consumer**)

The FPGA drives **bit clock** and **frame / word select**; the Pi receives **serial data** on the capture (**SDI**) line.

Naming below matches the aggregator nets **`pi_sck`**, **`pi_sd`**, **`pi_aln`**. Map **`pi_aln`** to the line your bitstream uses for **LRCK / WS / frame sync** (or alignment strobes if you named it that on the FPGA side).

| FPGA net | I²S role (typical) | Pi (BCM) | Phys | Notes |
|----------|-------------------|----------|------|--------|
| **`pi_sck`** | BCLK / SCK | **GPIO18** | 12 | Bit clock from FPGA |
| **`pi_aln`** | LRCK / WS / FS *(or your sync)* | **GPIO19** | 35 | Word / frame clock from FPGA |
| **`pi_sd`** | Serial data → Pi | **GPIO20** | 38 | **DIN / SDI** — TDM bitstream into the Pi |
| — | GND | GND | e.g. 6, 9, 14, 20, 25, 30, 34, 39 | Common ground |

**Pi 4 and earlier:** one I²S peripheral; the usual header pins for this mode are still **18 / 19 / 20** (and **21** is the default **output** data pin if you ever need it).

**Pi 5:** the documented **I²S1** mux also uses **GPIO18** (BCLK) and **GPIO19** (WS); **GPIO20** is **I²S1_SDI[0]** for the first data **in** line. If your overlay or design uses extra data lanes on **GPIO21–27**, avoid clashes with **LCD RST on GPIO27** — use a single-lane TDM setup or move LCD reset to another free GPIO and update `config.py`.

Enable I²S and the slave overlay in `/boot/firmware/config.txt` (or `/boot/config.txt` on some images), reboot, then check `aplay -l` / `arecord -l` for the new card (often **`hw:1,0`**).

---

### FPGA — I²C control (`i2cset`, address **0x20**, bus **1**)

Used to switch TDM / mux modes (e.g. `i2cset -y 1 0x20 0x0F`).

| Signal | Pi (BCM) | Phys |
|--------|----------|------|
| **SDA** | **GPIO2** | 3 |
| **SCL** | **GPIO3** | 5 |
| GND | GND | — |

Enable **I2C** in `raspi-config` (or `dtparam=i2c_arm=on`). A working link should show the FPGA at **0x20** on the chosen bus, for example:

```bash
i2cdetect -l
i2cdetect -y 1
```

If your hardware uses a bit-banged or secondary bus, adjust the bus number accordingly.

---

## Quick conflict check

| BCM | LCD | I²S / FPGA | I²C |
|-----|-----|--------------|-----|
| 2,3 | — | — | SDA, SCL |
| 8, 10, 11 | SPI0 CS, MOSI, SCLK | — | — |
| 12 | BL *(this repo default)* | — | — |
| 18, 19, 20 | — *(keep free if BL not on 18)* | BCLK, WS, DIN | — |
| 25, 27 | DC, RST | — | — |

---

## Related folders

| Path | Purpose |
|------|---------|
| [`display_config/`](display_config/) | ST7789 SPI display, `config.py` pin constants |
| [`tdm_capture/`](tdm_capture/) | ALSA 8-channel capture, example `asoundrc` |

Always treat FPGA **TDM / test patterns** as **capture-only**; play back through a normal stereo device after exporting or down-mixing if needed.
