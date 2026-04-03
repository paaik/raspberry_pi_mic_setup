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
    stereo: bool
    db: float
    db_l: float
    db_r: float
    wave: list[float]
    wave_r: list[float]
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
        self.stereo = capture.channels >= 2
        self.send_rate_hz = float(send_rate_hz)

        z = [0.0] * dsp.cfg.wave_points
        self._lock = threading.Lock()
        self._latest: LatestFrame = LatestFrame(
            stereo=self.stereo,
            db=dsp.cfg.db_floor,
            db_l=dsp.cfg.db_floor,
            db_r=dsp.cfg.db_floor,
            wave=list(z),
            wave_r=list(z),
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
            if self.stereo:
                out = self.dsp.process_int32_stereo(samples)
                w_l = np.asarray(out["wave"], dtype=np.float32).round(8).tolist()
                w_r = np.asarray(out["wave_r"], dtype=np.float32).round(8).tolist()
                with self._lock:
                    self._latest = LatestFrame(
                        stereo=True,
                        db=float(out["db"]),
                        db_l=float(out["db_l"]),
                        db_r=float(out["db_r"]),
                        wave=w_l,
                        wave_r=w_r,
                        mel=np.asarray(out["mel"], dtype=np.float32).round(2).tolist(),
                    )
            else:
                out = self.dsp.process_int32_mono(samples)
                w = np.asarray(out["wave"], dtype=np.float32).round(8).tolist()
                with self._lock:
                    self._latest = LatestFrame(
                        stereo=False,
                        db=float(out["db"]),
                        db_l=float(out["db"]),
                        db_r=float(out["db"]),
                        wave=list(w),
                        wave_r=list(w),
                        mel=np.asarray(out["mel"], dtype=np.float32).round(2).tolist(),
                    )

    def get_latest(self) -> LatestFrame:
        with self._lock:
            return LatestFrame(
                stereo=self._latest.stereo,
                db=float(self._latest.db),
                db_l=float(self._latest.db_l),
                db_r=float(self._latest.db_r),
                wave=list(self._latest.wave),
                wave_r=list(self._latest.wave_r),
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
        return {"ok": True, "db": latest.db, "stereo": latest.stereo, "db_l": latest.db_l, "db_r": latest.db_r}

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
                        "db": latest.db,
                        "db_l": latest.db_l,
                        "db_r": latest.db_r,
                        "wave": latest.wave,
                        "wave_r": latest.wave_r,
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
        help="1=mono, 2=stereo L/R (two mics on one I2S stereo capture).",
    )
    parser.add_argument("--format", type=str, default="S32_LE")
    parser.add_argument("--block-frames", type=int, default=2048)

    parser.add_argument("--send-rate-hz", type=float, default=10.0)
    args = parser.parse_args()

    if args.channels not in (1, 2):
        raise SystemExit("This dashboard only supports --channels 1 (mono) or 2 (stereo).")

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
        stereo=args.channels >= 2,
    )

    pipeline = AudioPipeline(capture=capture, dsp=dsp, send_rate_hz=args.send_rate_hz)
    pipeline.start()

    app = build_app(pipeline)

    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()

