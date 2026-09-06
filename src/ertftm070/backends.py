"""Bus backends: how bytes and pixels reach the display.

Two interchangeable implementations of the same small :class:`Bus`
protocol:

* :class:`_FastioBus` — wraps the compiled ``ertftm070._fastio`` C
  extension (~0.6 s full screen).
* :class:`_MmioBus` — pure Python over ``/dev/gpiomem`` via ``mmap``
  (~10 s full screen).  Used automatically when the extension was not
  built, or forced with ``ERTFTM070_FORCE_SLOW=1``.

Which one is active is decided once at import time and exposed as
``ertftm070.BACKEND`` (``"fast"`` or ``"slow"``).  Both are otherwise
identical: register-level operations keep their CS/DC framing in Python
(``Display``), while blits delegate one row per call to the backend
(:meth:`Bus.row_blit`) so the fast path pays a single C call per row.

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
from array import array
from typing import Any, Protocol

from .errors import NotOnRaspberryPi
from .pins import DEFAULT_PINS, Pins

# BCM2835-style GPIO register byte offsets (see _fastio.c for the map).
_GPSET0 = 0x1C
_GPCLR0 = 0x28
_GPLEV0 = 0x34
_FSEL = [0x00, 0x04, 0x08, 0x0C, 0x10, 0x14]  # one word per 10 pins
_MAP_LEN = 0xB4

# SoCs whose /dev/gpiomem maps the BCM2835-style GPIO block both backends
# write.  bcm2711 is the newest supported (Pi 4B/400/CM4); Pi 5's
# BCM2712 exposes an incompatible RP1 block under the same device node.
_SUPPORTED_SOC = (
    b"brcm,bcm2708",
    b"brcm,bcm2709",
    b"brcm,bcm2710",
    b"brcm,bcm2835",
    b"brcm,bcm2836",
    b"brcm,bcm2837",
    b"brcm,bcm2711",
)


def _read_compatible() -> bytes:
    """The DT compatible strings of the running machine (NUL-separated)."""
    with open("/proc/device-tree/compatible", "rb") as fh:
        return fh.read()


def _check_platform() -> None:
    """Raise :class:`~ertftm070.NotOnRaspberryPi` unless we run on a
    supported Raspberry Pi.

    Both backends write BCM2835-style register offsets into whatever
    ``/dev/gpiomem`` maps.  On a Pi 5 that is the RP1 block, where the
    same offsets address entirely different registers — a silent wrong
    result, not a crash — so the SoC is verified before the mapping is
    touched.
    """
    try:
        compatible = _read_compatible()
    except OSError as exc:
        raise NotOnRaspberryPi(
            "no /proc/device-tree/compatible — this does not look like a "
            "Raspberry Pi running Linux"
        ) from exc
    if not any(soc in compatible for soc in _SUPPORTED_SOC):
        raise NotOnRaspberryPi(
            "unsupported SoC: ertftm070 drives the BCM2835-family GPIO block "
            "(Raspberry Pi Zero/1/2/3/4).  Pi 5 (BCM2712/RP1) and other boards "
            "have incompatible GPIO registers."
        )


def _word_view(buf: Any):
    """Expose ``buf`` as a sequence of 16-bit words for pixel streaming.

    ``array('H')`` buffers and 16-bit memoryviews pass through; any
    1-byte-per-item buffer (``bytes``, ``bytearray``, a ``'B'``
    memoryview) is reinterpreted as little-endian word pairs — low byte
    first, matching the bus's ``bit 0 = DB0`` rule and the C backend's
    byte pairing.  Anything else (wider items, 2-D buffers) is a
    TypeError, and an odd byte count cannot form whole words.
    """
    if isinstance(buf, array) and buf.typecode == "H":
        return buf
    if isinstance(buf, memoryview):
        view = buf
    else:
        view = memoryview(buf)
    if view.format == "H":
        return view
    if view.ndim != 1 or view.itemsize != 1:
        raise TypeError(
            "pixel buffer must be 16-bit words: array('H'), a 16-bit "
            "memoryview, or bytes/bytearray of word pairs"
        )
    if len(view) % 2:
        raise ValueError(
            "pixel buffer length must be even (one 16-bit word per pixel)"
        )
    return view.cast("H")


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

    def pin_read(self, pin: int) -> bool:
        """Sample a pin's current level (GPLEV0).  True = high.

        Used for the SSD1963's TE (tearing effect) output and the touch
        controller's INT line — inputs the host only watches.
        """

    def write_byte(self, value: int) -> None:
        """One register byte on DB0-7 with a WR strobe."""

    def read_word(self) -> int:
        """Sample 16 bits on DB0-15 with an RD strobe.

        The backend flips DB0-15 to inputs for the strobe and restores
        them to outputs afterwards, so a read can never leave the bus in
        input mode (even across an exception).
        """

    def pixel_stream(self, buf: Any) -> None:
        """One WR strobe per 16-bit word (bit 0 = DB0), + 2 trailing dummies.

        ``buf`` must be a contiguous buffer of 16-bit words —
        ``array("H")``, a 16-bit memoryview, or ``bytes``/``bytearray``
        holding word pairs (low byte first).  CS/DC framing is the
        caller's job.
        """

    def row_blit(self, x0: int, x1: int, y: int, buf: Any) -> None:
        """One row of a blit, including CS/DC framing: the 0x2A/0x2B
        window for columns ``x0..x1`` on row ``y`` (controller-space
        coordinates), 0x2C, the pixel burst, and the 2 trailing dummies.

        ``buf`` must hold exactly ``x1 - x0 + 1`` 16-bit words (one per
        window column; the same word buffers :meth:`pixel_stream`
        accepts) — both real backends raise ``ValueError`` otherwise.

        A backend is free to compose this traffic however it likes; a
        custom ``Bus`` subclass passed to ``Display(backend=...)`` must
        implement ``row_blit``.  The backends differ only in CS/DC
        framing — the C extension holds CS low across each command+data
        group, the pure-Python backend pulses CS around every byte — the
        bytes and WR strobes at the pins are the same.  One row per
        call keeps the SSD1963's swallowed-write-word quirk
        (docs/LESSONS.md) contained to a single burst.
        """


def _framed_command(bus: Bus, cmd: int) -> None:
    """One command byte: CS low, DC low (command), byte, CS high."""
    bus.pin_write(bus.pins.cs, False)
    bus.pin_write(bus.pins.dc, False)
    bus.write_byte(cmd)
    bus.pin_write(bus.pins.cs, True)


def _framed_data(bus: Bus, values) -> None:
    """Data bytes for the current command: CS low, DC high, bytes, CS high."""
    bus.pin_write(bus.pins.cs, False)
    bus.pin_write(bus.pins.dc, True)
    for v in values:
        bus.write_byte(v)
    bus.pin_write(bus.pins.cs, True)


def _row_blit_traffic(bus: Bus, x0: int, x1: int, y: int, buf: Any) -> None:
    """The register traffic of one row blit, on ``bus``.

    Emits the 0x2A/0x2B/0x2C window commands, their argument bytes, the
    pixel burst and the 2 trailing dummies with CS/DC framing, exactly
    as the display driver framed rows before ``Bus.row_blit`` existed.
    The slow backend's ``row_blit`` and the tests' fake bus both emit a
    row this way, so their traffic cannot drift apart; the C extension
    strokes the same bytes at the pins (CS held across each group).
    Callers validate coordinates and buffer length first.
    """
    _framed_command(bus, 0x2A)  # column window
    _framed_data(bus, (x0 >> 8, x0 & 0xFF, x1 >> 8, x1 & 0xFF))
    _framed_command(bus, 0x2B)  # row window: a single row
    _framed_data(bus, (y >> 8, y & 0xFF, y >> 8, y & 0xFF))
    _framed_command(bus, 0x2C)  # memory write
    bus.pin_write(bus.pins.cs, False)
    bus.pin_write(bus.pins.dc, True)
    bus.pixel_stream(buf)
    bus.pin_write(bus.pins.cs, True)


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
        "using the slow pure-Python backend (~10 s full screen instead of "
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
    """Wraps ``ertftm070._fastio``.  Thin; all timing lives in C.

    The C module is single-owner: its registers are module state, so a
    second ``open()`` (e.g. a second :class:`~ertftm070.Display` while
    the first is open) raises ``RuntimeError`` instead of silently
    sharing a mapping, and closing one bus can never tear down another's.
    """

    def __init__(self, pins: Pins):
        self.pins = pins
        self._m = None  # type: Optional[Any]

    def _mod(self):
        if self._m is None:
            raise RuntimeError("bus not open — call open() first")
        return self._m

    @staticmethod
    def _wiring(pins: Pins) -> tuple:
        """The 18-int pin tuple the C backend strokes: 8 low + 8 high +
        WR + RD data-bus pins (BCM GPIO numbers)."""
        return tuple(pins.data_low) + tuple(pins.data_high) + (pins.wr, pins.rd)

    def open(self) -> None:
        _check_platform()
        if self._m is not None:
            return  # idempotent, like _MmioBus.open
        _fastio.open(self._wiring(self.pins))  # RuntimeError if another bus owns it
        self._m = _fastio

    def close(self) -> None:
        if self._m is not None:
            self._m.close()
            self._m = None

    def pin_mode(self, pin: int, output: bool) -> None:
        self._mod().pin_mode(pin, bool(output))

    def pin_write(self, pin: int, level: bool) -> None:
        self._mod().pin_write(pin, bool(level))

    def pin_read(self, pin: int) -> bool:
        return bool(self._mod().pin_read(pin))

    def write_byte(self, value: int) -> None:
        self._mod().write_byte(value)

    def read_word(self) -> int:
        return self._mod().read_word()

    def pixel_stream(self, buf: Any) -> None:
        self._mod().pixel_stream(buf)

    def row_blit(self, x0: int, x1: int, y: int, buf: Any) -> None:
        """One row (window + burst + CS/DC framing) in a single C call."""
        self._mod().row_blit(self.pins.cs, self.pins.dc, x0, x1, y, buf)


# ----------------------------------------------------------------------
# Slow backend: pure Python over /dev/gpiomem
# ----------------------------------------------------------------------


class _MmioBus:
    """Pure-Python fallback.  Same protocol, ~17x slower pixel path
    (~10 s vs ~0.6 s for a full-screen fill on a Pi Zero W).

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
        # Pre-packed register words: strobes then need one pack per pixel
        # instead of four (the mmap slice itself is the write).
        pack = struct.pack
        self._clr_low_bytes = pack("<I", self._clr_low)
        self._clr_bytes = pack("<I", self._clr_all)
        self._wr_bytes = pack("<I", self._wr)
        self._rd_bytes = pack("<I", self._rd)

    def open(self) -> None:
        if self._mm is not None:
            return
        _check_platform()
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

    def pin_read(self, pin: int) -> bool:
        return bool(self._read_word_reg(_GPLEV0) & (1 << pin))

    def write_byte(self, value: int) -> None:
        mm = self._check()
        mm[_GPCLR0 : _GPCLR0 + 4] = self._clr_low_bytes
        mm[_GPSET0 : _GPSET0 + 4] = struct.pack("<I", self._set_low[value & 0xFF])
        mm[_GPCLR0 : _GPCLR0 + 4] = self._wr_bytes
        mm[_GPSET0 : _GPSET0 + 4] = self._wr_bytes

    def read_word(self) -> int:
        mm = self._check()
        for pin in self.pins.data:
            self.pin_mode(pin, False)  # data lines to inputs
        mm[_GPCLR0 : _GPCLR0 + 4] = self._rd_bytes  # RD low: sample window
        time.sleep(0.000002)  # let the controller drive the bus
        lev = self._read_word_reg(_GPLEV0)
        mm[_GPSET0 : _GPSET0 + 4] = self._rd_bytes  # RD high: release
        value = 0
        for bit, pin in enumerate(self.pins.data):
            if lev & (1 << pin):
                value |= 1 << bit
            self.pin_mode(pin, True)  # restore: back to outputs
        return value

    def pixel_stream(self, buf: Any) -> None:
        mm = self._check()
        view = _word_view(buf)
        clr_b, wr_b = self._clr_bytes, self._wr_bytes
        set_low, set_high = self._set_low, self._set_high
        pack = struct.pack
        gpset, gpclr = _GPSET0, _GPCLR0
        last = 0
        for v in view:
            last = set_low[v & 0xFF] | set_high[v >> 8]
            mm[gpclr : gpclr + 4] = clr_b
            mm[gpset : gpset + 4] = pack("<I", last)
            mm[gpclr : gpclr + 4] = wr_b
            mm[gpset : gpset + 4] = wr_b
        # 2 trailing dummy pixels — absorb the burst-tail quirk
        for _ in range(2):
            mm[gpclr : gpclr + 4] = clr_b
            mm[gpset : gpset + 4] = pack("<I", last)
            mm[gpclr : gpclr + 4] = wr_b
            mm[gpset : gpset + 4] = wr_b

    def row_blit(self, x0: int, x1: int, y: int, buf: Any) -> None:
        """One row of a blit with CS/DC framing, composed from the same
        primitives the fast backend strokes in C (slower per call, the
        same bytes at the pins)."""
        self._check()
        if x0 > x1:
            raise ValueError("row_blit: x0 must not exceed x1")
        view = _word_view(buf)  # also rejects odd-length byte buffers
        if len(view) != x1 - x0 + 1:
            raise ValueError(
                "row_blit: buffer must hold one 16-bit word per window "
                f"column (x1 - x0 + 1 == {x1 - x0 + 1} words), got {len(view)}"
            )
        _row_blit_traffic(self, x0, x1, y, view)


def _byte_mask(value: int, pins) -> int:
    mask = 0
    for bit, pin in enumerate(pins):
        if value & (1 << bit):
            mask |= 1 << pin
    return mask
