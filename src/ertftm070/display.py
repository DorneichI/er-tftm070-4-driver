"""Display — the public driver class.

Usage::

    from ertftm070 import Display

    with Display() as lcd:            # opens the bus, resets + inits
        lcd.fill(0xF800)             # red screen in ~0.6 s (fast backend)
        lcd.fill_rect(10, 10, 100, 50, 0x07E0)
        lcd.image(pil_image, x=20, y=20)
        lcd.rotation = 90
        lcd.backlight(False)
        lcd.sleep()
        lcd.wake()

The default configuration (``DEFAULT_PINS`` + ``INIT_UTFT``) is the one
verified on hardware: 16-bit 8080, one WR strobe per pixel, landscape
with BGR order.  See ``docs/WIRING.md`` and ``docs/INIT-SEQUENCE.md``.

Everything that needs Pillow imports it lazily inside :meth:`image`,
so the core package has zero runtime dependencies.
"""
from __future__ import annotations

import logging
import time
from array import array
from typing import TYPE_CHECKING

from . import backends
from .colors import fit_image, rgb888_to_565_buffer
from .errors import NotOnRaspberryPi
from .init import INIT_UTFT, Table
from .pins import DEFAULT_PINS, Pins

if TYPE_CHECKING:
    from PIL import Image

log = logging.getLogger("ertftm070")

# SSD1963 0x36 (address mode) values per rotation.  Every state keeps the
# BGR bit (0x08) set for this panel.  Axis directions (which way 90° turns,
# and where the flips apply) are verified on hardware — see the README.
_ROTATIONS = {
    0: {"mad": 0x08, "width": 800, "height": 480},
    90: {"mad": 0x48, "width": 480, "height": 800},  # MV | BGR
    180: {"mad": 0x38, "width": 800, "height": 480},  # MY | MX | BGR
    270: {"mad": 0x78, "width": 480, "height": 800},  # MV | MY | MX | BGR
}

_ROTATION_VALUES = frozenset(_ROTATIONS)


def _map_point(rotation: int, x: int, y: int) -> tuple[int, int]:
    """Map a logical (x, y) to controller coordinates for `rotation`.

    The mapping preserves handedness (one axis flip per 90° step) so the
    image rotates rather than mirrors.  Exact sign conventions verified
    on hardware.
    """
    if rotation == 90:
        return (y, 479 - x)
    if rotation == 180:
        return (799 - x, 479 - y)
    if rotation == 270:
        return (799 - y, x)
    return (x, y)


def _map_rect(
    rotation: int, x0: int, y0: int, x1: int, y1: int
) -> tuple[int, int, int, int]:
    """Map a logical rectangle to a controller window (min/max corners)."""
    points = [
        _map_point(rotation, x0, y0),
        _map_point(rotation, x1, y0),
        _map_point(rotation, x0, y1),
        _map_point(rotation, x1, y1),
    ]
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


class Display:
    """Driver for the ER-TFTM070-4V2.1 (7" 800x480 TFT, SSD1963).

    A context manager: ``with Display() as lcd:`` opens the bus, applies
    the verified init sequence, and turns the backlight on; exiting (or
    Ctrl-C) turns the backlight off and releases the GPIO mapping.

    Args:
        pins: Wiring.  Defaults to the hardware-verified ``DEFAULT_PINS``.
        init_table: Init sequence.  Defaults to the verified ``INIT_UTFT``;
            ``INIT_ALT``/``INIT_BD`` exist for tinkering.
        backend: A ``ertftm070.backends.Bus`` instance, or ``None`` to
            auto-select (C extension if built, else pure-Python fallback).
        auto_init: If False, skip reset+init on open (advanced).
        backlight: Whether to switch the backlight on after init.
        rotation: 0, 90, 180 or 270 — logical orientation; ``width`` and
            ``height`` follow it.
    """

    def __init__(
        self,
        pins: Pins = DEFAULT_PINS,
        init_table: Table = INIT_UTFT,
        backend: backends.Bus | None = None,
        auto_init: bool = True,
        backlight: bool = True,
        rotation: int = 0,
    ) -> None:
        self.pins = pins
        self.init_table = init_table
        self._backend = backend
        self._auto_init = auto_init
        self._backlight_on = backlight
        self._bus = None  # type: Optional[backends.Bus]
        self._opened = False
        self._rotation = 0
        self.width = 800
        self.height = 480
        self.rotation = rotation  # validates and sets width/height

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def open(self) -> None:
        """Open the bus and bring the display up.  Idempotent."""
        if self._opened:
            return
        if self._backend is not None:
            bus = self._backend
        else:
            bus = backends.get_backend(self.pins)
        try:
            bus.open()
        except OSError as exc:
            raise NotOnRaspberryPi(
                "no usable /dev/gpiomem — is this a Raspberry Pi Zero/1/2/3/4? "
                "(Pi 5's RP1 GPIO controller is not supported yet.) "
                f"Original error: {exc}"
            ) from exc
        self._bus = bus
        try:
            self._configure_pins()
            if self._auto_init:
                self.reset()
                self._init()
            self._apply_rotation()
            self.backlight(self._backlight_on)
        except BaseException:
            # leave no half-configured bus behind
            try:
                bus.close()
            except Exception:
                pass
            self._bus = None
            raise
        self._opened = True
        log.info("ertftm070 display ready (%dx%d, backend=%s)",
                 self.width, self.height, backends.BACKEND)

    def close(self) -> None:
        """Backlight off and release the bus.  Safe to call repeatedly."""
        if self._bus is not None:
            try:
                self.backlight(False)
            except Exception:
                pass
            self._bus.close()
            self._bus = None
        self._opened = False

    def __enter__(self) -> Display:
        self.open()
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def __del__(self) -> None:  # best effort; context manager is the way
        try:
            self.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Register-level plumbing (16-bit 8080, one WR strobe per byte here —
    # register access always uses DB0-7 regardless of bus width)
    # ------------------------------------------------------------------

    def _bus_checked(self) -> backends.Bus:
        if self._bus is None:
            raise RuntimeError(
                "display not open — call open() or use the context manager"
            )
        return self._bus

    def _configure_pins(self) -> None:
        b = self._bus_checked()
        for pin in self.pins.data:
            b.pin_mode(pin, True)
            b.pin_write(pin, False)
        control = (
            self.pins.cs, self.pins.dc, self.pins.wr, self.pins.rd, self.pins.reset
        )
        for pin in control:
            b.pin_mode(pin, True)
            b.pin_write(pin, True)  # idle state: CS/DC/WR/RD/RESET high
        b.pin_mode(self.pins.backlight, True)
        b.pin_write(self.pins.backlight, False)

    def _command(self, cmd: int) -> None:
        b = self._bus_checked()
        b.pin_write(self.pins.cs, False)
        b.pin_write(self.pins.dc, False)  # command cycle
        b.write_byte(cmd)
        b.pin_write(self.pins.cs, True)

    def _data(self, value: int) -> None:
        b = self._bus_checked()
        b.pin_write(self.pins.cs, False)
        b.pin_write(self.pins.dc, True)  # data cycle
        b.write_byte(value)
        b.pin_write(self.pins.cs, True)

    def _data_list(self, values) -> None:
        """Several data bytes with CS held low (used by init sequences)."""
        b = self._bus_checked()
        b.pin_write(self.pins.cs, False)
        b.pin_write(self.pins.dc, True)
        for value in values:
            b.write_byte(value)
        b.pin_write(self.pins.cs, True)

    def reset(self) -> None:
        """Hardware reset: RESET low 100 ms, then high, wait 200 ms."""
        b = self._bus_checked()
        log.info("hardware reset")
        b.pin_write(self.pins.reset, False)
        time.sleep(0.1)
        b.pin_write(self.pins.reset, True)
        time.sleep(0.2)

    def _init(self) -> None:
        log.info("initializing SSD1963")
        for entry in self.init_table:
            if isinstance(entry, tuple):
                cmd, data = entry
                self._command(cmd)
                if data:
                    self._data_list(data)
            else:
                time.sleep(entry)  # delay entry
        # 16 bits per pixel — AFTER display-on, matching the verified
        # driver (the POR value of 0x3A is "Reserved" and misbehaves on
        # some chips).  See docs/INIT-SEQUENCE.md.
        self._command(0x3A)
        self._data(0x50)

    # ------------------------------------------------------------------
    # Windows and pixel streams
    # ------------------------------------------------------------------

    def _set_window(self, x0: int, y0: int, x1: int, y1: int) -> None:
        """Set the GRAM window from logical coordinates (rotation-aware)."""
        cx0, cy0, cx1, cy1 = _map_rect(self._rotation, x0, y0, x1, y1)
        self._command(0x2A)  # column address
        self._data_list([(cx0 >> 8) & 0xFF, cx0 & 0xFF, (cx1 >> 8) & 0xFF, cx1 & 0xFF])
        self._command(0x2B)  # page (row) address
        self._data_list([(cy0 >> 8) & 0xFF, cy0 & 0xFF, (cy1 >> 8) & 0xFF, cy1 & 0xFF])

    def _blit(self, buf) -> None:
        """Stream a buffer of 16-bit RGB565 words into the current window.

        CS is held low for the whole stream (one continuous burst); the
        backend appends the 2 trailing dummy pixels that absorb this
        chip's burst-tail quirk.
        """
        b = self._bus_checked()
        self._command(0x2C)  # memory write
        b.pin_write(self.pins.cs, False)
        b.pin_write(self.pins.dc, True)
        b.pixel_stream(buf)
        b.pin_write(self.pins.cs, True)

    def _check_bounds(self, x: int, y: int, w: int, h: int) -> None:
        if w <= 0 or h <= 0:
            raise ValueError("width and height must be positive")
        if x < 0 or y < 0 or x + w > self.width or y + h > self.height:
            raise ValueError(
                f"rectangle ({x},{y} {w}x{h}) outside the "
                f"{self.width}x{self.height} display"
            )

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------

    def fill(self, color: int) -> None:
        """Fill the whole screen with an RGB565 color (0..65535)."""
        self.fill_rect(0, 0, self.width, self.height, color)

    def fill_rect(self, x: int, y: int, w: int, h: int, color: int) -> None:
        """Fill a rectangle with an RGB565 color.

        One window + one continuous burst, so this is the fast way to do
        partial updates — redrawing only what changed.
        """
        self._check_bounds(x, y, w, h)
        self._set_window(x, y, x + w - 1, y + h - 1)
        self._blit(array("H", [color & 0xFFFF]) * (w * h))

    def set_pixel(self, x: int, y: int, color: int) -> None:
        """Set a single pixel (window + one-word stream).

        Fine for sparse updates; use :meth:`fill_rect` or :meth:`image`
        for anything dense.
        """
        self._check_bounds(x, y, 1, 1)
        self._set_window(x, y, x, y)
        self._blit(array("H", [color & 0xFFFF]))

    def image(
        self, img: Image.Image, x: int = 0, y: int = 0, fit: bool = False
    ) -> None:
        """Blit a Pillow image at the given top-left corner.

        The image is converted to RGB and packed to RGB565 rows; anything
        Pillow can open works (PNG, JPEG, GIF, …).  Draw text, shapes,
        charts or a whole UI into a PIL image first and blit it here —
        that's the intended pattern.

        Args:
            img: The Pillow image to show.
            x, y: Top-left corner in logical coordinates.
            fit: If True, scale the image down (aspect ratio preserved)
                to fit inside the current logical screen.  Useful after
                a rotation, which swaps ``width``/``height``: an 800x480
                image no longer fits a 480x800 screen.

        Requires the ``pillow`` extra: ``pip install ertftm070[Pillow]``.
        """
        if fit:
            img = fit_image(img, self.width - x, self.height - y)
        buf = rgb888_to_565_buffer(img)
        w, h = img.size
        self._check_bounds(x, y, w, h)
        self._set_window(x, y, x + w - 1, y + h - 1)
        self._blit(buf)

    # ------------------------------------------------------------------
    # Display state
    # ------------------------------------------------------------------

    @property
    def rotation(self) -> int:
        """Current rotation: 0, 90, 180 or 270 (logical orientation)."""
        return self._rotation

    @rotation.setter
    def rotation(self, degrees: int) -> None:
        if degrees not in _ROTATION_VALUES:
            raise ValueError("rotation must be one of 0, 90, 180, 270")
        self._rotation = degrees
        self.width = _ROTATIONS[degrees]["width"]
        self.height = _ROTATIONS[degrees]["height"]
        if self._bus is not None:
            self._apply_rotation()

    def _apply_rotation(self) -> None:
        mad = _ROTATIONS[self._rotation]["mad"]
        self._command(0x36)
        self._data(mad)

    def backlight(self, on: bool) -> None:
        """Switch the backlight on (True) or off (False)."""
        b = self._bus_checked()
        b.pin_write(self.pins.backlight, bool(on))

    def sleep(self) -> None:
        """Display off + enter sleep (minimal power)."""
        self._command(0x28)
        self._command(0x10)

    def wake(self) -> None:
        """Exit sleep and turn the display back on."""
        self._command(0x11)
        time.sleep(0.1)
        self._command(0x29)
        time.sleep(0.1)

    # ------------------------------------------------------------------
    # Diagnostics (ported from the verified legacy/display_test.py)
    # ------------------------------------------------------------------

    def _read_words(self, count: int):
        """Read `count` 16-bit words from the controller (DC high)."""
        b = self._bus_checked()
        for pin in self.pins.data:
            b.pin_mode(pin, False)
        b.pin_write(self.pins.cs, False)
        b.pin_write(self.pins.dc, True)
        out = [b.read_word() for _ in range(count)]
        b.pin_write(self.pins.cs, True)
        for pin in self.pins.data:
            b.pin_mode(pin, True)
        return out

    def selftest(self) -> bool:
        """Verify the bus against the SSD1963 itself.  Returns True if OK.

        Two tests: the Device Descriptor Block read (``0xA1`` must answer
        ``01 57 61 01 FF`` — Solomon Systech, SSD1963), and a 0xB8/0xB9
        register round-trip.  These work right after a hardware reset,
        before any init, and pass regardless of bus width — they prove
        CS/DC/WR/RD and DB0-7, not the 16-bit pixel path (use
        :meth:`gramcheck` for that).
        """
        self._command(0xA1)
        ddb = [w & 0xFF for w in self._read_words(6)]
        expected = [0x01, 0x57, 0x61, 0x01, 0xFF]
        ok = ddb[:5] == expected or ddb[1:6] == expected
        log.info("DDB read-back: %s", [f"0x{b:02X}" for b in ddb])
        if not ok:
            log.error("DDB mismatch: expected %s", [f"0x{b:02X}" for b in expected])

        self._command(0xB8)
        self._data_list([0x0F, 0x01])
        self._command(0xB9)
        got = [w & 0xFF for w in self._read_words(3)]
        log.info("0xB8/0xB9 round-trip: %s", [f"0x{b:02X}" for b in got])
        roundtrip = got[:2] == [0x0F, 0x01] or got[1:3] == [0x0F, 0x01]
        if not roundtrip:
            log.error("0xB8/0xB9 round-trip failed")

        ok = ok and roundtrip
        log.info("self-test %s", "PASSED" if ok else "FAILED")
        return ok

    def gramcheck(self) -> bool:
        """Write 8 known pixels, read them back from GRAM, compare.

        This proves the *16-bit pixel path* — window, stream, byte order,
        addressing — independent of the panel.  Write code that passes
        the self-test but shows garbage pixels fails here.  Returns True
        if GRAM holds exactly what was written.
        """
        pixels = [0x0000, 0xF800, 0x07E0, 0x001F, 0xFFFF, 0xFFE0, 0x07FF, 0xF81F]
        #          black   red    green  blue   white  yellow cyan   magenta
        self._set_window(0, 0, 7, 0)
        self._blit(array("H", pixels))
        time.sleep(0.05)

        self._set_window(0, 0, 7, 0)
        self._command(0x2E)  # memory read
        got = self._read_words(10)
        log.info("gramcheck read-back: %s", [f"0x{w:04X}" for w in got])
        # The first read after 0x2E returns real data on this chip; still
        # tolerate one leading dummy, and the burst may run 1-2 words short.
        match = got[:8] == pixels or got[1:9] == pixels
        log.info("gramcheck %s", "PASSED" if match else "FAILED")
        return match
