# FPGA `pi_sd` framing (Lattice TDM aggregator)

This matches the RTL under `Past_Lattice 4 Avril copy/` in this repo, especially:

- `tdm_aggregator_top.v` — `pi_sck = clk_12m`, `pi_sd` serial out
- `superframe_loader.v` — packs four 64-bit lane frames into one 256-bit `frame_reg`
- `mic_capture_4lane.v` — channel assignment into each lane
- `pi_tdm_serializer.v` — shifts **MSB first** onto `pi_sd` with `pi_aln` alignment modes

## 256-bit superframe layout

`superframe_loader` does:

```verilog
frame_reg <= {lane0_frame, lane1_frame, lane2_frame, lane3_frame};
```

Verilog concatenation puts **lane0 in the MSBs** of the 256-bit word.

Each `laneN_frame` is 64 bits: **upper 32 bits** first in time from the mic pair’s “even” word slot, **lower 32 bits** from the “odd” slot (`mic_capture_4lane`):

| Lane   | `[63:32]` | `[31:0]` |
|--------|-----------|----------|
| lane0  | ch0 (1L)  | ch1 (1R) |
| lane1  | ch2 (2L)  | ch3 (2R) |
| lane2  | ch4 (3L)  | ch5 (3R) |
| lane3  | ch6 (4L)  | ch7 (4R) |

So in the **256-bit `frame_reg`**, from **MSB to LSB**:

| Bit range   | Channel |
|-------------|---------|
| `[255:224]` | ch0 (1L) |
| `[223:192]` | ch1 (1R) |
| `[191:160]` | ch2 (2L) |
| `[159:128]` | ch3 (2R) |
| `[127:96]`  | ch4 (3L) |
| `[95:64]`   | ch5 (3R) |
| `[63:32]`   | ch6 (4L) |
| `[31:0]`    | ch7 (4R) |

## Serializer bit order on `pi_sd`

`pi_tdm_serializer` outputs **bit 255 first** (MSB of the packed register), then shifts left each `pi_sck` rising edge. So for each 32-bit channel word, **bit 31 is transmitted first** — the natural **big-endian** layout of the four bytes of that `int32` on the wire.

Therefore, after you group the serial stream into **32-byte chunks** (one per 256-bit frame), decode each channel as **big-endian signed 32-bit** (`>i4` in NumPy). This is the **default** in `audio/pi_sd_decode.py` and `server.py --pi-sd-frame-endian big`.

If your SPI bridge or logic analyzer swaps bytes, use `--pi-sd-frame-endian little`.

## `pi_aln` (alignment)

While `pi_aln` is high, the FPGA sends **zero frames**; on the falling edge it emits a **marker frame** (all ones), then live data. The Python `tdm_align` handshake expects the same **32-byte framing** in the byte stream (see `audio/tdm_align.py`).

## Why `arecord -l` does not show `pi_sd`

ALSA only lists devices the **Linux kernel** exposes as sound cards (I²S/TDM with BCLK+LRCK framing, USB audio, etc.). Your top-level exposes **`pi_sck` + `pi_sd` + `pi_aln`** as a **custom serial link**, not the Pi’s I²S **slave** pins with valid TDM timing the `snd-soc` stack can attach to.

**Software cannot turn a GPIO bitstream into an ALSA card** without one of:

1. **FPGA change**: output **standard I²S or TDM** on the Pi’s `PCM_FS` / `PCM_CLK` / `PCM_DIN` (or documented hat pins) with a rate and slot count the device tree overlay supports — then `arecord` can work.
2. **Kernel driver**: a loadable module that clocks data in and registers an ALSA PCM device (non-trivial).
3. **Userspace path (this project)**: SPI or another bridge delivers **aligned 32-byte frames** to a FIFO; `server.py --capture-backend pi_sd` decodes them. PipeWire/PulseAudio “virtual mic” is possible by feeding **decoded** PCM from a daemon — still not `arecord` seeing the raw pin.

## Practical capture path on the Pi

The FPGA tree includes `tools/pi_sd_dump_spi.py`: it reads **spidev** at high speed and writes raw bytes to stdout or a file. Pipe that into a FIFO consumed by `server.py --capture-backend pi_sd`, or extend the tool to emit 32-byte-aligned frames only.

**Alignment**: arbitrary `spidev` read sizes may **split** frames; you may need a small C helper or state machine that **resyncs** on 256-bit boundaries using the all-ones marker after `pi_aln`.
