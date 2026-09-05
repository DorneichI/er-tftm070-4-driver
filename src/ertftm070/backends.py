"""Bus backends: how bytes and pixels reach the display.

Two interchangeable implementations of the same small :class:`Bus`
protocol:

* :class:`_FastioBus` — wraps the compiled ``ertftm070._fastio`` C
  extension (~0.6 s full screen).
* :class:`_MmioBus` — pure Python over ``/dev/gpiomem`` via ``mmap``
  (~3 s full screen).  Used automatically when the extension was not
  built, or forced with ``ERTFTM070_FORCE_SLOW=1``.

Which one is active is decided once at import time and exposed as
``ertftm070.BACKEND`` (``"fast"`` or ``"slow"``).  Both are otherwise
identical: the caller (``Display``) keeps CS/DC framing in Python while
the backend performs the timing-critical strobes.

Both backends need the BCM2835-style GPIO block exposed by
``/dev/gpiomem``: Raspberry Pi Zero/1/2/3/4.  On Pi 5 (RP1) or any
non-Pi machine, :meth:`open` raises :class:`~ertftm070.NotOnRaspberryPi`.
"""
from __future__ import annotations

import mmap
import os
import struct
import time
import warnings
from typing import Any, Protocol

from .pins import DEFAULT_PINS, Pins

# BCM2835-style GPIO register byte offsets (see _fastio.c for the map).
_GPSET0 = 0x1C
_GPCLR0 = 0x28
_GPLEV0 = 0x34
_FSEL = [0x00, 0x04, 0x08, 0x0C, 0x10, 0x14]  # one word per 10 pins
_MAP_LEN = 0xB4


class Bus(Protocol):
    """The timing-critical operations a backend must provide.

    All methods require an open bus.  Pin numbers are BCM GPIO numbers.
    """

    def open(self) -> None:
        """Open /dev/gpiomem and map the GPIO registers."""

    def close(self) -> None:
        """Release the mapping; safe to call more than once."""

    def pin_mode(self, pin: int, output: bool) -> None:
        """Set a pin to output (True) or input (False)."""

    def pin_write(self, pin: int, level: bool) -> None:
        """Drive a pin high (True) or low (False)."""

    def write_byte(self, value: int) -> None:
        """One register byte on DB0-7 with a WR strobe."""

    def read_word(self) -> int:
        """Sample 16 bits on DB0-15 with an RD strobe."""

    def pixel_stream(self, buf: Any) -> None:
        """One WR strobe per 16-bit word (bit 0 = DB0), + 2 trailing dummies.

        ``buf`` must be a contiguous buffer of 16-bit words —
        ``array("H")``, ``memoryview``, or ``bytes``.  CS/DC framing is
        the caller's job.
        """


# ----------------------------------------------------------------------
# Backend selection (once, at import time)
# ----------------------------------------------------------------------

_fastio = None  # type: Optional[Any]

if os.environ.get("ERTFTM070_FORCE_SLOW") == "1":
    _forced_slow = True
else:
    _forced_slow = False
    try:
        import ertftm070._fastio as _fastio  # noqa: F401
    except ImportError:
        _fastio = None

BACKEND = "fast" if _fastio is not None else "slow"

if BACKEND == "slow" and not _forced_slow:
    warnings.warn(
        "ertftm070: the compiled _fastio extension is not available; "
        "using the slow pure-Python backend (~3 s full screen instead of "
        "~0.6 s).  To silence this on purpose, set ERTFTM070_FORCE_SLOW=1.",
        RuntimeWarning,
        stacklevel=2,
    )


def get_backend(pins: Pins = DEFAULT_PINS, backend: Bus | None = None) -> Bus:
    """Return a :class:`Bus` for ``pins``.

    An explicitly passed ``backend`` (e.g. a fake in tests) wins;
    otherwise the best available backend is created.
    """
    if backend is not None:
        return backend
    if _fastio is not None:
        return _FastioBus(pins)
    return _MmioBus(pins)


# ----------------------------------------------------------------------
# Fast backend: the C extension
# ----------------------------------------------------------------------


class _FastioBus:
    """Wraps ``ertftm070._fastio``.  Thin; all timing lives in C."""

    def __init__(self, pins: Pins):
        self.pins = pins
        self._m = None  # type: Optional[Any]

    def _mod(self):
        if self._m is None:
            raise RuntimeError("bus not open — call open() first")
        return self._m

    def open(self) -> None:
        self._m = _fastio
        self._m.open()

    def close(self) -> None:
        if self._m is not None:
            self._m.close()
            self._m = None

    def pin_mode(self, pin: int, output: bool) -> None:
        self._mod().pin_mode(pin, bool(output))

    def pin_write(self, pin: int, level: bool) -> None:
        self._mod().pin_write(pin, bool(level))

    def write_byte(self, value: int) -> None:
        self._mod().write_byte(value)

    def read_word(self) -> int:
        return self._mod().read_word()

    def pixel_stream(self, buf: Any) -> None:
        self._mod().pixel_stream(buf)


# ----------------------------------------------------------------------
# Slow backend: pure Python over /dev/gpiomem
# ----------------------------------------------------------------------


class _MmioBus:
    """Pure-Python fallback.  Same protocol, ~10x slower pixel path.

    Word writes go through ``mmap`` slice assignment + ``struct.pack``;
    the inter-statement gaps of the interpreter (~µs) double as the
    strobe timing, comfortably above the SSD1963's ~100 ns minimum.
    """

    def __init__(self, pins: Pins):
        self.pins = pins
        self._mm = None  # type: Optional[mmap.mmap]
        self._fd = None  # type: Optional[int]
        self._clr_low = _byte_mask(0xFF, pins.data_low)
        self._clr_high = _byte_mask(0xFF, pins.data_high)
        self._clr_all = self._clr_low | self._clr_high
        self._set_low = [_byte_mask(v, pins.data_low) for v in range(256)]
        self._set_high = [_byte_mask(v, pins.data_high) for v in range(256)]
        self._wr = 1 << pins.wr
        self._rd = 1 << pins.rd

    def open(self) -> None:
        if self._mm is not None:
            return
        self._fd = os.open("/dev/gpiomem", os.O_RDWR | os.O_SYNC)
        try:
            self._mm = mmap.mmap(
                self._fd, _MAP_LEN, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE
            )
        except BaseException:
            os.close(self._fd)
            self._fd = None
            raise

    def close(self) -> None:
        if self._mm is not None:
            self._mm.close()
            self._mm = None
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def _check(self) -> mmap.mmap:
        if self._mm is None:
            raise RuntimeError("bus not open — call open() first")
        return self._mm

    def _write_word(self, off: int, value: int) -> None:
        self._mm[off : off + 4] = struct.pack("<I", value)

    def _read_word_reg(self, off: int) -> int:
        return struct.unpack("<I", self._mm[off : off + 4])[0]

    def pin_mode(self, pin: int, output: bool) -> None:
        self._check()
        word = pin // 10
        shift = (pin % 10) * 3
        val = self._read_word_reg(_FSEL[word])
        val = (val & ~(7 << shift)) | ((1 if output else 0) << shift)
        self._write_word(_FSEL[word], val)

    def pin_write(self, pin: int, level: bool) -> None:
        self._check()
        self._write_word(_GPSET0 if level else _GPCLR0, 1 << pin)

    def write_byte(self, value: int) -> None:
        mm = self._check()
        mm[_GPCLR0 : _GPCLR0 + 4] = struct.pack("<I", self._clr_low)
        mm[_GPSET0 : _GPSET0 + 4] = struct.pack("<I", self._set_low[value & 0xFF])
        mm[_GPCLR0 : _GPCLR0 + 4] = struct.pack("<I", self._wr)
        mm[_GPSET0 : _GPSET0 + 4] = struct.pack("<I", self._wr)

    def read_word(self) -> int:
        mm = self._check()
        for pin in self.pins.data:
            self.pin_mode(pin, False)
        mm[_GPCLR0 : _GPCLR0 + 4] = struct.pack("<I", self._rd)
        time.sleep(0.000002)  # let the controller drive the bus
        lev = self._read_word_reg(_GPLEV0)
        mm[_GPSET0 : _GPSET0 + 4] = struct.pack("<I", self._rd)
        value = 0
        for bit, pin in enumerate(self.pins.data):
            if lev & (1 << pin):
                value |= 1 << bit
            self.pin_mode(pin, True)
        return value

    def pixel_stream(self, buf: Any) -> None:
        mm = self._check()
        clr = self._clr_all
        set_low, set_high = self._set_low, self._set_high
        wr, gpset, gpclr = self._wr, _GPSET0, _GPCLR0
        pack = struct.pack
        last = 0
        for v in buf:
            last = set_low[v & 0xFF] | set_high[v >> 8]
            mm[gpclr : gpclr + 4] = pack("<I", clr)
            mm[gpset : gpset + 4] = pack("<I", last)
            mm[gpclr : gpclr + 4] = pack("<I", wr)
            mm[gpset : gpset + 4] = pack("<I", wr)
        # 2 trailing dummy pixels — absorb the burst-tail quirk
        for _ in range(2):
            mm[gpclr : gpclr + 4] = pack("<I", clr)
            mm[gpset : gpset + 4] = pack("<I", last)
            mm[gpclr : gpclr + 4] = pack("<I", wr)
            mm[gpset : gpset + 4] = pack("<I", wr)


def _byte_mask(value: int, pins) -> int:
    mask = 0
    for bit, pin in enumerate(pins):
        if value & (1 << bit):
            mask |= 1 << pin
    return mask
