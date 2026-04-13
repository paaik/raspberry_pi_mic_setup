"""Level metering: int32 samples -> dBFS (full scale = 2**31)."""

from __future__ import annotations

import math
FULL_SCALE = float(2**31)
FLOOR_DB = -120.0


def dbfs_from_sample(x: int, full_scale: float = FULL_SCALE) -> float:
    ax = abs(int(x))
    if ax <= 0:
        return FLOOR_DB
    return 20.0 * math.log10(ax / full_scale)


def dbfs_from_peak(peak: int, full_scale: float = FULL_SCALE) -> float:
    return dbfs_from_sample(peak, full_scale)


def smooth_peak(prev: float, new_peak_linear: float, coeff: float) -> float:
    """Exponential smoothing on linear peak (not dB)."""
    if prev <= 0:
        return float(new_peak_linear)
    return prev * coeff + float(new_peak_linear) * (1.0 - coeff)
