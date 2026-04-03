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

Then open:

`http://localhost:8000`

### Notes

- If you omit `--alsa-hw`, the program will try to auto-pick the first ALSA input device it finds.
- dB is displayed as *relative dBFS* (uncalibrated). 0 dBFS corresponds to full-scale PCM.
- With `--channels 2`, the dashboard shows **L** and **R** waveforms/scopes and **L/R dB**; the **mel spectrogram** and **spectrum bars** use **(L+R)/2** so you still get one time–frequency view.

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

## Troubleshooting

- If `arecord -l` shows no devices, double-check your wiring (BCLK/LRCLK/DATA) and consider adding/configuring a device-tree overlay so the Pi exposes the mic as a capture device.
- If the dashboard shows silence or an unstable waveform:
  - verify the `arecord` test WAV actually contains audio
  - try a different ALSA device from `arecord -l`

