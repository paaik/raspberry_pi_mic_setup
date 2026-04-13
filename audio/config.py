"""
Hardware / protocol constants for the Lattice TDM aggregator -> Raspberry Pi link.

Verilog reference: Past_Lattice 4 Avril copy/tdm_aggregator_top.v
 - pi_sck is clk_12m (12 MHz bit clock to the Pi)
  - pi_sd is MSB-first serial; one 256-bit superframe per 32 bytes
  - pi_aln high: FPGA shifts zero frames; falling edge -> one 0xFF..FF marker
 frame, then audio superframes (pi_tdm_serializer.v)

Superframe layout (MSB first on the wire = first byte is bits [255:248]):
  {lane0_frame, lane1_frame, lane2_frame, lane3_frame}
Each lane is 64 bits: [63:32] = WS=0 word (Left), [31:0] = WS=1 word (Right).
Lane / mic_sd mapping matches mic_capture_4lane.v:
  lane0 <- mic_sd1 -> ch0 L, ch1 R
  lane1 <- mic_sd2 -> ch2 L, ch3 R
  lane2 <- mic_sd3 -> ch4 L, ch5 R
  lane3 <- mic_sd4 -> ch6 L, ch7 R

Wiring goal: do NOT use SPI0 (display uses SPI0 + GPIO25/27 per display_config).
This project uses the SoC BSC peripheral in SPI slave mode via pigpio bsc_xfer.

BCM2835-class (Pi 3 / Zero2 / etc.) BSC SPI slave pinout (pigpio docs):
  MOSI (data in from FPGA pi_sd) = GPIO20  (header pin 38)
  SCLK (clock in from FPGA pi_sck) = GPIO19 (header pin 35)
  MISO                          = GPIO18 (header pin 12) — not used by FPGA
  CE                            = GPIO21 (header pin 40)

BCM2711 (Pi 4 / Pi 400) uses different BSC pin mapping — see pigpio bsc_xfer
table (typically GPIO9/11/10/8).

Pi 5 (BCM2712 / RP1 GPIO): use the pigpio `bsc_xfer` SPI mapping for your board
revision; confirm pins with `pinctrl` / the Pi 5 GPIO header guide. pigpio may
require a Pi-5-capable build — if BSC is unavailable, use an alternate capture
path.

pi_aln: any free GPIO output, not shared with the LCD (avoid 25, 27, 12 from
display_config unless you changed those). Default: GPIO5.

Alignment (host drives pi_aln; matches pi_tdm_serializer.v):
  1) PI_ALN=1 — FPGA emits zero superframes; host discards RX until idle zeros.
  2) PI_ALN=0 — host ignores TDM until one full 0xFF..FF marker superframe.
  3) Host then decodes 32-byte superframes while PI_ALN stays0 (high aborts
     streaming in Verilog).
"""

from __future__ import annotations

FRAME_BITS = 256
FRAME_BYTES = FRAME_BITS // 8

MARKER = bytes([0xFF]) * FRAME_BYTES
ZERO_FRAME = bytes([0x00]) * FRAME_BYTES

# pigpio BSC SPI control: EN | SP | RE (enable, SPI mode, receive enable)
# Tweak PH/PL bits if you need CPOL/CPHA different from mode 0.
BSC_SPI_CTRL_RX = 0x00000303

# BCM2711 (Pi 4): set True to use the alternate BSC SPI pin set from pigpio docs.
BCM2711_BSC_SPI = False

# GPIO for pi_aln (BCM number). Idle-before-capture is high (1); leave low (0)
# during normal TDM so the FPGA stays in ST_RUN.
PIN_PI_ALN = 5

# After PI_ALN=1, require this many consecutive zero superframes before ALN goes low.
ALIGN_MIN_CONSECUTIVE_ZERO_FRAMES = 4

# Timeouts for blocking alignment in start_capture (seconds).
ALIGN_FLUSH_TIMEOUT_S = 1.0
ALIGN_WAIT_MARKER_TIMEOUT_S = 1.0

# If no TDM superframes arrive for this long while decoding, treat mics as inactive.
MIC_ACTIVE_STALE_AFTER_S = 2.0
