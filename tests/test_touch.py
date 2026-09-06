"""FT5x06 touch logic — decoders, calibration, and the Touch class
against fakes.  Frame layouts come from docs/COMMUNITY-RESEARCH.md §6."""
from __future__ import annotations

import pytest

from ertftm070.touch import (
    EVENT_CONTACT,
    EVENT_DOWN,
    Touch,
    TouchCalibration,
    TouchPins,
    TouchPoint,
    decode_points,
    decode_status,
)


class FakeI2C:
    """Records writes and reads; read_reg answers from a per-register script."""

    def __init__(self):
        self.opened = False
        self.closed = False
        self.writes = []  # (reg, data bytes)
        self.reads = []  # (reg, n) — every read_reg call
        self.responses = {}  # reg -> bytes

    def open(self):
        self.opened = True

    def close(self):
        self.closed = True

    def write_reg(self, reg, data=b""):
        self.writes.append((reg, bytes(data)))

    def read_reg(self, reg, n):
        data = self.responses.get(reg, b"\x00" * n)
        self.reads.append((reg, n))
        assert len(data) == n, f"FakeI2C script for 0x{reg:02X} is {n} bytes short"
        return data


class _FakeClock:
    """monotonic() stand-in that advances 50 ms per call — wait loops
    hit their deadlines in a handful of iterations instead of seconds."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        self.now += 0.05
        return self.now


# ----------------------------------------------------------------------
# Decoders (pure)
# ----------------------------------------------------------------------


def test_decode_status_counts_low_nibble_and_rejects_junk():
    assert decode_status(0x00) == 0
    assert decode_status(0x01) == 1
    assert decode_status(0x05) == 5
    assert decode_status(0x06) == 0  # more than the map can hold: junk
    assert decode_status(0x0F) == 0
    assert decode_status(0x10) == 0  # gesture bits only


def test_decode_points_parses_one_down_record():
    # X=0x300 (event 00 in XH bits 7:6), Y=0x100, finger id 2
    records = bytes([0x03, 0x00, 0x21, 0x00, 0x00, 0x00])
    points = decode_points(records, 1)
    assert points == [TouchPoint(x=0x300, y=0x100, id=2, event=EVENT_DOWN)]


def test_decode_points_keeps_contact_and_drops_release():
    down = bytes([0x03, 0x00, 0x21, 0x00, 0x00, 0x00])
    up = bytes([0x43, 0x00, 0x21, 0x00, 0x00, 0x00])  # event 01: release
    contact = bytes([0x83, 0x00, 0x21, 0x00, 0x00, 0x00])  # event 10: move
    points = decode_points(down + up + contact, 3)
    assert [p.event for p in points] == [EVENT_DOWN, EVENT_CONTACT]
    assert all(p.x == 0x300 for p in points)


def test_decode_points_stops_at_truncated_records():
    records = bytes([0x03, 0x00, 0x21, 0x00, 0x00, 0x00, 0x83, 0x00])  # 1.3 records
    points = decode_points(records, 2)
    assert len(points) == 1


def test_decode_points_parses_multitouch_records_at_strided_bases():
    # two records, one at base 0 and one at base 6 (register 0x09)
    records = bytes([0x03, 0x00, 0x21, 0x00, 0x00, 0x00]) * 2
    points = decode_points(records, 2)
    assert len(points) == 2
    assert points[1].id == 2


# ----------------------------------------------------------------------
# Calibration (pure)
# ----------------------------------------------------------------------


def test_calibration_scales_panel_native_span():
    # default span measured on the panel: raw 0..799 x 0..479
    cal = TouchCalibration()
    p = cal.map(TouchPoint(x=0, y=479, id=0, event=EVENT_DOWN))
    assert (p.x, p.y) == (0, 479)
    p = cal.map(TouchPoint(x=799, y=0, id=0, event=EVENT_DOWN))
    assert (p.x, p.y) == (799, 0)


def test_calibration_clamps_and_mirrors():
    cal = TouchCalibration(mirror_x=True, mirror_y=True)
    p = cal.map(TouchPoint(x=0, y=0, id=0, event=EVENT_CONTACT))
    assert (p.x, p.y) == (799, 479)
    p = cal.map(TouchPoint(x=-100, y=9999, id=0, event=EVENT_CONTACT))
    assert (p.x, p.y) == (799, 0)  # clamped then mirrored


def test_calibration_swap_xy_maps_axes_and_sizes():
    cal = TouchCalibration(width=800, height=480, swap_xy=True)
    p = cal.map(TouchPoint(x=799, y=0, id=0, event=EVENT_DOWN))
    # raw x (799) feeds the swapped y axis, which spans the 800-wide
    # side; raw y (0) feeds the swapped x axis (480-wide side)
    assert (p.x, p.y) == (0, 799)


# ----------------------------------------------------------------------
# Touch class (fakes)
# ----------------------------------------------------------------------


def test_touch_open_settles_dummy_reads_and_sets_working_mode(bus):
    i2c = FakeI2C()
    t = Touch(bus, i2c=i2c)
    t.open()
    # pins: /RST output high, INT input
    assert bus.pin_modes[t.touch_pins.rst_pin] is True
    assert bus.pin_levels[t.touch_pins.rst_pin] is True
    assert bus.pin_modes[t.touch_pins.int_pin] is False
    # one dummy read, then device mode = working (0x00 <- 0x00)
    assert i2c.writes == [(0x00, b"\x00")]


def test_touch_open_skips_none_pins(bus):
    i2c = FakeI2C()
    t = Touch(bus, touch_pins=TouchPins(int_pin=None, rst_pin=None), i2c=i2c)
    t.open()
    assert bus.pin_modes == {}


def test_touch_read_empty_status_gives_no_points(bus):
    i2c = FakeI2C()
    i2c.responses[0x02] = b"\x00"
    t = Touch(bus, i2c=i2c)
    t.open()
    assert t.read() == []


def test_touch_read_parses_points_and_maps_them(bus):
    i2c = FakeI2C()
    i2c.responses[0x02] = b"\x01"
    i2c.responses[0x03] = bytes([0x03, 0x00, 0x21, 0x00, 0x00, 0x00])
    t = Touch(bus, i2c=i2c)
    t.open()
    points = t.read()
    assert len(points) == 1
    assert (points[0].x, points[0].y) == (0x300, 0x100)
    mapped = t.read(mapped=True)
    # default calibration is panel-native: identity for in-span points
    assert (mapped[0].x, mapped[0].y) == (0x300, 0x100)


def test_touch_read_drops_bogus_down_frame(bus):
    # the kernel's "bogus coordinates in TOUCH_DOWN" quirk: a press whose
    # position is the full-scale "unpressed" marker (0x0FFF) is dropped —
    # the chip-family rule, independent of the calibration span
    i2c = FakeI2C()
    i2c.responses[0x02] = b"\x01"
    i2c.responses[0x03] = bytes([0x0F, 0xFF, 0x2F, 0xFF, 0x00, 0x00])  # 4095,4095
    t = Touch(
        bus,
        i2c=i2c,
        calibration=TouchCalibration(raw_x_max=2000, raw_y_max=2000),
    )
    t.open()
    assert t.read() == []


def test_touch_read_keeps_sub_marker_down_frames(bus):
    # the filter is the chip's full-scale junk band (_JUNK_COORD), NOT the
    # calibration span: a DOWN below 0x0FF0 passes through raw — with the
    # default calibration (span 0..799) it reads raw and maps clamped
    i2c = FakeI2C()
    i2c.responses[0x02] = b"\x01"
    i2c.responses[0x03] = bytes([0x05, 0x00, 0x21, 0x00, 0x00, 0x00])  # 0x500,0x100
    t = Touch(bus, i2c=i2c)
    t.open()
    assert t.read()[0].x == 0x500
    mapped = t.read(mapped=True)[0]
    assert (mapped.x, mapped.y) == (799, 256)  # clamped into the 0..799 span


def test_touch_read_requires_open(bus):
    t = Touch(bus, i2c=FakeI2C())
    with pytest.raises(RuntimeError):
        t.read()


def _patch_time(monkeypatch):
    import ertftm070.touch as touch_module

    monkeypatch.setattr(touch_module.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(touch_module.time, "monotonic", _FakeClock())


def test_touch_wait_uses_int_pin_and_confirms_on_status(bus, monkeypatch):
    i2c = FakeI2C()
    i2c.responses[0x02] = b"\x01"
    t = Touch(bus, i2c=i2c)
    t.open()
    bus.pin_read_script = [True, False]  # INT high (idle), then low (pending)
    _patch_time(monkeypatch)
    assert t.wait_touch(timeout=1.0) is True


def test_touch_wait_polls_status_without_int_pin(bus, monkeypatch):
    i2c = FakeI2C()
    i2c.responses[0x02] = b"\x00"
    t = Touch(bus, touch_pins=TouchPins(int_pin=None, rst_pin=None), i2c=i2c)
    t.open()
    _patch_time(monkeypatch)
    assert t.wait_touch(timeout=1.0) is False  # no touch ever arrives


def test_touch_wait_catches_finger_resting_before_the_call(bus, monkeypatch):
    # a finger already down must be caught on the FIRST poll: TD_STATUS
    # is confirmed unconditionally at entry, with no INT edge and no
    # ~200 ms safety-poll wait first
    i2c = FakeI2C()
    i2c.responses[0x02] = b"\x01"
    t = Touch(bus, i2c=i2c)
    t.open()
    bus.pin_levels[t.touch_pins.int_pin] = False  # INT idle the whole time
    slept = []
    monkeypatch.setattr("ertftm070.touch.time.sleep", slept.append)
    assert t.wait_touch(timeout=1.0) is True
    assert slept == []  # returned on the first confirm, never polled


def test_touch_wait_int_edge_triggers_immediate_status_confirm(bus, monkeypatch):
    # status says "no touch" on the first poll; the moment INT changes,
    # TD_STATUS is re-read and the pending touch is confirmed
    import ertftm070.touch as touch_module

    class TogglingI2C(FakeI2C):
        def read_reg(self, reg, n):
            if reg == 0x02:
                self.status_reads += 1
                self.responses[0x02] = b"\x01" if self.status_reads > 1 else b"\x00"
            return super().read_reg(reg, n)

    i2c = TogglingI2C()
    i2c.status_reads = 0
    t = Touch(bus, i2c=i2c)
    t.open()
    i2c.reads.clear()
    bus.pin_read_script = [False, False, False, True]  # idle... then INT rises
    monkeypatch.setattr(touch_module.time, "sleep", lambda s: None)
    monkeypatch.setattr(touch_module.time, "monotonic", _FakeClock())
    assert t.wait_touch(timeout=1.0) is True
    # confirm on the edge, not on the every-10th-tick safety poll
    assert i2c.reads.count((0x02, 1)) == 2


def test_touch_wait_requires_open(bus):
    t = Touch(bus, i2c=FakeI2C())
    with pytest.raises(RuntimeError):
        t.wait_touch(timeout=0.01)


def test_touch_reset_pulses_rst_low_then_high(bus):
    i2c = FakeI2C()
    t = Touch(bus, i2c=i2c)
    t.open()
    bus.pin_levels[t.touch_pins.rst_pin] = True
    t.reset()
    assert bus.pin_levels[t.touch_pins.rst_pin] is True  # back to inactive


def test_touch_reset_without_rst_pin_raises(bus):
    t = Touch(bus, touch_pins=TouchPins(int_pin=None, rst_pin=None), i2c=FakeI2C())
    t.open()
    with pytest.raises(RuntimeError):
        t.reset()


def test_touch_reset_requires_open(bus):
    t = Touch(bus, i2c=FakeI2C())  # default TouchPins has /RST on GPIO0
    with pytest.raises(RuntimeError):
        t.reset()


def test_touch_reset_flushes_first_report_garbage(bus):
    # after a /RST pulse + Trsi wait the chip needs the same first-access
    # flush open() does — reset() must leave it in a clean state
    i2c = FakeI2C()
    t = Touch(bus, i2c=i2c)
    t.open()
    i2c.reads.clear()
    t.reset()
    assert i2c.reads == [(0x00, 1)]


def test_touch_pins_validates_range_and_uniqueness():
    with pytest.raises(ValueError):
        TouchPins(int_pin=28)  # beyond the 40-pin header (0..27)
    with pytest.raises(ValueError):
        TouchPins(rst_pin=31)
    with pytest.raises(ValueError):
        TouchPins(int_pin=5, rst_pin=5)  # INT and /RST must differ
    # None disables a pin and skips validation of it
    assert TouchPins(int_pin=None, rst_pin=None).int_pin is None
    assert TouchPins().int_pin == 15
    assert TouchPins().rst_pin == 0
