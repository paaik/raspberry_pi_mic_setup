# Raspberry Pi · FPGA TDM mic array + LCD

This project runs on a **Raspberry Pi** that is wired to:

1. A **Waveshare 1.69″ ST7789 LCD** (4-wire **SPI0** — see `display_config/`).
2. A **Lattice FPGA** aggregator that exposes **`pi_sck`**, **`pi_sd`**, and **`pi_aln`** for an **8-channel** (4× I²S stereo lane) microphone stream.

The web dashboard (`server.py`) decodes the FPGA bitstream and shows **dBFS** per channel. The display stack is separate and uses **`display_config/`**; do **not** share SPI0 or the LCD GPIOs with the mic link.

---

## Quick start (software)

```bash
sudo apt update
sudo apt install -y python3-pip python3-pigpio pigpio
cd /path/to/raspberrypi_micsetup
pip3 install --user -r requirements.txt
sudo pigpiod
python3 server.py
```

Open **`http://<pi-ip>:8080`** (or `http://127.0.0.1:8080` on the Pi).

**Mock mode** (no FPGA, UI test only):

```bash
python3 server.py --mock-file /path/to/raw_capture.bin
```

---

## Raspberry Pi OS configuration

### SPI for the LCD (SPI0)

The Waveshare panel uses **SPI0** with **CE0** (kernel driver owns CE0 when SPI is enabled).

1. Enable SPI, e.g. with **Raspberry Pi Configuration → Interfaces → SPI**, or in `/boot/firmware/config.txt` (Bookworm) or `/boot/config.txt` (older images):

   ```text
   dtparam=spi=on
   ```

2. Reboot if you changed firmware settings.

3. Confirm a device exists, e.g. `/dev/spidev0.0`.

**Do not** use SPI0 pins for the FPGA mic stream; the LCD and capture must stay on separate peripherals/pins.

### pigpio (FPGA `pi_sd` capture)

This repo’s capture path uses **pigpio**’s **BSC SPI slave** (`bsc_xfer`) so the **FPGA supplies the bit clock** (the Pi is not the SPI master on that link).

1. Install and start the daemon:

   ```bash
   sudo apt install -y pigpio python3-pigpio
   sudo pigpiod
   ```

2. **Pi 5 / newer boards:** pigpio support depends on your image and pigpio version. If `pigpiod` or BSC capture fails, update pigpio or check your distro’s notes; you can still run **`--mock-file`** to validate the web UI and decoder.

### Optional: disable SPI1 if it clashes with BSC pins

On **BCM2835-class** Pis, pigpio’s BSC SPI slave uses **GPIO19/20/18/21**. The **`dtoverlay=spi1-1cs`** overlay also uses **GPIO21** (and **GPIO20** as MOSI). **Do not enable SPI1** on the same pins you need for BSC, or move SPI1 CS with overlay parameters so nothing conflicts.

On **Pi 4**, pigpio documents a **different** BSC pin set (see table below). Check **`/boot/firmware/overlays/README`** on the Pi for SPI overlays before enabling extra SPI buses.

---

## Pin wiring

All GPIO numbers below are **BCM** (Broadcom), not physical pin numbers unless noted.

### A) Waveshare 1.69″ LCD (`display_config/config.py`)

| Signal | BCM GPIO | Notes |
|--------|----------|--------|
| SPI0 MOSI | **10** | Fixed with SPI0 |
| SPI0 SCLK | **11** | Fixed with SPI0 |
| SPI0 CE0 | **8** | Usually owned by SPI driver |
| DC | **25** | Data/command |
| RST | **27** | Reset |
| BL (backlight) | **12** | Example in config; any free GPIO is fine |

Physical header positions for this panel are described on the vendor wiki:  
https://www.waveshare.net/wiki/1.69inch_LCD_Module  

Keep **`PIN_BL`**, **`PIN_DC`**, and **`PIN_RST`** consistent with how you actually wired the module.

### B) FPGA → Pi (TDM serial to Pi)

FPGA **outputs** (from your top-level HDL):

| FPGA signal | Role | Connect to on Pi |
|-------------|------|-------------------|
| **`pi_sck`** | Bit clock (~12 MHz in design) | BSC **SCLK** input (see table by SoC) |
| **`pi_sd`** | MSB-first serial data | BSC **MOSI** input (slave receives master-out data) |
| **`pi_aln`** | Alignment, Pi → FPGA | Any **free GPIO output** on the Pi (default **GPIO5** in `audio/config.py`) |

**Ground** must be common between Pi and FPGA boards.

#### BSC SPI slave pins (pigpio `bsc_xfer`)

These are the mappings described in the **pigpio** documentation for the BSC peripheral in **SPI slave** mode. **Your Pi model must match the row you wire.**

| SoC / Pi family | MOSI ← `pi_sd` | SCLK ← `pi_sck` | MISO (often unused) | CE |
|-----------------|----------------|-----------------|----------------------|-----|
| **BCM2835** (e.g. Pi 3, Zero2 W) | **GPIO20** (pin 38) | **GPIO19** (pin 35) | **GPIO18** (pin 12) | **GPIO21** (pin 40) |
| **BCM2711** (Pi 4, Pi 400) | **GPIO9** | **GPIO11** | **GPIO10** | **GPIO8** |
| **Pi 5** | Confirm with **`pinctrl`** and current pigpio docs; RP1 routing differs. | | | |

- The FPGA does not need the Pi’s **MISO** line for this design; follow your hardware guide for leaving it unconnected or tied off.
- **CE** behaviour may need to match your pigpio/BSC setup (some builds expect CE strapped or driven; verify with a scope or logic analyzer on first bring-up).

### Pins to keep free for the mic link

Avoid using these for the **LCD** or other peripherals if they are the **BSC slave** pins for your Pi model:

- On **BCM2835-class**: **GPIO18–21** (and especially **19/20** for clock/data).
- On **Pi 4**: **GPIO8–11** per the table above.

The LCD mapping in this repo uses **GPIO25, 27, 12** plus SPI0 — those must **not** be reused as **`PIN_PI_ALN`** or as BSC clock/data unless you change wiring and `audio/config.py`.

---

## Application configuration (`audio/config.py`)

| Setting | Purpose |
|---------|---------|
| **`PIN_PI_ALN`** | BCM GPIO the Pi drives to **FPGA `pi_aln`** (default **5**). |
| **`ALIGN_MIN_CONSECUTIVE_ZERO_FRAMES`** | After **`PI_ALN=1`**, how many **32-byte zero** superframes must be seen before **`PI_ALN=0`**. |
| **`ALIGN_FLUSH_TIMEOUT_S`** / **`ALIGN_WAIT_MARKER_TIMEOUT_S`** | Limits for alignment waits at startup. |
| **`BSC_SPI_CTRL_RX`** | pigpio BSC control word (CPOL/CPHA tweaks if needed). |

Alignment sequence (implemented in `audio/aln_sync.py` and `audio/pi_sd_decode.py`):

1. **`PI_ALN = 1`** — FPGA sends zero frames; software discards RX until idle zeros are stable.
2. **`PI_ALN = 0`** — Software waits for one **all-0xFF** marker superframe (32 bytes).
3. Software decodes **32-byte** superframes into **8× signed 32-bit** channels while **`PI_ALN` stays 0** (high aborts streaming in the Verilog serializer).

---

## Running as a service (optional)

Example pattern (adapt paths and user):

```ini
# /etc/systemd/system/mic-dashboard.service
[Unit]
Description=FPGA mic TDM dashboard
After=network.target

[Service]
Type=simple
ExecStartPre=/usr/bin/pigpiod
ExecStart=/usr/bin/python3 /home/pi/raspberrypi_micsetup/server.py --host 0.0.0.0 --port 8080
WorkingDirectory=/home/pi/raspberrypi_micsetup
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Ensure **`pigpiod`** is not started twice; use **`ExecStartPre`** or a **`Requires=`** unit depending on your image.

---

## Troubleshooting

| Symptom | Things to check |
|---------|------------------|
| **No `/dev/spidev0.0`** | SPI not enabled; fix `config.txt` / overlays and reboot. |
| **`pigpio daemon not reachable`** | Run **`sudo pigpiod`** before **`server.py`**. |
| **Flat / stuck dBFS** | Alignment timeouts; scope **`pi_sck`/`pi_sd`/`pi_aln`**; increase timeouts; verify **`PIN_PI_ALN`**. |
| **Display glitches** | Lower `SPI_BAUDRATE` in `display_config/config.py`. |
| **Pin conflicts** | Compare LCD GPIOs, `PIN_PI_ALN`, and BSC pins for your **exact** Pi model. |

---

## Related folders

| Path | Role |
|------|------|
| `audio/` | Decode, alignment, metering, pigpio capture |
| `display_config/` | ST7789 LCD + optional `mic_status_lcd.py` (polls `/api/meters`) |
| `web/` | Flask templates and static assets for the dashboard |
| `Past_Lattice 4 Avril copy/` | FPGA Verilog and constraints (source of framing behaviour) |
