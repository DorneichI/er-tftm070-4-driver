"""ertftm070 — Python driver for the EastRising ER-TFTM070-4V2.1.

A 7" 800x480 TFT (SSD1963 controller) driven over a bit-banged 16-bit
8080 bus from a Raspberry Pi Zero/1/2/3/4.  The board is strapped for
16-bit 8080, so one WR strobe writes one 16-bit pixel.

Quickstart::

    pip install ertftm070[Pillow]

    from ertftm070 import Display

    with Display() as lcd:            # verified wiring + init, backlight on
        lcd.fill(0xF800)             # red screen in ~0.6 s
        lcd.image(pil_image, x=0, y=0)

``BACKEND`` reports which pixel path is active: ``"fast"`` (the compiled
C extension), ``"slow"`` (the pure-Python fallback) or ``"sim"`` (the
browser simulator, selected with ``ERTFTM070_DISPLAY=sim`` — set it
before starting Python, like every backend choice).

Full wiring table: docs/WIRING.md.  Debugging lore: docs/LESSONS.md.
"""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from . import backends
from .colors import rgb565
from .display import Display
from .errors import Ertftm070Error, NotOnRaspberryPi
from .init import INIT_ALT, INIT_BD, INIT_UTFT
from .pins import DEFAULT_PINS, Pins
from .touch import Touch, TouchCalibration, TouchPoint

try:
    __version__ = version("ertftm070")
except PackageNotFoundError:  # not installed (e.g. running from a source tree)
    __version__ = "0.3.0"

BACKEND = backends.BACKEND

__all__ = [
    "Display",
    "Pins",
    "DEFAULT_PINS",
    "rgb565",
    "INIT_UTFT",
    "INIT_ALT",
    "INIT_BD",
    "Ertftm070Error",
    "NotOnRaspberryPi",
    "Touch",
    "TouchPoint",
    "TouchCalibration",
    "BACKEND",
    "__version__",
]
