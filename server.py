import argparse
import os
import shutil
import tempfile
import threading
from dataclasses import dataclass
from typing import Any, Optional

import asyncio
import numpy as np
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from audio.capture_alsa import AlsaDevice, AlsaI2SMicCapture
from audio.capture_pi_sd import PiSdRawCapture
from audio.dsp import FPGA_CHANNELS, AudioDsp, DspConfig
from audio.wav_export import write_s32le_stereo_wav


@dataclass
class LatestFrame:
    """Latest meters (+ optional waveform/mel when --full-dsp)."""

    stereo: bool
    channels: int
    db: float
    db_l: float
    db_r: float
    db_ch: list[float]
    wave: list[float]
    wave_r: list[float]
    wave_ch: list[list[float]]
    mel: list[list[float]]


class AudioPipeline:
    def __init__(
        self,
        *,
        capture: AlsaI2SMicCapture,
        dsp: AudioDsp,
        meters_only: bool = True,
    ) -> None:
        self.capture = capture
        self.dsp = dsp
        self.meters_only = bool(meters_only)

        z = [0.0] * dsp.cfg.wave_points
        n_ch = FPGA_CHANNELS
        self._ph_wave_ch = [list(z) for _ in range(n_ch)]
        self._ph_mel = [[dsp.cfg.db_floor] * dsp.cfg.mel_cols for _ in range(dsp.cfg.n_mels)]

        self._lock = threading.Lock()
        self._latest: LatestFrame = LatestFrame(
            stereo=False,
            channels=n_ch,
            db=dsp.cfg.db_floor,
            db_l=dsp.cfg.db_floor,
            db_r=dsp.cfg.db_floor,
            db_ch=[dsp.cfg.db_floor] * n_ch,
            wave=list(z),
            wave_r=list(z),
            wave_ch=[list(z) for _ in range(n_ch)],
            mel=[[dsp.cfg.db_floor] * dsp.cfg.mel_cols for _ in range(dsp.cfg.n_mels)],
        )

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # 15 s stereo WAV (mic pair 1 = ALSA channels 0+1): filled by capture thread
        self._record_lock = threading.Lock()
        self._recording = False
        self._record_chunks: list[np.ndarray] = []
        self._record_frames_target = 0
        self._record_frame_count = 0
        self._record_done = threading.Event()
        self._record_result_path: Optional[str] = None
        self._record_error: Optional[str] = None

        self._aln_align_requested = threading.Event()
        self._aln_align_done = threading.Event()
        self._aln_align_error: Optional[str] = None

        # Diagnostics for /health (written from capture thread; reads are best-effort).
        self.capture_state = "starting"
        self.capture_error: Optional[str] = None
        self.pcm_blocks = 0
        self.last_block_peak = 0
        self.aln_align_active = False
        self.aln_backend_status = ""

    def _on_aln_detail(self, msg: str) -> None:
        self.aln_backend_status = msg

    def start(self) -> None:
        if self._thread is not None:
            return

        self._stop.clear()
        self._thread = threading.Thread(target=self._run_capture_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            self.capture.stop()
        except Exception:
            pass

    def _run_capture_loop(self) -> None:
        self.capture_state = "starting"
        self.capture_error = None
        try:
            need_arecord = getattr(self.capture, "backend", "alsa") == "alsa"
            if need_arecord and shutil.which("arecord") is None:
                self.capture_state = "failed"
                self.capture_error = "arecord not found — install alsa-utils (e.g. apt install alsa-utils)"
                return

            self.capture.start()
        except Exception as e:
            self.capture_state = "failed"
            self.capture_error = str(e) or repr(e)
            return

        self.capture_state = "running"

        while not self._stop.is_set():
            try:
                if self._aln_align_requested.is_set():
                    self._aln_align_requested.clear()
                    self.aln_align_active = True
                    self.aln_backend_status = "Starting PI_ALN handshake…"
                    err: Optional[str] = None
                    try:
                        self.capture.run_aln_alignment_now(on_status=self._on_aln_detail)
                    except Exception as e:
                        err = str(e)
                        self.aln_backend_status = f"ALN error: {err}"
                    finally:
                        self.aln_align_active = False
                    self._finish_aln_align(err)
                    continue

                samples = self.capture.read_block()
            except Exception as e:
                self.capture_state = "failed"
                self.capture_error = f"{type(e).__name__}: {e}"
                break

            self.pcm_blocks += 1
            try:
                self.last_block_peak = int(np.max(np.abs(samples)))
            except Exception:
                self.last_block_peak = 0
            if self._recording:
                with self._record_lock:
                    if self._recording:
                        self._record_chunks.append(samples.copy())
                        self._record_frame_count += int(samples.shape[0])
                        if self._record_frame_count >= self._record_frames_target:
                            self._finalize_stereo_mic1_recording()
            if self.meters_only:
                out = self.dsp.process_int32_fpga8_meters_only(samples)
                with self._lock:
                    self._latest = LatestFrame(
                        stereo=False,
                        channels=FPGA_CHANNELS,
                        db=float(out["db"]),
                        db_l=float(out["db"]),
                        db_r=float(out["db"]),
                        db_ch=[float(x) for x in out["db_ch"]],
                        wave=self._ph_wave_ch[0],
                        wave_r=self._ph_wave_ch[1],
                        wave_ch=self._ph_wave_ch,
                        mel=self._ph_mel,
                    )
            else:
                out = self.dsp.process_int32_fpga8(samples)
                wave_ch = [np.asarray(w, dtype=np.float32).round(8).tolist() for w in out["wave_ch"]]
                z = [0.0] * self.dsp.cfg.wave_points
                with self._lock:
                    self._latest = LatestFrame(
                        stereo=False,
                        channels=FPGA_CHANNELS,
                        db=float(out["db"]),
                        db_l=float(out["db"]),
                        db_r=float(out["db"]),
                        db_ch=[float(x) for x in out["db_ch"]],
                        wave=wave_ch[0] if wave_ch else list(z),
                        wave_r=wave_ch[1] if len(wave_ch) > 1 else list(z),
                        wave_ch=wave_ch,
                        mel=np.asarray(out["mel"], dtype=np.float32).round(2).tolist(),
                    )

    def get_latest(self) -> LatestFrame:
        with self._lock:
            return LatestFrame(
                stereo=self._latest.stereo,
                channels=self._latest.channels,
                db=float(self._latest.db),
                db_l=float(self._latest.db_l),
                db_r=float(self._latest.db_r),
                db_ch=list(self._latest.db_ch),
                wave=list(self._latest.wave),
                wave_r=list(self._latest.wave_r),
                wave_ch=[list(w) for w in self._latest.wave_ch],
                mel=[list(row) for row in self._latest.mel],
            )

    def _finalize_stereo_mic1_recording(self) -> None:
        """Build 15 s stereo WAV from mic pair 1 (ALSA ch 0 = 1L, ch 1 = 1R). Caller holds no lock."""
        try:
            if not self._record_chunks:
                self._record_error = "no samples captured"
                return
            cat = np.concatenate(self._record_chunks, axis=0)
            cat = cat[: self._record_frames_target]
            if cat.ndim != 2 or cat.shape[1] < 2:
                self._record_error = "unexpected sample shape"
                return
            L = cat[:, 0].astype(np.int32, copy=False)
            R = cat[:, 1].astype(np.int32, copy=False)
            n = min(L.size, R.size)
            L = L[:n]
            R = R[:n]
            interleaved = np.empty(2 * n, dtype=np.int32)
            interleaved[0::2] = L
            interleaved[1::2] = R
            fd, path = tempfile.mkstemp(suffix=".wav", prefix="mic1_stereo_")
            os.close(fd)
            write_s32le_stereo_wav(path, interleaved, self.capture.sample_rate)
            self._record_result_path = path
        except Exception as e:
            self._record_error = str(e)
        finally:
            self._recording = False
            self._record_chunks = []
            self._record_frame_count = 0
            self._record_done.set()

    def request_stereo_mic1_wav_15s(self) -> str:
        """
        Record 15 s from the live stream: stereo WAV = ALSA channels 0+1 (mic pair 1 L/R).
        Blocks until the capture thread finishes writing the WAV (max ~20 s).
        """
        with self._record_lock:
            if self._recording:
                raise RuntimeError("Recording already in progress")
            if self._thread is None or not self._thread.is_alive():
                raise RuntimeError("Capture thread is not running")
            self._record_chunks = []
            self._record_frame_count = 0
            self._record_frames_target = 15 * self.capture.sample_rate
            self._recording = True
            self._record_done.clear()
            self._record_result_path = None
            self._record_error = None
        if not self._record_done.wait(timeout=22.0):
            with self._record_lock:
                self._recording = False
                self._record_chunks = []
            raise TimeoutError("Recording timed out (is ALSA capture running?)")
        err = self._record_error
        path = self._record_result_path
        if err:
            if path and os.path.isfile(path):
                try:
                    os.unlink(path)
                except OSError:
                    pass
            raise RuntimeError(err)
        if not path or not os.path.isfile(path):
            raise RuntimeError("Recording failed (no file)")
        return path

    def request_aln_align(self, timeout: float = 90.0) -> None:
        """Ask the capture thread to run the PI_ALN TDM alignment sequence (blocks until done)."""
        if self._thread is None or not self._thread.is_alive():
            raise RuntimeError("Capture is not running")
        self._aln_align_done.clear()
        self._aln_align_error = None
        self._aln_align_requested.set()
        if not self._aln_align_done.wait(timeout=timeout):
            self._aln_align_requested.clear()
            raise TimeoutError("ALN alignment timed out")
        err = self._aln_align_error
        if err:
            raise RuntimeError(err)

    def _finish_aln_align(self, err: Optional[str]) -> None:
        self._aln_align_error = err
        self._aln_align_done.set()

    def aln_dashboard_info(self) -> dict[str, Any]:
        cap = self.capture
        use_aln = cap.aln_bcm is not None
        return {
            "aln_gpio": cap.aln_bcm,
            "aln_capable": use_aln,
            "aln_gpio_ready": bool(getattr(cap, "_aln_gpio_ready", False)) if use_aln else False,
            "aln_align_active": self.aln_align_active,
            "aln_backend_status": self.aln_backend_status,
        }

    def capture_dashboard_info(self) -> dict[str, Any]:
        cap = self.capture
        if getattr(cap, "device", None) is not None:
            dev = cap.device.hw_string
        else:
            dev = getattr(cap, "source_label", "unknown")
        alive = self._thread is not None and self._thread.is_alive()
        peak = self.last_block_peak
        return {
            "state": self.capture_state,
            "error": self.capture_error,
            "alsa_device": dev,
            "capture_backend": getattr(cap, "backend", "alsa"),
            "sample_rate": cap.sample_rate,
            "pcm_blocks": self.pcm_blocks,
            "last_block_peak_int32": peak,
            "has_nonzero_pcm": peak > 0,
            "capture_thread_alive": alive,
            "pi_sd_frame_endian": getattr(cap, "frame_word_endian", None),
        }


def _parse_alsa_hw(hw: str) -> AlsaDevice:
    # Accept: hw:1,0 or 1,0
    hw = hw.strip()
    if hw.startswith("hw:"):
        hw = hw[3:]
    parts = hw.split(",")
    if len(parts) != 2:
        raise ValueError("Expected --alsa-hw in the form hw:<card>,<device> (e.g. hw:1,0)")
    return AlsaDevice(card=int(parts[0]), device=int(parts[1]))


def build_app(pipeline: AudioPipeline) -> FastAPI:
    app = FastAPI()
    base_dir = Path(__file__).resolve().parent
    templates = Jinja2Templates(directory=str(base_dir / "web" / "templates"))
    app.mount("/static", StaticFiles(directory=str(base_dir / "web" / "static")), name="static")

    @app.post("/api/fpga/aln-align")
    async def post_aln_align() -> dict[str, Any]:
        """Run PI_ALN handshake + TDM sync (8-channel + ALN GPIO only)."""
        loop = asyncio.get_event_loop()

        def run_align() -> None:
            pipeline.request_aln_align()

        try:
            await loop.run_in_executor(None, run_align)
        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e
        except TimeoutError as e:
            raise HTTPException(status_code=504, detail=str(e)) from e
        return {"ok": True}

    @app.get("/api/record/stereo_mic1_15s.wav")
    async def download_stereo_mic1_15s() -> Any:
        """15 s stereo WAV: ALSA channels 0+1 (FPGA mic pair 1 L/R)."""
        loop = asyncio.get_event_loop()

        def run_record() -> str:
            return pipeline.request_stereo_mic1_wav_15s()

        try:
            path = await loop.run_in_executor(None, run_record)
        except RuntimeError as e:
            msg = str(e)
            code = 409 if "already in progress" in msg else 503
            raise HTTPException(status_code=code, detail=msg) from e
        except TimeoutError as e:
            raise HTTPException(status_code=504, detail=str(e)) from e

        def read_and_remove() -> bytes:
            try:
                with open(path, "rb") as f:
                    return f.read()
            finally:
                try:
                    os.unlink(path)
                except OSError:
                    pass

        data = await loop.run_in_executor(None, read_and_remove)
        return Response(
            content=data,
            media_type="audio/wav",
            headers={"Content-Disposition": 'attachment; filename="stereo_mic1_15s.wav"'},
        )

    @app.get("/favicon.ico")
    async def favicon() -> Response:
        return Response(status_code=204)

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> Any:
        # Starlette 0.28+ expects (request=..., name=..., context=...).
        # Old pattern TemplateResponse("x.html", {"request": request}) passes a dict as the
        # template name and triggers Jinja2: TypeError: unhashable type: 'dict'.
        return templates.TemplateResponse(request=request, name="index.html", context={})

    @app.get("/health")
    async def health() -> dict[str, Any]:
        latest = pipeline.get_latest()
        out = {
            "ok": True,
            "db": latest.db,
            "stereo": latest.stereo,
            "channels": latest.channels,
            "db_l": latest.db_l,
            "db_r": latest.db_r,
            "db_ch": latest.db_ch,
            "meters_only": pipeline.meters_only,
        }
        out.update(pipeline.aln_dashboard_info())
        out["capture"] = pipeline.capture_dashboard_info()
        return out

    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)

    parser.add_argument(
        "--capture-backend",
        choices=("alsa", "pi_sd"),
        default="alsa",
        help="alsa: kernel I²S/TDM via arecord. pi_sd: raw 32-byte frames from --pi-sd-source.",
    )
    parser.add_argument(
        "--pi-sd-source",
        type=str,
        default="",
        help="FIFO, file, or - for stdin (32 bytes/frame). Required when --capture-backend pi_sd.",
    )
    parser.add_argument(
        "--pi-sd-frame-endian",
        choices=("big", "little"),
        default="big",
        help="Per-channel int32 byte order inside each 32-byte frame. "
        "'big' matches Lattice pi_tdm_serializer MSB-first (default). "
        "Use 'little' only if your bridge swaps endianness.",
    )
    parser.add_argument("--alsa-hw", type=str, default="", help="ALSA device, e.g. hw:1,0 (optional)")
    parser.add_argument("--sample-rate", type=int, default=48000)
    parser.add_argument("--format", type=str, default="S32_LE")
    parser.add_argument("--block-frames", type=int, default=2048)
    parser.add_argument(
        "--aln-gpio",
        type=int,
        default=16,
        help="BCM GPIO for PI_ALN output to FPGA (default 16; avoids I²S 18–20 & typical LCD pins). Use 0 to disable.",
    )
    parser.add_argument(
        "--full-dsp",
        action="store_true",
        help="Compute mel + waveforms every block (heavy on the Pi). Default is meters-only (dBFS per channel).",
    )
    args = parser.parse_args()

    device: Optional[AlsaDevice] = None
    if args.alsa_hw.strip():
        device = _parse_alsa_hw(args.alsa_hw)

    aln_bcm: Optional[int] = None if args.aln_gpio == 0 else args.aln_gpio

    if args.capture_backend == "pi_sd":
        src = args.pi_sd_source.strip()
        if not src:
            parser.error("--pi-sd-source is required when using --capture-backend pi_sd (FIFO path or -)")
        capture: AlsaI2SMicCapture | PiSdRawCapture = PiSdRawCapture(
            src,
            sample_rate=args.sample_rate,
            block_frames=args.block_frames,
            aln_bcm=aln_bcm,
            frame_word_endian=args.pi_sd_frame_endian,
        )
    else:
        capture = AlsaI2SMicCapture(
            device=device,
            sample_rate=args.sample_rate,
            format_str=args.format,
            block_frames=args.block_frames,
            aln_bcm=aln_bcm,
        )
    dsp = AudioDsp(DspConfig(sample_rate=args.sample_rate))

    pipeline = AudioPipeline(capture=capture, dsp=dsp, meters_only=not args.full_dsp)
    pipeline.start()

    app = build_app(pipeline)

    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()

