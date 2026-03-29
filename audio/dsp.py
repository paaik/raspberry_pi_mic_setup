from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


def hz_to_mel(hz: float) -> float:
    # Slaney-style mel scale
    return 2595.0 * math.log10(1.0 + hz / 700.0)


def mel_to_hz(mel: float) -> float:
    return 700.0 * (10.0 ** (mel / 2595.0) - 1.0)


def mel_filterbank(
    *,
    sample_rate: int,
    n_fft: int,
    n_mels: int,
    fmin: float = 0.0,
    fmax: float | None = None,
) -> np.ndarray:
    """
    Build a mel filterbank matrix of shape (n_mels, n_fft//2+1).
    """
    if fmax is None:
        fmax = sample_rate / 2.0

    # +2 for start/end points
    mel_points = np.linspace(hz_to_mel(fmin), hz_to_mel(fmax), n_mels + 2)
    hz_points = mel_to_hz(mel_points)

    # FFT bin frequencies are k * fs / n_fft
    bin_frequencies = np.floor((n_fft + 1) * hz_points / sample_rate).astype(int)
    bin_frequencies = np.clip(bin_frequencies, 0, n_fft // 2)

    fb = np.zeros((n_mels, n_fft // 2 + 1), dtype=np.float32)
    for m in range(1, n_mels + 1):
        left = bin_frequencies[m - 1]
        center = bin_frequencies[m]
        right = bin_frequencies[m + 1]
        if center == left:
            center = left + 1
        if right == center:
            right = center + 1

        # Rising slope
        if center > left:
            fb[m - 1, left:center] = (np.arange(left, center) - left) / (center - left)
        # Falling slope
        if right > center:
            fb[m - 1, center:right] = (right - np.arange(center, right)) / (right - center)

    return fb


@dataclass
class DspConfig:
    sample_rate: int = 48000
    n_fft: int = 1024
    hop_length: int = 256
    window: str = "hann"
    n_mels: int = 40
    mel_cols: int = 80
    fmin: float = 0.0
    fmax: float | None = None
    wave_points: int = 512
    wave_window_sec: float = 1.0
    db_floor: float = -100.0
    db_ceiling: float = 0.0


class RingBuffer1D:
    def __init__(self, size: int, dtype=np.float32) -> None:
        self.size = int(size)
        self.buf = np.zeros((self.size,), dtype=dtype)
        self.idx = 0
        self.full = False

    def append(self, x: np.ndarray) -> None:
        x = np.asarray(x, dtype=self.buf.dtype)
        if x.size == 0:
            return
        if x.size >= self.size:
            self.buf[:] = x[-self.size :]
            self.idx = 0
            self.full = True
            return

        pos = 0
        n = int(x.size)
        while pos < n:
            space = self.size - self.idx
            take = min(n - pos, space)
            self.buf[self.idx : self.idx + take] = x[pos : pos + take]
            self.idx += take
            pos += take
            if self.idx == self.size:
                self.idx = 0
                self.full = True

    def ordered(self) -> np.ndarray:
        """
        Return `size` samples in time order (oldest -> newest), newest on the right.
        While filling, left-pad with zeros so plot length stays fixed.
        """
        out = np.zeros((self.size,), dtype=self.buf.dtype)
        if self.full:
            out[:] = np.concatenate([self.buf[self.idx :], self.buf[: self.idx]])
            return out
        if self.idx > 0:
            out[self.size - self.idx : self.size] = self.buf[: self.idx]
        return out


class RingBuffer2DCols:
    def __init__(self, n_rows: int, n_cols: int, dtype=np.float32) -> None:
        self.n_rows = int(n_rows)
        self.n_cols = int(n_cols)
        self.buf = np.zeros((self.n_rows, self.n_cols), dtype=dtype)
        self.idx = 0  # next column to write
        self.full = False

    def append_column(self, col: np.ndarray) -> None:
        col = np.asarray(col)
        if col.shape != (self.n_rows,):
            col = np.reshape(col, (self.n_rows,))

        self.buf[:, self.idx] = col
        self.idx = (self.idx + 1) % self.n_cols
        if self.idx == 0:
            self.full = True

    def ordered(self) -> np.ndarray:
        """Time order left->right; pad empty past time slots with zeros."""
        out = np.zeros((self.n_rows, self.n_cols), dtype=self.buf.dtype)
        if self.full:
            out[:, :] = np.concatenate([self.buf[:, self.idx :], self.buf[:, : self.idx]], axis=1)
            return out
        if self.idx > 0:
            out[:, self.n_cols - self.idx : self.n_cols] = self.buf[:, : self.idx]
        return out


class AudioDsp:
    def __init__(self, cfg: DspConfig | None = None) -> None:
        self.cfg = cfg or DspConfig()

        self._window = np.hanning(self.cfg.n_fft).astype(np.float32)

        self._mel_fb = mel_filterbank(
            sample_rate=self.cfg.sample_rate,
            n_fft=self.cfg.n_fft,
            n_mels=self.cfg.n_mels,
            fmin=self.cfg.fmin,
            fmax=self.cfg.fmax,
        ).astype(np.float32)

        self._mel_cols = self.cfg.mel_cols
        self._mel = RingBuffer2DCols(n_rows=self.cfg.n_mels, n_cols=self._mel_cols, dtype=np.float32)

        # Waveform: decimate to a stable number of points for a fixed window.
        wave_step = int((self.cfg.sample_rate * self.cfg.wave_window_sec) / self.cfg.wave_points)
        self._wave_step = max(1, wave_step)
        self._wave = RingBuffer1D(self.cfg.wave_points, dtype=np.float32)

        self._pending = np.zeros((0,), dtype=np.float32)

        self._latest_db = float(self.cfg.db_floor)

    def process_int32_mono(self, samples_i32: np.ndarray) -> dict:
        """
        Consume int32 mono PCM samples and update internal rolling buffers.
        """
        x = samples_i32.astype(np.float32, copy=False)
        # Normalize int32 full scale to [-1, 1)
        x = x / 2147483648.0

        # dBFS from RMS over this block
        rms = float(np.sqrt(np.mean(x * x) + 1e-18))
        dbfs = 20.0 * math.log10(rms + 1e-18)
        self._latest_db = float(np.clip(dbfs, self.cfg.db_floor, self.cfg.db_ceiling))

        # Waveform updates (decimated)
        x_decim = x[:: self._wave_step]
        self._wave.append(x_decim)

        # Mel spectrogram updates
        self._pending = np.concatenate([self._pending, x])
        # Process as many STFT frames as possible
        n_fft = self.cfg.n_fft
        hop = self.cfg.hop_length
        while self._pending.size >= n_fft:
            frame = self._pending[:n_fft]
            self._pending = self._pending[hop:]

            windowed = frame * self._window
            spec = np.fft.rfft(windowed, n=n_fft)
            power = (np.abs(spec) ** 2).astype(np.float32)

            mel_power = self._mel_fb @ power
            mel_db = 10.0 * np.log10(mel_power + 1e-12)
            mel_db = np.clip(mel_db, self.cfg.db_floor, self.cfg.db_ceiling)
            self._mel.append_column(mel_db.astype(np.float32))

        return {
            "db": self._latest_db,
            "wave": self._wave.ordered(),
            "mel": self._mel.ordered(),
        }

