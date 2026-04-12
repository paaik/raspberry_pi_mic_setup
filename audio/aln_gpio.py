"""
Drive PI_ALN (Pi → FPGA): HIGH = align / zeros from FPGA; LOW = normal TDM.

Uses RPi.GPIO on the Raspberry Pi when available; no-op elsewhere.
"""

from __future__ import annotations

from typing import Callable, Optional

_setter: Optional[Callable[[bool], None]] = None
_cleanup: Optional[Callable[[], None]] = None


def try_init_aln_output(bcm_pin: int) -> bool:
    """
    Configure BCM `bcm_pin` as a push-pull output for PI_ALN.
    Returns True if GPIO is usable, False on import/runtime failure (dev PC).
    """
    global _setter, _cleanup
    _setter = None
    _cleanup = None

    try:
        import RPi.GPIO as GPIO  # type: ignore[import-untyped]
    except ImportError:
        return False

    try:
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(bcm_pin, GPIO.OUT, initial=GPIO.LOW)

        def _set(high: bool) -> None:
            GPIO.output(bcm_pin, GPIO.HIGH if high else GPIO.LOW)

        def _clean() -> None:
            try:
                GPIO.cleanup(bcm_pin)
            except Exception:
                pass

        _setter = _set
        _cleanup = _clean
        return True
    except Exception:
        return False


def aln_set(high: bool) -> None:
    if _setter is not None:
        _setter(high)


def aln_cleanup() -> None:
    global _setter, _cleanup
    if _cleanup is not None:
        _cleanup()
    _setter = None
    _cleanup = None
