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
import statistics
import time
from array import array
from typing import TYPE_CHECKING

from . import backends
from .colors import fit_image, rgb888_to_565_buffer, rotate_image
from .errors import NotOnRaspberryPi
from .init import INIT_UTFT, Table
from .pins import DEFAULT_PINS, Pins

if TYPE_CHECKING:
    from PIL import Image

log = logging.getLogger("ertftm070")

# Rotation is done in SOFTWARE: the panel keeps its verified native
# orientation (MADCTL = 0x08, landscape + BGR) and images are pre-rotated
# before blitting.  On the SSD1963, 0x36 is not ILI-style MADCTL: bits
# A[7]/A[6] are the host fill-pointer's walk direction (datasheet §9.24),
# which reverses the write order inside the window and scrambles
# partial-window blits — GRAM read-back on hardware showed exactly that
# for MADCTL-based rotations — and 90°/270° hardware rotation does not
# exist on this controller at all.  So we never touch 0x36 after init
# (the community's working pattern too; see docs/COMMUNITY-RESEARCH.md).
# The mappings below are logical→controller; 270° was verified
# byte-perfect against GRAM, the rest follow by composition.
_ROTATIONS = {
    0: {"width": 800, "height": 480},
    90: {"width": 480, "height": 800},
    180: {"width": 800, "height": 480},
    270: {"width": 480, "height": 800},
}

_ROTATION_VALUES = frozenset(_ROTATIONS)

# Shared wording for every TE-timeout: the pin is not pulsing, and the
# two things that cause that are the wire and the panel being driven.
_TE_HINT = (
    "TE is not pulsing — is panel pin 8 wired to the TE GPIO, is the "
    "display awake, and did init enable 0x35? (see docs/WIRING.md)"
)

_warned_slow_vsync = False


def _warn_slow_vsync() -> None:
    """One-time warning: vsync pacing on the pure-Python backend."""
    global _warned_slow_vsync
    if _warned_slow_vsync:
        return
    _warned_slow_vsync = True
    log.warning(
        "vsync= on the pure-Python backend: its ~20 ms rows do not fit "
        "the ~1.6 ms blanking window — updates fall back to one row per "
        "frame, so vsynced blits crawl (~9 s per screen)"
    )


def _map_point(rotation: int, x: int, y: int) -> tuple[int, int]:
    """Map a logical (x, y) to controller coordinates for `rotation`.

    The mapping preserves handedness (one axis flip per 90° step) so the
    image rotates rather than mirrors.  270° verified byte-perfect
    against GRAM read-back on hardware; the others follow by composition.
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


def _ref_ratio(init_table: Table) -> float:
    """kHz of pixel clock per MHz of reference crystal, from the init
    table's own clock chain: PLL = ref × (0xE2 byte0 + 1) / (0xE2 byte1
    + 1) and PCLK = PLL × (0xE6 24-bit FPR + 1) / 2**20
    (docs/COMMUNITY-RESEARCH.md §2).  Raises ValueError if the table
    lacks the 0xE2/0xE6 entries."""
    pll = fpr = None
    for entry in init_table:
        if isinstance(entry, tuple):
            cmd, data = entry
            if cmd == 0xE2 and len(data) >= 2:
                pll = (data[0] + 1) / (data[1] + 1)
            elif cmd == 0xE6 and len(data) >= 3:
                fpr = (((data[0] << 16) | (data[1] << 8) | data[2]) + 1) / (1 << 20)
    if pll is None or fpr is None:
        raise ValueError("init table lacks the 0xE2/0xE6 clock entries")
    return 1000.0 * pll * fpr


def crystal_guess(pclk_khz: int, init_table: Table = INIT_UTFT) -> str:
    """Name the crystal behind a measured pixel clock.

    The clock chain is read out of ``init_table`` itself (see
    :func:`_ref_ratio`): PCLK = ref × (0xE2 + 1)/(0xE2 + 1) ×
    (0xE6 + 1)/2**20, so a measured pixel clock pins down the
    reference — with the default ``INIT_UTFT`` (×31/3, ÷4) that reduces
    to ref_MHz = pclk_kHz × 12 / 31000.  Known crystal options on these
    boards: 6.5, 10 and 12 MHz.

    Raises ValueError if ``init_table`` lacks the 0xE2/0xE6 entries.
    """
    ref_mhz = pclk_khz / _ref_ratio(init_table)
    for candidate in (12.0, 10.0, 6.5):
        if abs(ref_mhz - candidate) <= 0.75:
            return f"{candidate:g} MHz"
    return f"unknown ({ref_mhz:.1f} MHz)"


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
        write_passes: How often to write each pixel (default 1).  The
            SSD1963's GRAM arbitration occasionally swallows a write word
            (see docs/LESSONS.md); 2 passes heal most of those at 2x
            write time — use it for content that must look perfect and
            is drawn rarely.
    """

    def __init__(
        self,
        pins: Pins = DEFAULT_PINS,
        init_table: Table = INIT_UTFT,
        backend: backends.Bus | None = None,
        auto_init: bool = True,
        backlight: bool = True,
        rotation: int = 0,
        write_passes: int = 1,
    ) -> None:
        self.pins = pins
        self.init_table = init_table
        self._backend = backend
        self._auto_init = auto_init
        self._backlight_on = backlight
        if write_passes < 1:
            raise ValueError("write_passes must be >= 1")
        self._write_passes = write_passes
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
        # The bus class, not the BACKEND env label: an injected backend
        # (simulated, a fake in tests) is what actually drives rows.
        log.info("ertftm070 display ready (%dx%d, backend=%s)",
                 self.width, self.height, type(bus).__name__)

    def close(self) -> None:
        """Backlight off and release the bus.  Safe to call repeatedly.

        If a :class:`ertftm070.touch.Touch` shares this bus, close it
        first — after this the GPIO pins are released, and touch pin
        operations (INT, /RST) fail.
        """
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
        if self.pins.te is not None:
            b.pin_mode(self.pins.te, False)  # TE is an input driven by the panel

    def _command(self, cmd: int) -> None:
        """One command cycle: CS low, DC low (command), byte, CS high.

        Framing lives in one place, :func:`backends._framed_command`, so
        every bus user (init, windows, blits) strokes CS/DC alike and the
        traffic recorded by the tests' fake bus matches the real ones.
        """
        backends._framed_command(self._bus_checked(), cmd)

    def _data(self, value: int) -> None:
        """One data cycle: CS low, DC high (data), byte, CS high."""
        backends._framed_data(self._bus_checked(), (value,))

    def _data_list(self, values) -> None:
        """Several data bytes with CS held low (used by init sequences)."""
        backends._framed_data(self._bus_checked(), values)

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
        # Tearing effect on: 0x35 = 0x00 pulses TE during V-blanking.
        # Like 0x3A, no community init table sets it — it is a
        # driver-level addition, and the source of vsync and the
        # refresh-rate measurement.  Skipped when Pins.te is None: the
        # panel then pulses no TE line, and vsync_wait/refresh_rate
        # raise instead of timing out.  See docs/COMMUNITY-RESEARCH.md §5.
        if self.pins.te is not None:
            self._command(0x35)
            self._data(0x00)

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
        chip's burst-tail quirk.  Use :meth:`_blit_rows` for anything
        multi-row: a swallowed write word mid-burst shifts everything
        after it, so large areas are written one row per burst.
        """
        b = self._bus_checked()
        self._command(0x2C)  # memory write
        b.pin_write(self.pins.cs, False)
        b.pin_write(self.pins.dc, True)
        b.pixel_stream(buf)
        b.pin_write(self.pins.cs, True)

    def _blit_rows(
        self, buf, x0: int, y0: int, x1: int, y1: int, vsync: bool = False
    ) -> None:
        """Write a row-major buffer to a controller-space window, one row
        per burst.

        The SSD1963's GRAM arbitration occasionally swallows one write
        word mid-burst (see docs/LESSONS.md); in one long burst that
        shifts everything after the drop.  Per-row bursts contain any
        drop to a single row, and each row's burst-tail loss is absorbed
        by its own 2 trailing dummy pixels.  With ``write_passes`` > 1
        every row is written again, healing most swallowed words (a word
        must be swallowed in *every* pass to stay wrong).

        Each row is one ``Bus.row_blit`` call — window commands, burst
        and CS/DC framing in a single backend call (a single C call on
        the fast backend), instead of the ~27 per-row calls that
        dominated small-blit latency.

        ``buf`` must hold exactly ``(x1 - x0 + 1) * (y1 - y0 + 1)``
        16-bit RGB565 words, row-major in window order: ``array('H')``,
        a 16-bit memoryview, or bytes/bytearray of word pairs (the
        pixel buffers the backends accept).  Anything else — wrong
        length, odd byte count, wider items — raises here, before any
        register traffic is emitted.

        With ``vsync``, each row burst first waits for a *fresh*
        blanking window (TE falls, then rises — see
        :meth:`_wait_vsync_blanking`) and is issued at its start, so a
        row never lands mid-scan or straddles a window boundary —
        tear-free updates.  Measured on this panel: a row burst takes
        ~1.3 ms and blanking lasts ~1.6 ms of the ~18.6 ms frame, so a
        full-screen vsynced blit costs ~480 × 18.6 ms ≈ 9 s — opt-in
        for narrow, fast-moving content, not whole screens.  The
        pure-Python backend's ~20 ms rows do not fit one blanking
        window at all; it degrades to one row per frame (a warning is
        logged once).
        """
        width = x1 - x0 + 1
        row_count = y1 - y0 + 1
        words = backends._word_view(buf)
        if isinstance(words, array):
            words = memoryview(words)  # row slices below are zero-copy
        if len(words) != width * row_count:
            raise ValueError(
                "blit buffer must hold exactly one 16-bit word per pixel "
                f"of the {width}x{row_count} window "
                f"({width * row_count} words), got {len(words)}"
            )
        # Row slices are the same objects on every pass.
        rows = [words[row * width : (row + 1) * width] for row in range(row_count)]
        b = self._bus_checked()
        # The crawl warning belongs to the bus actually driving the rows,
        # not to the BACKEND env label: the simulated backend's ~1.3 ms
        # paced rows fit the blanking window like the C backend's, and an
        # explicitly injected slow bus must warn even when the env label
        # says otherwise (e.g. ERTFTM070_DISPLAY=sim with an _MmioBus).
        if vsync and isinstance(b, backends._MmioBus):
            _warn_slow_vsync()
        for _pass in range(self._write_passes):
            for yy, row in zip(range(y0, y1 + 1), rows):
                if vsync:
                    self._wait_vsync_blanking()
                b.row_blit(x0, x1, yy, row)

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

    def fill_rect(
        self, x: int, y: int, w: int, h: int, color: int, vsync: bool = False
    ) -> None:
        """Fill a rectangle with an RGB565 color.

        Written one row per burst (see :meth:`_blit_rows`) so a swallowed
        write word can never shift more than one row.  The fast way to do
        partial updates — redraw only what changed.

        ``vsync=True`` paces each row into vertical blanking (tear-free;
        see :meth:`_blit_rows` for the cost) — for narrow moving content.
        """
        self._check_bounds(x, y, w, h)
        cx0, cy0, cx1, cy1 = _map_rect(self._rotation, x, y, x + w - 1, y + h - 1)
        self._blit_rows(
            array("H", [color & 0xFFFF]) * (w * h), cx0, cy0, cx1, cy1, vsync=vsync
        )

    def set_pixel(self, x: int, y: int, color: int) -> None:
        """Set a single pixel (window + one-word stream).

        Written through :meth:`_blit_rows` — a single row burst with the
        trailing dummies, and ``write_passes`` honored — so a swallowed
        write word heals exactly like any other draw call.

        Fine for sparse updates; use :meth:`fill_rect` or :meth:`image`
        for anything dense.
        """
        self._check_bounds(x, y, 1, 1)
        cx0, cy0, cx1, cy1 = _map_rect(self._rotation, x, y, x, y)
        self._blit_rows(array("H", [color & 0xFFFF]), cx0, cy0, cx1, cy1)

    def image(
        self, img: Image.Image, x: int = 0, y: int = 0, fit: bool = False,
        vsync: bool = False,
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
            vsync: Pace each row into vertical blanking (tear-free; see
                :meth:`_blit_rows` for the cost).

        Requires the ``pillow`` extra: ``pip install ertftm070[Pillow]``.
        """
        # Origin outside the screen must fail cleanly BEFORE fit computes a
        # box from (width - x): a box of 0 or negative width would surface
        # as a raw PIL thumbnail() ZeroDivisionError/ValueError instead.
        if x < 0 or y < 0 or x >= self.width or y >= self.height:
            raise ValueError(
                f"origin ({x},{y}) outside the {self.width}x{self.height} display"
            )
        if fit:
            img = fit_image(img, self.width - x, self.height - y)
        w, h = img.size
        self._check_bounds(x, y, w, h)  # logical coordinates
        img = rotate_image(img, self._rotation)
        buf = rgb888_to_565_buffer(img)
        cx0, cy0, cx1, cy1 = _map_rect(self._rotation, x, y, x + w - 1, y + h - 1)
        self._blit_rows(buf, cx0, cy0, cx1, cy1, vsync=vsync)

    # ------------------------------------------------------------------
    # Display state
    # ------------------------------------------------------------------

    @property
    def bus(self) -> backends.Bus | None:
        """The opened bus, for :class:`ertftm070.touch.Touch` (it shares
        the display's GPIO access — the fast backend is single-owner);
        ``None`` until :meth:`open`."""
        return self._bus

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

    def unmap_point(self, x: int, y: int) -> tuple[int, int]:
        """Inverse of the rotation mapping: panel-native coordinates →
        the current logical frame (the space the draw calls use).

        Panel-native is the frame the panel draws at ``rotation=0`` —
        always 800 wide × 480 high, whatever the current rotation —
        which is exactly what :meth:`ertftm070.touch.Touch.read` returns
        with ``mapped=True`` (see the touch module docs).  Identity at
        ``rotation=0``; touch users on a rotated display:

            x, y = lcd.unmap_point(point.x, point.y)
        """
        if self._rotation == 90:
            return (479 - y, x)
        if self._rotation == 180:
            return (799 - x, 479 - y)
        if self._rotation == 270:
            return (y, 799 - x)
        return (x, y)

    def backlight(self, on: bool) -> None:
        """Switch the backlight on (True) or off (False)."""
        b = self._bus_checked()
        b.pin_write(self.pins.backlight, bool(on))

    def sleep(self) -> None:
        """Display off + enter sleep (minimal power).

        The panel's liquid crystal discharges unevenly when the drive is
        cut, so the last picture visibly fades out toward the center —
        normal TFT physics, not a bug.  Fill the screen black first
        (``lcd.fill(0x0000)``) for an invisible power-down.

        Note: with our init (``0xB8 = 07 01``) GPIO0 is a plain host
        output, so ``0x10`` does not toggle the panel enable (datasheet
        §9.8) — the power saving here is ``0x28`` plus the backlight
        GPIO.  See docs/COMMUNITY-RESEARCH.md §5.
        """
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
        """Read `count` 16-bit words from the controller (DC high).

        The backend flips DB0-15 to inputs for each word's RD strobe and
        restores them to outputs right after sampling (see the Bus
        protocol), so no direction management belongs here — a whole-read
        flip would also leave the bus wedged in input mode if anything
        raised mid-read.

        Reliable for small counts (gramcheck-style windows).  Panel-scale
        reads drop ~1 word per 400 — the SSD1963's read pointer does not
        stride rows like the write path — so don't trust long reads as
        ground truth.  See docs/LESSONS.md.
        """
        b = self._bus_checked()
        b.pin_write(self.pins.cs, False)
        b.pin_write(self.pins.dc, True)
        out = [b.read_word() for _ in range(count)]
        b.pin_write(self.pins.cs, True)
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

    # ------------------------------------------------------------------
    # Timing: register read-back, TE vsync, refresh measurement
    # ------------------------------------------------------------------

    def read_register(self, reg: int, n: int) -> list[int]:
        """Read ``n`` bytes from a register (``0xE7``, ``0xB9``, ...).

        Register reads always arrive on D[7:0] regardless of bus width —
        the low byte of each 16-bit word, masked off exactly like
        :meth:`selftest` does.  Keep ``n`` small: like every read on
        this chip, long reads drop words (see :meth:`_read_words`).
        """
        self._command(reg)
        return [w & 0xFF for w in self._read_words(n)]

    def _te_pin(self) -> int:
        te = self.pins.te
        if te is None:
            raise RuntimeError(
                "no TE pin configured (Pins.te is None) — TE-based timing "
                "needs panel pin 8 wired to a GPIO (docs/WIRING.md)"
            )
        return te

    def _wait_te_level(self, level: bool, deadline: float, hint: str) -> None:
        """Poll the TE pin until it reads `level`, or raise TimeoutError
        (with `hint`) once `deadline` passes.

        Polling at ~200 µs samples the ~1.6 ms blanking window ~8 times,
        so an edge is caught within a fraction of the window — the poll
        period of the old vsync loop (~1 ms) could miss half of it.
        """
        b = self._bus_checked()
        te = self._te_pin()
        while b.pin_read(te) != level:
            if time.monotonic() > deadline:
                raise TimeoutError(hint)
            time.sleep(0.0002)

    def vsync_wait(self, timeout: float = 0.1) -> None:
        """Block until TE goes high — the start of the vertical blanking
        window (init sets ``0x35 = 0x00``).  The raw edge wait; the draw
        calls gate on a *fresh* window instead (see
        :meth:`_wait_vsync_blanking`), so use this to pace your own
        drawing::

            lcd.vsync_wait()   # next blanking window starts
            lcd.fill_rect(...)

        Raises ``TimeoutError`` when no TE pulse arrives — check the
        pin-8 → GPIO14 wire (docs/WIRING.md) — and ``RuntimeError``
        when ``Pins.te`` is ``None``.
        """
        deadline = time.monotonic() + timeout
        self._wait_te_level(True, deadline, _TE_HINT)

    def _wait_vsync_blanking(self, timeout: float = 0.1) -> None:
        """Wait for the *start* of a fresh blanking window: TE low, then
        high.

        Rows issued after this land at the window's start with the whole
        ~1.6 ms ahead of them, instead of mid-window (a row that starts
        in blanking but ends after it tears).  A TE line stuck high
        (shorted wire, panel asleep) times out here rather than letting
        every row pretend it is blanking.
        """
        deadline = time.monotonic() + timeout
        self._wait_te_level(False, deadline, _TE_HINT)
        self._wait_te_level(True, deadline, _TE_HINT)

    def _te_period_seconds(self, samples: int = 5, timeout: float = 2.0) -> float:
        """Median TE period (one frame) across ``samples`` rising edges.

        The median shrugs off a single dropped TE pulse, which would
        skew a mean to ~2× the period; the ``samples`` edges yield
        ``samples - 1`` period samples, so ``samples`` must be ≥ 2.
        """
        if samples < 2:
            raise ValueError("samples must be >= 2 (one period needs two edges)")
        self._bus_checked()
        self._te_pin()  # fail fast before burning the deadline on a pin-less wait
        deadline = time.monotonic() + timeout
        self._wait_te_level(False, deadline, _TE_HINT)  # start from a known phase
        self._wait_te_level(True, deadline, _TE_HINT)  # first rising edge
        edges = [time.monotonic()]
        for _ in range(samples - 1):
            self._wait_te_level(False, deadline, _TE_HINT)
            self._wait_te_level(True, deadline, _TE_HINT)
            edges.append(time.monotonic())
        gaps = [edges[i + 1] - edges[i] for i in range(samples - 1)]
        return statistics.median(gaps)

    def _scan_totals(self) -> tuple[int, int]:
        """``(ht_total, vt_total)`` from the 0xB4/0xB6 entries of the
        active init table — the datasheet stores total-minus-one in
        HT[10:0]/VT[10:0] (bytes 1-2 of each command)."""
        ht = vt = None
        for entry in self.init_table:
            if isinstance(entry, tuple):
                cmd, data = entry
                if cmd == 0xB4 and len(data) >= 2:
                    ht = (((data[0] & 0x07) << 8) | data[1]) + 1
                elif cmd == 0xB6 and len(data) >= 2:
                    vt = (((data[0] & 0x07) << 8) | data[1]) + 1
        if ht is None or vt is None:
            raise RuntimeError("init table lacks 0xB4/0xB6 scan timing")
        return ht, vt

    def refresh_rate(self, samples: int = 5, timeout: float = 2.0) -> tuple[int, float]:
        """``(pclk_khz, hz)`` — the panel's actual clocks, measured.

        Both derive from the TE period, the ground truth for refresh:
        ``hz`` directly, and PCLK from the frame rate and the scan
        totals of the active init table (PCLK = hz × HT × VT).  The
        ``0xE7`` register was tried first — on hardware it returns the
        FPR value (``0x03FFFF``), not a frequency, so it is not used.
        Pair with :func:`crystal_guess` to settle which crystal the
        board carries — see docs/COMMUNITY-RESEARCH.md §2 for why that
        was ever in doubt.
        """
        ht, vt = self._scan_totals()
        hz = 1.0 / self._te_period_seconds(samples, timeout)
        pclk_khz = int(hz * ht * vt / 1000.0)
        return pclk_khz, hz
