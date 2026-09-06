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
            lcd.fill_rect(point.x, point.y, 4, 4, 0xFFFF)

``Touch`` shares the :class:`~ertftm070.Display`'s bus for its GPIO
pins — the fast backend is single-owner, so a second GPIO mapping
cannot exist anyway.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from ._i2c import I2C
from .backends import Bus

FT5X06_ADDR = 0x38

TD_STATUS = 0x02
_POINT_BASES = (0x03, 0x09, 0x0F, 0x15, 0x1B)
_MAX_POINTS = 5

# Event flags (XH bits 7:6).  Only 00 (press) and 10 (contact/move)
# carry real positions; 01 (release) and 11 (reserved) are skipped —
# the same filter the kernel and ESPHome apply.
EVENT_DOWN = 0
EVENT_UP = 1
EVENT_CONTACT = 2


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
    disables a pin.
    """

    int_pin: int | None = 15
    rst_pin: int | None = 0


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
            ``/dev/i2c-1`` at 0x38.
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
        self._i2c = i2c or I2C(FT5X06_ADDR)
        self._opened = False

    # -- lifecycle --

    def open(self) -> None:
        """Bring the touch chip up: pins, I2C, settle, dummy read.

        Idempotent.  No register writes beyond ``0x00 = 0`` (working
        mode) — the FT5x06 needs timing, not configuration.
        """
        if self._opened:
            return
        b = self._bus_checked()
        if self.touch_pins.rst_pin is not None:
            b.pin_mode(self.touch_pins.rst_pin, True)
            b.pin_write(self.touch_pins.rst_pin, True)  # reset inactive
        if self.touch_pins.int_pin is not None:
            b.pin_mode(self.touch_pins.int_pin, False)  # INT is an input
        self._i2c.open()
        # First report needs >=300 ms after power-on/reset (Tpon/Trsi).
        time.sleep(0.3)
        self._i2c.read_reg(0x00, 1)  # dummy read: flush first-access garbage
        self._i2c.write_reg(0x00, b"\x00")  # device mode = working
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
                "Touch needs an open bus — pass the Display's bus (lcd._bus)"
            )
        return self._bus

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
        cal = self.calibration
        points = [
            p
            for p in points
            if not (
                p.event == EVENT_DOWN
                and not (
                    cal.raw_x_min <= p.x <= cal.raw_x_max
                    and cal.raw_y_min <= p.y <= cal.raw_y_max
                )
            )
        ]
        if mapped:
            points = [cal.map(p) for p in points]
        return points

    def _has_touch(self) -> bool:
        return decode_status(self._i2c.read_reg(TD_STATUS, 1)[0]) > 0

    def wait_touch(
        self, timeout: float | None = None, poll_interval: float = 0.02
    ) -> bool:
        """Block until a touch is present, or return False on timeout.

        INT polarity is firmware-dependent — measured on this panel
        (2026-09-06): the line idles LOW and rises HIGH during touches,
        the opposite of the datasheet's active-low convention — so any
        *change* on the INT pin counts as an event and is confirmed on
        TD_STATUS.  A status confirm also runs every ~200 ms regardless,
        so a stuck or missing INT degrades to plain polling instead of
        hanging.  Without an INT pin, TD_STATUS is polled directly.
        This is the wake-on-touch primitive::

            lcd.sleep()
            touch.wait_touch()  # backlight off, waiting for a finger
            lcd.wake()
        """
        b = self._bus_checked()
        deadline = None if timeout is None else time.monotonic() + timeout
        int_pin = self.touch_pins.int_pin
        last = None if int_pin is None else b.pin_read(int_pin)
        ticks = 0
        while True:
            ticks += 1
            if int_pin is not None:
                level = b.pin_read(int_pin)
                edge = last is not None and level != last
                last = level
                if (edge or ticks % 10 == 0) and self._has_touch():
                    return True
            elif self._has_touch():
                return True
            if deadline is not None and time.monotonic() > deadline:
                return False
            time.sleep(poll_interval)

    def reset(self) -> None:
        """Pulse /RST low ≥5 ms (Trst), then wait the 300 ms (Trsi) the
        chip needs before its first report — the recovery hammer for a
        wedged FT5x06."""
        if self.touch_pins.rst_pin is None:
            raise RuntimeError("no /RST pin configured (TouchPins.rst_pin is None)")
        b = self._bus_checked()
        pin = self.touch_pins.rst_pin
        b.pin_mode(pin, True)
        b.pin_write(pin, False)
        time.sleep(0.01)
        b.pin_write(pin, True)
        time.sleep(0.3)
