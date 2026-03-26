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

Start the server:

```bash
python3 server.py --port 8000 --alsa-hw hw:<CARD>,<DEVICE>
```

Then open:

`http://localhost:8000`

### Notes

- If you omit `--alsa-hw`, the program will try to auto-pick the first ALSA input device it finds.
- dB is displayed as *relative dBFS* (uncalibrated). 0 dBFS corresponds to full-scale PCM.

## Troubleshooting

- If `arecord -l` shows no devices, double-check your wiring (BCLK/LRCLK/DATA) and consider adding/configuring a device-tree overlay so the Pi exposes the mic as a capture device.
- If the dashboard shows silence or an unstable waveform:
  - verify the `arecord` test WAV actually contains audio
  - try a different ALSA device from `arecord -l`

