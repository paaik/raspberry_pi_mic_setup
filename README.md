# FPGA 8-mic dashboard (Raspberry Pi)

This project captures **eight int32 mic channels** either from **kernel ALSA** (`arecord`, I²S/TDM) or from a **raw `pi_sd` byte stream** (256-bit / 32-byte frames from a custom FPGA serializer — no Linux sound device). It computes per-channel **dBFS** (and optionally mel + waveforms), and serves a **lightweight** web UI at `http://localhost:<port>` that **polls `GET /health`** (no Plotly, no WebSocket — easy on the Pi browser).

Channel order: **1L, 1R, 2L, 2R, 3L, 3R, 4L, 4R**.

## Requirements

- Raspberry Pi OS (Bookworm recommended)
- `alsa-utils` (only if you use **`--capture-backend alsa`**)
- Python 3
- **`pip install RPi.GPIO`** on the Pi if you use **PI_ALN** (default BCM **16**)

## Install

```bash
sudo apt-get update
sudo apt-get install -y alsa-utils python3-pip
pip3 install -r requirements.txt
```

## Verify ALSA (8 channels)

```bash
arecord -l
arecord -D hw:<CARD>,<DEVICE> -c 8 -r 48000 -f S32_LE -t wav -d 3 test_8ch.wav
```

## Run

```bash
python3 server.py --port 8000 --alsa-hw hw:<CARD>,<DEVICE> --sample-rate 48000
```

- Omit **`--alsa-hw`** to auto-pick the first capture device (if any).
- **`--aln-gpio`** defaults to **BCM 16** (PI_ALN → FPGA). Use **`--aln-gpio 0`** if unwired.
- **`POST /api/fpga/aln-align`** or the dashboard button runs the PI_ALN TDM alignment handshake (not at startup).
- By default the server runs **meters-only DSP** (dBFS per channel + mix). Use **`--full-dsp`** if you need mel + wave buffers for your own tooling (heavier on the Pi).

Open **`http://localhost:8000`**. The UI shows **per-channel dBFS**, **Record 15sec .WAV -ch1** (mic pair 1 = ALSA ch 0+1), and **Run FPGA TDM alignment** when ALN is enabled.

### Raw `pi_sd` databus (custom 256-bit frames, no ALSA)

If the FPGA drives **`pi_sd` + `pi_sck`** with **32-byte frames** (256 bits = eight little-endian `int32` samples per frame, order **1L, 1R, … 4R**) instead of standard I²S that `arecord` can open, use a **FIFO or pipe** (or stdin) as the bridge from your SPI/GPIO capture helper into Python:

```bash
mkfifo /tmp/fpga_pi_sd
python3 server.py --capture-backend pi_sd --pi-sd-source /tmp/fpga_pi_sd --sample-rate 48000
```

Use **`--pi-sd-source -`** to read framed bytes from **stdin**. Decoding is in **`audio/pi_sd_decode.py`**; adjust there if your Verilog uses different **byte order** inside each 32-bit word. This repo does **not** implement the low-level clocked sampling of `pi_sd` — only **framed** 32-byte chunks.

### PI_ALN (optional, Pi → FPGA)

| **PI_ALN** | **FPGA PCM (8 ch)** |
|------------|----------------------|
| **1** | All channels **0x00000000** until a full block is zeros. |
| **1 → 0** | One frame **0xFFFFFFFF** on all channels (sync). |
| **0** | Live TDM on **DOUT**. |

### GPIO: I²S vs LCD

| Signal | BCM | Phys |
|--------|-----|------|
| BCLK | 18 | 12 |
| LRCLK | 19 | 35 |
| DIN | 20 | 38 |
| PI_ALN (default) | **16** | **36** |

Avoid clashing with the Waveshare LCD (**SPI 8/10/11**, D/C **25**, RST **27**, BL e.g. **12** — see `display_config/config.py`).

Getting **8-channel** capture usually needs a correct driver / device-tree for your FPGA timing.

## Troubleshooting

- No ALSA device: check wiring and overlays until `arecord -c 8` works.
- Silence / garbage: confirm `test_8ch.wav`; try a lower effective rate or FPGA alignment (**PI_ALN** button).
- Dashboard stuck on **“Connecting…”** or **“Cannot reach /health”**: open the page at **`http://<pi-ip>:8000`** (same host/port as `server.py`). **HTTPS pages cannot call `http://` APIs** (mixed content) unless you terminate TLS with a reverse proxy on the same origin.
- **`capture.state` failed** in `/health`: read `capture.error` (e.g. wrong `--alsa-hw`, device busy, or `arecord` missing).
- **Levels stuck at ~−100 dBFS** with **`last_block_peak_int32` always 0**: PCM may be all zeros — wrong card/device, I²S not connected, or FPGA not driving **DOUT** (see the on-page hint when blocks exceed 40 and peak stays 0).
