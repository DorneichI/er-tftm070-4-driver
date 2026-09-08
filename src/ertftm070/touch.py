"""FT5x06-family capacitive touch driver (FT5206 on V2.1, FT5316 on V3).

Everything here follows docs/COMMUNITY-RESEARCH.md §6 — the register
map shared by every FT5x06 driver in the wild (Adafruit, sumotoy's
EastRising-lineage lib, Linux ``edt-ft5x06``, PyFTtxx6).  Register
values are facts; no code was copied from any library.

The chip needs almost no init: wait ≥300 ms after power-on/reset, do
one dummy read to flush first-access garbage, optionally write
``0x00 = 0`` (working mode).  **Never gate on the chip-ID registers** —
their contents vary across firmwares.

Wiring (display-connector pins, see docs/WIRING.md):

* SCL (34) → Pi phys 5 (I2C-1 SCL), SDA (35) → phys 3
* WAKE (37) → 3.3 V, tied high — a hibernating chip answers at ghost
  addresses instead of 0x38
* INT (36) → GPIO15 (optional; polling TD_STATUS works without it.
  Polarity is firmware-dependent — measured on this panel: idle LOW,
  HIGH during touches — so the driver treats any change as an event)
* /RST (33) → GPIO0 (optional; the board has an on-board RC reset)

Usage::

    from ertftm070 import Display
    from ertftm070.touch import Touch

    with Display() as lcd, Touch(lcd.bus) as touch:
        for point in touch.read(mapped=True):
            x = min(point.x, lcd.width - 4)   # mapped points are panel-
            y = min(point.y, lcd.height - 4)  # native: clamp like any draw
            lcd.fill_rect(x, y, 4, 4, 0xFFFF)

``mapped=True`` coordinates live in the panel-native frame — the frame
the display draws in at ``rotation=0``.  On a rotated display, pass
them through ``Display.unmap_point`` first (see there).

``Touch`` shares the :class:`~ertftm070.Display`'s bus for its GPIO
pins — the fast backend is single-owner, so a second GPIO mapping
cannot exist anyway.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from ._i2c import I2C
from .backends import Bus
from .pins import GPIO_MAX

log = logging.getLogger("ertftm070")

FT5X06_ADDR = 0x38

# Coordinates at or above this are the chip family's "unpressed" /
# unplaceable full-scale marker (0x0FFF), never a real position on any
# FT5x06 panel regardless of its native span — see docs/COMMUNITY-
# RESEARCH.md §6 and the kernel's "bogus coordinates in TOUCH_DOWN".
_JUNK_COORD = 0x0FF0

TD_STATUS = 0x02
_POINT_BASES = (0x03, 0x09, 0x0F, 0x15, 0x1B)
_MAX_POINTS = 5

# Event flags (XH bits 7:6).  Only 00 (press) and 10 (contact/move)
# carry real positions; 01 (release) and 11 (reserved) are skipped —
# the same filter the kernel and ESPHome apply.
EVENT_DOWN = 0
EVENT_UP = 1
EVENT_CONTACT = 2

# The FT5x06 can power on in a "phantom" state: TD_STATUS claims touches
# (0x35 = five) whose records carry impossible finger ids (> 4) and frozen
# garbage coordinates.  It is not cleared by a write-0 to 0x02 or a /RST
# pulse and self-recovers only after minutes — so open()/reset() wait out
# the worst of it (see _wait_sane), warn once per episode, and carry on.
# read()/wait_touch() also drop the phantom's impossible-id records, so a
# phantom that outlives the wait never surfaces as touches either.
_SANITY_TIMEOUT = 5.0
_SANITY_POLL = 0.25

_warned_phantom = False


def _warn_phantom() -> None:
    """Warn once per phantom episode: the chip's phantom state did not
    clear within the wait budget.  Re-armed by ``_mark_phantom_clear``
    the next time the chip is seen sane, so a fresh episode (a later
    power-on, a second panel) warns again while one persistent episode
    stays silent after its first warning."""
    global _warned_phantom
    if _warned_phantom:
        return
    _warned_phantom = True
    log.warning(
        "FT5x06 phantom touch state did not clear within %.1f s — "
        "proceeding; TD_STATUS may keep claiming touches with impossible "
        "finger ids until the chip recovers on its own (minutes)",
        _SANITY_TIMEOUT,
    )


def _mark_phantom_clear() -> None:
    """The chip has been seen sane: re-arm the warn-once latch so the
    next phantom episode is diagnosed again."""
    global _warned_phantom
    _warned_phantom = False


@dataclass(frozen=True)
class TouchPoint:
    """One touch.  ``x``/``y`` are 12-bit raw panel coordinates whose
    span is set by the chip's per-panel firmware — on this panel it
    measures 0..799 × 0..479 (2026-09-06), not the 0..4095 the 12-bit
    format nominally allows.  Map with :class:`TouchCalibration`."""

    x: int
    y: int
    id: int
    event: int


def decode_status(byte: int) -> int:
    """The number of touches in a TD_STATUS byte.

    The count is the low nibble; values above the five the FT5x06
    register map can hold mean the chip returned junk — treat as none.
    """
    count = byte & 0x0F
    return count if count <= _MAX_POINTS else 0


def decode_points(records: bytes, count: int) -> list[TouchPoint]:
    """Parse ``count`` 6-byte touch records as read from address 0x03.

    Record layout: XH (bits 7:6 event, bits 3:0 X[11:8]), XL (X[7:0]),
    YH (bits 7:4 finger id, bits 3:0 Y[11:8]), YL (Y[7:0]), then two
    weight bytes every driver ignores.  Records whose event is not a
    press/contact are dropped (a release carries no position).
    """
    points = []
    for i in range(count):
        base = i * 6
        if base + 6 > len(records):
            break
        xh, xl, yh, yl = records[base : base + 4]
        event = (xh >> 6) & 0x03
        if event in (EVENT_DOWN, EVENT_CONTACT):
            points.append(
                TouchPoint(
                    x=((xh & 0x0F) << 8) | xl,
                    y=((yh & 0x0F) << 8) | yl,
                    id=(yh >> 4) & 0x0F,
                    event=event,
                )
            )
    return points


def _records_sane(records: bytes, count: int) -> bool:
    """False when any of ``count`` raw 6-byte records is impossible: a
    truncated record or a finger-id nibble above the chip's 0..4."""
    for i in range(count):
        base = i * 6
        if base + 6 > len(records):
            return False
        if (records[base + 2] >> 4) & 0x0F > _MAX_POINTS - 1:
            return False
    return True


def _touch_sane(i2c: I2C) -> bool:
    """One status sample: TD_STATUS count 0, or every claimed record
    plausible.  Junk counts (> 5) decode as none — clean."""
    status = i2c.read_reg(TD_STATUS, 1)[0]
    count = decode_status(status)
    if count == 0:
        return True
    records = i2c.read_reg(_POINT_BASES[0], count * 6)
    return _records_sane(records, count)


def _default_i2c(bus: Bus):
    """The I2C transport for ``bus``: the simulated FT5x06 register file
    when the display runs on :class:`ertftm070.simulator.SimulatedBus`
    (``ERTFTM070_DISPLAY=sim``), else the real ``/dev/i2c-1`` device.

    Keyed off the bus, not the environment: a backend injected with
    ``Display(backend=...)`` keeps real-I2C semantics, and an explicit
    ``i2c=`` passed to :class:`Touch` wins over both.
    """
    from .simulator import SimulatedBus, SimulatedI2C

    if isinstance(bus, SimulatedBus):
        return SimulatedI2C(FT5X06_ADDR, state=bus.touch)
    return I2C(FT5X06_ADDR)


def _rst_pulse(bus: Bus, pin: int) -> None:
    """Drive one /RST pulse: high, low ≥5 ms (Trst), then ≥300 ms of
    settle time (Trsi) before the chip's first report.  Shared by
    :meth:`Touch.open` and :meth:`Touch.reset` — the same timing in
    both, so a later reset behaves exactly like a fresh open."""
    bus.pin_mode(pin, True)
    bus.pin_write(pin, True)  # reset inactive
    bus.pin_write(pin, False)  # real /RST pulse
    time.sleep(0.01)  # >= Trst (5 ms)
    bus.pin_write(pin, True)  # release
    time.sleep(0.3)  # Trsi: >= 300 ms before the first report


def _scale(value: int, vmin: int, vmax: int, size: int, mirror: bool) -> int:
    """One axis of the raw→logical mapping, clamped and mirrored."""
    value = min(max(value, vmin), vmax)
    out = (value - vmin) * (size - 1) // max(vmax - vmin, 1)
    return size - 1 - out if mirror else out


@dataclass(frozen=True)
class TouchCalibration:
    """Raw → logical coordinate mapping.

    The chip outputs 12-bit coordinates whose span is set by per-panel
    firmware (COMMUNITY-RESEARCH.md §6).  The defaults are the span
    measured on this panel on 2026-09-06 by corner touches: raw
    0..799 × 0..479, i.e. panel-native — finger-edge slack keeps the
    observed extremes a pixel or two inside.  ``swap_xy``/``mirror_*``
    cover mounting orientations.
    """

    width: int = 800
    height: int = 480
    raw_x_min: int = 0
    raw_x_max: int = 799
    raw_y_min: int = 0
    raw_y_max: int = 479
    swap_xy: bool = False
    mirror_x: bool = False
    mirror_y: bool = False

    def map(self, point: TouchPoint) -> TouchPoint:
        """Scale one raw point into logical panel coordinates."""
        rx, ry = point.x, point.y
        x_min, x_max = self.raw_x_min, self.raw_x_max
        y_min, y_max = self.raw_y_min, self.raw_y_max
        out_w, out_h = self.width, self.height
        mirror_x, mirror_y = self.mirror_x, self.mirror_y
        if self.swap_xy:
            rx, ry = ry, rx
            x_min, x_max, y_min, y_max = y_min, y_max, x_min, x_max
            out_w, out_h = out_h, out_w
            mirror_x, mirror_y = mirror_y, mirror_x
        return TouchPoint(
            x=_scale(rx, x_min, x_max, out_w, mirror_x),
            y=_scale(ry, y_min, y_max, out_h, mirror_y),
            id=point.id,
            event=point.event,
        )


@dataclass(frozen=True)
class TouchPins:
    """The GPIO pins the touch overlay uses (BCM numbers).

    SCL/SDA are the fixed I2C-1 bus and WAKE is tied to 3.3 V in the
    verified wiring, so only INT and /RST are configurable — ``None``
    disables a pin.  Like :class:`~ertftm070.pins.Pins`, range and
    self-uniqueness are validated at construction; collisions with the
    display's own pins are rejected by :class:`Touch` at construction
    (``_validate_pin_layout``), since both drivers would fight over the
    shared GPIO register bank.
    """

    int_pin: int | None = 15
    rst_pin: int | None = 0

    def __post_init__(self) -> None:
        pins = [p for p in (self.int_pin, self.rst_pin) if p is not None]
        bad = [p for p in pins if not 0 <= p <= GPIO_MAX]
        if bad:
            raise ValueError(
                f"GPIO numbers out of range 0..{GPIO_MAX} (40-pin header): {bad}"
            )
        if len(pins) == 2 and pins[0] == pins[1]:
            raise ValueError("INT and /RST must use different GPIOs")


#: The wiring verified on hardware: INT on GPIO15, /RST on GPIO0.
DEFAULT_TOUCH_PINS = TouchPins()

#: Panel-native raw span, measured by corner touches on 2026-09-06.
DEFAULT_CALIBRATION = TouchCalibration()


class Touch:
    """FT5x06 touch input over the display's bus and I2C-1.

    Args:
        bus: The :class:`~ertftm070.backends.Bus` of the opened
            :class:`~ertftm070.Display` (the fast backend is
            single-owner — pass ``lcd.bus``).
        touch_pins: INT//RST wiring; ``None`` disables a pin.
        calibration: Raw→logical mapping (default: the measured
            panel-native 0..799 × 0..479 span).
        i2c: Injectable I2C transport for tests; the default opens
            ``/dev/i2c-1`` at 0x38 — or, when ``bus`` is the simulated
            backend (``ERTFTM070_DISPLAY=sim``), the simulated register
            file that browser touches feed.

    Lifecycle: close the touch before its Display — the context
    manager pair ``with Display() as lcd, Touch(lcd.bus) as touch:``
    exits in exactly that order.  Closing the Display first leaves a
    live Touch whose I2C reads still work but whose pin operations
    (INT, /RST) reach the released bus.
    """

    def __init__(
        self,
        bus: Bus,
        touch_pins: TouchPins = DEFAULT_TOUCH_PINS,
        calibration: TouchCalibration = DEFAULT_CALIBRATION,
        i2c: I2C | None = None,
    ):
        self._bus = bus
        self.touch_pins = touch_pins
        self.calibration = calibration
        self._i2c = i2c or _default_i2c(bus)
        self._opened = False
        self._validate_pin_layout(bus)

    def _validate_pin_layout(self, bus: Bus) -> None:
        """Reject a touch wiring that aliases the display's own pins.

        INT and /RST live on the same physical connector the display
        uses, and both drivers write their pins' levels through the
        same register bank — a collision is not merely a short on the
        bench, but an active fight between ``Display`` and ``Touch``
        (e.g. a /RST pulse into the SSD1963's reset line resets the
        display mid-frame).  The checked bus implementations expose
        their wiring as ``.pins``; anything else cannot be validated
        and is accepted as-is.
        """
        bus_pins = getattr(bus, "pins", None)
        if bus_pins is None:
            return
        display_pins = {
            *bus_pins.data_low,
            *bus_pins.data_high,
            bus_pins.cs,
            bus_pins.dc,
            bus_pins.wr,
            bus_pins.rd,
            bus_pins.reset,
            bus_pins.backlight,
        }
        if bus_pins.te is not None:
            display_pins.add(bus_pins.te)
        clash = sorted(
            p
            for p in (self.touch_pins.int_pin, self.touch_pins.rst_pin)
            if p is not None and p in display_pins
        )
        if clash:
            raise ValueError(
                "touch pins collide with the display's bus pins "
                f"(GPIO {clash} — see docs/WIRING.md)"
            )

    # -- lifecycle --

    def open(self) -> None:
        """Bring the touch chip up: /RST pulse, pins, I2C, dummy read.

        Idempotent.  No register writes beyond ``0x00 = 0`` (working
        mode) — the FT5x06 needs timing, not configuration.  With a
        /RST pin the chip is actively reset (low ≥ 5 ms, then the
        ≥ 300 ms Trsi settle before its first report); without one the
        board's RC reset has long settled, so that settle is skipped.
        The chip is then polled for up to ~5 s until TD_STATUS stops
        claiming impossible touches (the phantom power-on state, see
        ``_warn_phantom``) — it is waited out, not fatal.
        """
        if self._opened:
            return
        b = self._bus_checked()
        if self.touch_pins.rst_pin is not None:
            _rst_pulse(b, self.touch_pins.rst_pin)
        if self.touch_pins.int_pin is not None:
            b.pin_mode(self.touch_pins.int_pin, False)  # INT is an input
        self._i2c.open()
        try:
            self._i2c.read_reg(0x00, 1)  # dummy read: flush first-access garbage
            self._i2c.write_reg(0x00, b"\x00")  # device mode = working
            self._wait_sane()
        except BaseException:
            # The bus was opened but the chip did not answer (a wiring/
            # power fault — _wait_sane propagates those on purpose, see
            # there): release the fd so a retry or process exit does not
            # leak it.  _opened is still False, so the touch stays
            # "needs open()" and the Display-half of the bus is left
            # untouched.
            self._i2c.close()
            raise
        self._opened = True

    def close(self) -> None:
        self._i2c.close()
        self._opened = False

    def __enter__(self) -> Touch:
        self.open()
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def _bus_checked(self) -> Bus:
        if self._bus is None:
            raise RuntimeError(
                "Touch needs an open bus — pass the Display's bus (lcd.bus)"
            )
        return self._bus

    def _wait_sane(self) -> None:
        """Poll TD_STATUS + records until the chip leaves its phantom
        power-on state, or ~5 s pass.

        Phantom state is not fatal — the chip self-recovers within
        minutes — so a timeout only warns (once per episode, see
        ``_warn_phantom``) and open()/reset() proceed.  An I2CError from
        a poll propagates: a bus that stops answering is a wiring/power
        fault (see ``_i2c.I2CError``), not a phantom, and per-transfer
        timeouts bound it anyway.
        """
        deadline = time.monotonic() + _SANITY_TIMEOUT
        while True:
            if _touch_sane(self._i2c):
                # A sane sample means the episode is over: re-arm the
                # warn-once latch for the next one.
                _mark_phantom_clear()
                return
            if time.monotonic() >= deadline:
                _warn_phantom()
                return
            time.sleep(_SANITY_POLL)

    # -- input --

    def read(self, mapped: bool = False) -> list[TouchPoint]:
        """The touches present right now (possibly empty).

        One TD_STATUS byte, then one 6-byte record per touch from 0x03
        — the minimum traffic per poll.  ``mapped=True`` returns
        logical panel coordinates via the calibration.

        Press frames can carry bogus coordinates ("bogus coordinates in
        TOUCH_DOWN" — the kernel's quirk note for this chip family);
        such points are dropped, and the contact frames that follow
        carry the real positions.
        """
        if not self._opened:
            raise RuntimeError(
                "touch not open — call open() or use the context manager"
            )
        status = self._i2c.read_reg(TD_STATUS, 1)[0]
        count = decode_status(status)
        if not count:
            return []
        records = self._i2c.read_reg(_POINT_BASES[0], count * 6)
        points = decode_points(records, count)
        # Finger ids above the chip's 0..4 are impossible — they are the
        # phantom power-on signature _records_sane detects — so drop such
        # points here too: a phantom that outlives open()'s ~5 s wait
        # must not surface as touches for the minutes it persists.
        points = [p for p in points if p.id <= _MAX_POINTS - 1]
        # Full-scale coordinates are the family's "unpressed" marker, not
        # a position on any panel — filter on the chip's own junk band
        # rather than the calibration span, which may legitimately not
        # cover the whole raw range on other mountings.
        points = [
            p
            for p in points
            if p.event != EVENT_DOWN
            or (p.x < _JUNK_COORD and p.y < _JUNK_COORD)
        ]
        if mapped:
            cal = self.calibration
            points = [cal.map(p) for p in points]
        return points

    def _has_touch(self) -> bool:
        """A *real* touch is present: TD_STATUS claims a count and the
        claimed records decode to at least one plausible point (finger
        id 0..4, press/contact event).  Phantom frames — TD_STATUS 0x35
        with impossible-id records, see ``_records_sane`` — read as no
        touch, so ``wait_touch`` does not wake on them and a phantom
        that outlives open()'s wait-out cannot busy-trigger a wake.
        """
        count = decode_status(self._i2c.read_reg(TD_STATUS, 1)[0])
        if count == 0:
            return False
        records = self._i2c.read_reg(_POINT_BASES[0], count * 6)
        return any(p.id <= _MAX_POINTS - 1 for p in decode_points(records, count))

    def wait_touch(
        self, timeout: float | None = None, poll_interval: float = 0.02
    ) -> bool:
        """Block until a touch is present, or return False on timeout.

        Nothing here hard-blocks on the INT pin: it is *polled* every
        ``poll_interval`` seconds (a plain GPIO read — the pin has no
        edge-interrupt wait available on this bus model), so worst-case
        wake latency is one ``poll_interval`` with or without it.  The
        first poll confirms TD_STATUS unconditionally, so a finger
        already resting on the panel when this is called is reported.

        INT polarity is firmware-dependent — measured on this panel
        (2026-09-06): the line idles LOW and rises HIGH during touches,
        the opposite of the datasheet's active-low convention — so any
        *change* on the INT pin counts as an event and is confirmed on
        TD_STATUS.  A status confirm also runs every ~200 ms regardless,
        so a stuck or missing INT degrades to plain polling instead of
        hanging.  Without an INT pin, TD_STATUS is polled directly.
        This is the wake-on-touch primitive::

            lcd.sleep()
            lcd.backlight(False)
            touch.wait_touch()  # display dark, waiting for a finger
            lcd.backlight(True)
            lcd.wake()
        """
        if not self._opened:
            raise RuntimeError(
                "touch not open — call open() or use the context manager"
            )
        b = self._bus_checked()
        deadline = None if timeout is None else time.monotonic() + timeout
        int_pin = self.touch_pins.int_pin
        last = None if int_pin is None else b.pin_read(int_pin)
        ticks = 0
        while True:
            ticks += 1
            if int_pin is not None:
                level = b.pin_read(int_pin)
                edge = level != last  # any change: rising or falling
                last = level
                if (ticks == 1 or edge or ticks % 10 == 0) and self._has_touch():
                    return True
            elif self._has_touch():
                return True
            if deadline is not None and time.monotonic() > deadline:
                return False
            time.sleep(poll_interval)

    def reset(self) -> None:
        """Pulse /RST low ≥5 ms (Trst), then wait the 300 ms (Trsi) the
        chip needs before its first report — the recovery hammer for a
        wedged FT5x06.  Afterwards the first-report garbage is flushed
        the same way :meth:`open` does and the phantom power-on state is
        waited out the same way too.

        What the wait cannot promise: a phantom that outlives the ~5 s
        budget is warned about and *carried on from* (the chip only
        self-recovers after minutes), and the pulse does not clear it
        (measured).  Data surfaced after that stays clean regardless —
        :meth:`read` and :meth:`wait_touch` drop the phantom's
        impossible-id records."""
        if not self._opened:
            raise RuntimeError(
                "touch not open — call open() or use the context manager"
            )
        if self.touch_pins.rst_pin is None:
            raise RuntimeError("no /RST pin configured (TouchPins.rst_pin is None)")
        b = self._bus_checked()
        _rst_pulse(b, self.touch_pins.rst_pin)
        self._i2c.read_reg(0x00, 1)  # dummy read: flush first-access garbage
        self._wait_sane()  # the phantom state can outlive the pulse
