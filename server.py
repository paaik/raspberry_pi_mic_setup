import argparse
import shutil
import threading
from dataclasses import dataclass
from typing import Any, Optional

import numpy as np
import asyncio
from pathlib import Path
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from audio.capture_alsa import AlsaDevice, AlsaI2SMicCapture
from audio.dsp import AudioDsp, DspConfig


@dataclass
class LatestFrame:
    """Audio frame for WebSocket JSON. `channels` 1=mono, 2=stereo L/R, 8=FPGA 8-mic."""

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
        send_rate_hz: float = 10.0,
    ) -> None:
        self.capture = capture
        self.dsp = dsp
        self.stereo = capture.channels == 2
        self.multichannel = capture.channels > 2
        self.send_rate_hz = float(send_rate_hz)

        z = [0.0] * dsp.cfg.wave_points
        n_ch = capture.channels
        self._lock = threading.Lock()
        self._latest: LatestFrame = LatestFrame(
            stereo=self.stereo,
            channels=n_ch,
            db=dsp.cfg.db_floor,
            db_l=dsp.cfg.db_floor,
            db_r=dsp.cfg.db_floor,
            db_ch=[dsp.cfg.db_floor] * n_ch if n_ch > 2 else [],
            wave=list(z),
            wave_r=list(z),
            wave_ch=[list(z) for _ in range(n_ch)] if n_ch > 2 else [],
            mel=[[dsp.cfg.db_floor] * dsp.cfg.mel_cols for _ in range(dsp.cfg.n_mels)],
        )

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

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
        try:
            if shutil.which("arecord") is None:
                # Running on a dev machine without ALSA utilities.
                return

            self.capture.start()
        except Exception:
            # Keep zeros in the dashboard rather than crashing the server.
            return

        while not self._stop.is_set():
            samples = self.capture.read_block()
            if self.multichannel:
                out = self.dsp.process_int32_multichannel(samples)
                wave_ch = [
                    np.asarray(w, dtype=np.float32).round(8).tolist() for w in out["wave_ch"]
                ]
                z = [0.0] * self.dsp.cfg.wave_points
                with self._lock:
                    self._latest = LatestFrame(
                        stereo=False,
                        channels=self.capture.channels,
                        db=float(out["db"]),
                        db_l=float(out["db"]),
                        db_r=float(out["db"]),
                        db_ch=[float(x) for x in out["db_ch"]],
                        wave=wave_ch[0] if wave_ch else list(z),
                        wave_r=wave_ch[1] if len(wave_ch) > 1 else list(z),
                        wave_ch=wave_ch,
                        mel=np.asarray(out["mel"], dtype=np.float32).round(2).tolist(),
                    )
            elif self.stereo:
                out = self.dsp.process_int32_stereo(samples)
                w_l = np.asarray(out["wave"], dtype=np.float32).round(8).tolist()
                w_r = np.asarray(out["wave_r"], dtype=np.float32).round(8).tolist()
                with self._lock:
                    self._latest = LatestFrame(
                        stereo=True,
                        channels=2,
                        db=float(out["db"]),
                        db_l=float(out["db_l"]),
                        db_r=float(out["db_r"]),
                        db_ch=[],
                        wave=w_l,
                        wave_r=w_r,
                        wave_ch=[],
                        mel=np.asarray(out["mel"], dtype=np.float32).round(2).tolist(),
                    )
            else:
                out = self.dsp.process_int32_mono(samples)
                w = np.asarray(out["wave"], dtype=np.float32).round(8).tolist()
                with self._lock:
                    self._latest = LatestFrame(
                        stereo=False,
                        channels=1,
                        db=float(out["db"]),
                        db_l=float(out["db"]),
                        db_r=float(out["db"]),
                        db_ch=[],
                        wave=list(w),
                        wave_r=list(w),
                        wave_ch=[],
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

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> Any:
        # Starlette 0.28+ expects (request=..., name=..., context=...).
        # Old pattern TemplateResponse("x.html", {"request": request}) passes a dict as the
        # template name and triggers Jinja2: TypeError: unhashable type: 'dict'.
        return templates.TemplateResponse(request=request, name="index.html", context={})

    @app.get("/health")
    async def health() -> dict[str, Any]:
        latest = pipeline.get_latest()
        return {
            "ok": True,
            "db": latest.db,
            "stereo": latest.stereo,
            "channels": latest.channels,
            "db_l": latest.db_l,
            "db_r": latest.db_r,
            "db_ch": latest.db_ch,
        }

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        await ws.accept()
        interval = 1.0 / max(1e-6, pipeline.send_rate_hz)

        try:
            while True:
                latest = pipeline.get_latest()
                await ws.send_json(
                    {
                        "stereo": latest.stereo,
                        "channels": latest.channels,
                        "db": latest.db,
                        "db_l": latest.db_l,
                        "db_r": latest.db_r,
                        "db_ch": latest.db_ch,
                        "wave": latest.wave,
                        "wave_r": latest.wave_r,
                        "wave_ch": latest.wave_ch,
                        "mel": latest.mel,
                    }
                )
                await asyncio.sleep(interval)
        except WebSocketDisconnect:
            return

    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)

    parser.add_argument("--alsa-hw", type=str, default="", help="ALSA device, e.g. hw:1,0 (optional)")
    parser.add_argument("--sample-rate", type=int, default=48000)
    parser.add_argument(
        "--channels",
        type=int,
        default=1,
        help="1=mono, 2=stereo, 8=FPGA 8×I2S (interleaved S32_LE per ALSA).",
    )
    parser.add_argument("--format", type=str, default="S32_LE")
    parser.add_argument("--block-frames", type=int, default=2048)

    parser.add_argument("--send-rate-hz", type=float, default=10.0)
    args = parser.parse_args()

    if args.channels not in (1, 2, 8):
        raise SystemExit("Supported --channels: 1 (mono), 2 (stereo), or 8 (FPGA 8-mic bus).")

    device: Optional[AlsaDevice] = None
    if args.alsa_hw.strip():
        device = _parse_alsa_hw(args.alsa_hw)

    capture = AlsaI2SMicCapture(
        device=device,
        sample_rate=args.sample_rate,
        channels=args.channels,
        format_str=args.format,
        block_frames=args.block_frames,
    )
    dsp = AudioDsp(
        DspConfig(sample_rate=args.sample_rate),
        channels=args.channels,
    )

    pipeline = AudioPipeline(capture=capture, dsp=dsp, send_rate_hz=args.send_rate_hz)
    pipeline.start()

    app = build_app(pipeline)

    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()

