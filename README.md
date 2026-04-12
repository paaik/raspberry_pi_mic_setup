# Pi Mic Dashboard (Raspberry Pi 5 + I2S mic)

This project captures audio from a Raspberry Pi 5 I2S digital microphone (e.g. `DMM-4026-B-I2S-R`), computes:

- relative level in dBFS
- a live waveform
- a live mel spectrogram

and displays it at `http://localhost:<port>` via a WebSocket-backed dashboard.

## What you need

- Raspberry Pi OS (Bookworm recommended)
- `alsa-utils`
- Python 3

## Install dependencies (on the Pi)

```bash
sudo apt-get update
sudo apt-get install -y alsa-utils python3-pip

pip3 install -r requirements.txt
```

## Verify the microphone with ALSA

1. List ALSA devices:

```bash
arecord -l
```

2. Record a short WAV (mono, 48 kHz, 32-bit container):

```bash
arecord -D hw:<CARD>,<DEVICE> -c 1 -r 48000 -f S32_LE -t wav -V mono test.wav
```

Replace `<CARD>,<DEVICE>` with the values from `arecord -l`.

## Run the dashboard

**Mono (one mic):**

```bash
python3 server.py --port 8000 --alsa-hw hw:<CARD>,<DEVICE> --channels 1
```

**Stereo (left + right on one stereo ALSA device, e.g. two I2S MEMS mics):**

```bash
python3 server.py --port 8000 --alsa-hw hw:<CARD>,<DEVICE> --channels 2
```

**FPGA → Raspberry Pi (eight I2S microphones on one interleaved stream):**

The FPGA drives the mics and presents a **single I²S-style interface** to the Pi: **bit clock**, **word / LR clock**, and **one serial data line** carrying **eight 32-bit PCM words per sample period** in this order: mic **1L, 1R, 2L, 2R, 3L, 3R, 4L, 4R**. The Pi must see that stream as an **8-channel ALSA capture** (`S32_LE`). Your FPGA may pack samples in **256-word** hardware frames; as long as ALSA exposes continuous interleaved 8-channel PCM, this app will parse it.

```bash
python3 server.py --port 8000 --alsa-hw hw:<CARD>,<DEVICE> --channels 8 --sample-rate 48000
```

The dashboard then shows **per-channel dBFS**, **eight waveforms**, a **stacked VLC-style scope**, **eight VU bars**, and a **mel spectrogram** computed from the **mean** of all eight channels.

Then open:

`http://localhost:8000`

In the browser, **Record 15 s WAV — mic 1 stereo** saves a file with **left = ALSA channel 0** and **right = ALSA channel 1** (the first stereo pair: **1L / 1R** on an 8-mic FPGA capture, or the normal L/R bus when `--channels 2`). Mono (`--channels 1`) saves the same signal on both channels.

### Notes

- If you omit `--alsa-hw`, the program will try to auto-pick the first ALSA input device it finds.
- dB is displayed as *relative dBFS* (uncalibrated). 0 dBFS corresponds to full-scale PCM.
- With `--channels 2`, the dashboard shows **L** and **R** waveforms/scopes and **L/R dB**; the **mel spectrogram** and **spectrum bars** use **(L+R)/2** so you still get one time–frequency view.
- With **`--channels 8`**, avoid GPIO conflicts with the **Waveshare display** (see table below).

## Two I2S microphones on one Raspberry Pi (stereo)

Typical MEMS I2S parts (including PUI `DMM-4026-B-I2S-R`) are wired as a **stereo pair** on a **single I2S bus**:

| Mic signal | Connect |
|------------|---------|
| **BCLK** (bit clock / SCLK) | Both mics → **GPIO 18** (pin 12) |
| **LRCLK** / **WS** (word select) | Both mics → **GPIO 19** (pin 35) |
| **DOUT** / **SD** (data from mic to Pi) | **Both** mic data pins → **GPIO 20** (pin 38) — same Pi input |
| **VDD** | 3.3 V (both) |
| **GND** | Common ground |

**Channel select (`SEL`)** (if your breakout exposes it):

- **Left mic:** tie **SEL** to **GND**
- **Right mic:** tie **SEL** to **3.3 V** (or the opposite if your datasheet says so — always check the part’s pin list)

The Pi then sees **one stereo capture** (L slot / R slot on the shared data line). Use `arecord -c 2` and `--channels 2` in this app.

**Alternative (advanced):** Raspberry Pi 5 can expose extra I2S data inputs (e.g. additional SDI pins) with a **custom device-tree** overlay. That is only needed if your hardware uses **separate** data lines instead of the shared DOUT + SEL arrangement above.

**ALSA:** Your sound card must expose **2-channel** capture (as with the Google Voice HAT overlay or a proper stereo I2S overlay). Verify with:

```bash
arecord -D hw:<CARD>,<DEVICE> -c 2 -r 48000 -f S32_LE -t wav -d 3 -V stereo test_stereo.wav
```

### GPIO: FPGA I²S vs LCD (no shared pins)

Wire the FPGA to the Pi’s **primary I²S** pins (PCM). These do **not** overlap the **SPI display** pins used in `display_config/` (MOSI/SCLK/CE0, D/C, RST, BL).

| Signal | BCM GPIO | Physical pin | Notes |
|--------|-----------|--------------|--------|
| **BCLK** (bit clock from FPGA) | **18** | 12 | PCM_CLK; keep free if you use it for I²S |
| **LRCLK** / word select | **19** | 35 | PCM_FS |
| **DIN** / SD (data into Pi) | **20** | 38 | PCM_DIN — 8 logical channels must appear interleaved in ALSA |

**Display (do not use for I²S):** GPIO **8, 10, 11** (SPI0), **25** (D/C), **27** (RST), **12** (backlight in this repo’s `config.py`). Use **`config.py`** `PIN_BL` if you need to move the backlight off GPIO12.

Getting **8-channel** capture usually requires the correct **machine driver / device-tree overlay** for your FPGA timing (standard stereo I²S only exposes two slots; TDM or a custom FPGA framing may need a bespoke overlay). Until `arecord -c 8` works for your card, the dashboard cannot receive eight channels.

```bash
arecord -D hw:<CARD>,<DEVICE> -c 8 -r 48000 -f S32_LE -t wav -d 3 test_8ch.wav
```

## Troubleshooting

- If `arecord -l` shows no devices, double-check your wiring (BCLK/LRCLK/DATA) and consider adding/configuring a device-tree overlay so the Pi exposes the mic as a capture device.
- If the dashboard shows silence or an unstable waveform:
  - verify the `arecord` test WAV actually contains audio
  - try a different ALSA device from `arecord -l`

