"""Display logic against the FakeBus — no hardware needed."""
from __future__ import annotations

import pytest
from PIL import Image

from ertftm070 import NotOnRaspberryPi, rgb565
from ertftm070.display import Display, _map_rect
from tests.conftest import FailOpenBus, FakeBus

PIXELS = [0x0000, 0xF800, 0x07E0, 0x001F, 0xFFFF, 0xFFE0, 0x07FF, 0xF81F]


def test_open_configures_pins_and_inits(display, bus):
    display.open()
    # all 22 pins configured as outputs
    pins = list(bus.pin_modes)
    assert len(pins) == 22
    assert all(bus.pin_modes[p] is True for p in pins)
    # idle states: control lines high, data and backlight low
    for p in (bus.pins.cs, bus.pins.dc, bus.pins.wr, bus.pins.rd, bus.pins.reset):
        assert bus.pin_levels[p] is True
    for p in bus.pins.data:
        assert bus.pin_levels[p] is False
    # init ran: first command is 0xE2 (PLL) after the reset pulse
    assert bus.commands()[0] == 0xE2
    assert bus.pin_levels[bus.pins.backlight] is True  # backlight on by default


def test_init_writes_f0_and_post_init_3a(display, bus):
    display.open()
    stream = bus.bytes_written
    # 0xF0 command followed by data byte 0x03
    i = stream.index((False, 0xF0))
    assert stream[i + 1] == (True, 0x03)
    # 0x3A=0x50 comes after 0x29 (display on), at the very end of the table
    assert stream.index((False, 0x29)) < stream.index((False, 0x3A))
    assert stream[stream.index((False, 0x3A)) + 1] == (True, 0x50)


def test_fill_rect_window_and_stream(display, bus):
    display.open()
    display.fill_rect(0, 0, 100, 50, 0xF800)
    # one continuous stream, exactly w*h words
    assert len(bus.streams) == 1
    assert len(bus.streams[0]) == 100 * 50
    assert set(bus.streams[0]) == {0xF800}
    # window command: 0x2A then 4 data bytes (x0=0, x1=99)
    stream = bus.bytes_written
    i = stream.index((False, 0x2A))
    assert [b for _, b in stream[i + 1 : i + 5]] == [0x00, 0x00, 0x00, 99]
    j = stream.index((False, 0x2B))
    assert [b for _, b in stream[j + 1 : j + 5]] == [0x00, 0x00, 0x00, 49]
    # and the memory-write command before the stream
    assert (False, 0x2C) in stream


def test_fill_uses_full_logical_screen(display, bus):
    display.open()
    display.fill(0x07E0)
    assert len(bus.streams[0]) == 800 * 480


def test_set_pixel(display, bus):
    display.open()
    display.set_pixel(400, 240, 0xFFFF)
    assert bus.streams[-1] == [0xFFFF]


def test_image_blit(display, bus):
    im = Image.new("RGB", (2, 3))
    for y in range(3):
        for x in range(2):
            im.putpixel((x, y), (x * 255, y * 100, 128))
    display.open()
    display.image(im, x=5, y=7)
    assert len(bus.streams[-1]) == 6
    expected = [rgb565(*im.getpixel((x, y))) for y in range(3) for x in range(2)]
    assert bus.streams[-1] == expected
    # window covers (5,7)-(6,9)
    stream = bus.bytes_written
    i = stream.index((False, 0x2A))
    assert [b for _, b in stream[i + 1 : i + 5]] == [0x00, 0x05, 0x00, 0x06]
    j = stream.index((False, 0x2B))
    assert [b for _, b in stream[j + 1 : j + 5]] == [0x00, 0x07, 0x00, 0x09]


def test_bounds_checks(display, bus):
    display.open()
    with pytest.raises(ValueError):
        display.fill_rect(-1, 0, 10, 10, 0)
    with pytest.raises(ValueError):
        display.fill_rect(0, 0, 801, 480, 0)
    with pytest.raises(ValueError):
        display.fill_rect(0, 0, 0, 10, 0)
    with pytest.raises(ValueError):
        display.set_pixel(800, 0, 0)


def test_rotation_dimensions_and_window_mapping(display, bus):
    display.open()
    display.rotation = 90
    assert (display.width, display.height) == (480, 800)
    display.fill_rect(0, 0, 480, 800, 0xF800)
    # the logical full screen maps onto the full controller window
    stream = bus.bytes_written
    i = stream.index((False, 0x2A))
    assert [b for _, b in stream[i + 1 : i + 5]] == [0x00, 0x00, 0x03, 0x1F]  # 0..799
    j = stream.index((False, 0x2B))
    assert [b for _, b in stream[j + 1 : j + 5]] == [0x00, 0x00, 0x01, 0xDF]  # 0..479
    # rotation changes MADCTL
    assert bus.commands().count(0x36) >= 2  # once during init, once for rotation


def test_rotation_rejects_bad_values(display):
    with pytest.raises(ValueError):
        display.rotation = 45


@pytest.mark.parametrize(
    "rotation,rect,expected",
    [
        (0, (0, 0, 0, 0), (0, 0, 0, 0)),
        (0, (10, 20, 30, 40), (10, 20, 30, 40)),
        (180, (0, 0, 0, 0), (799, 479, 799, 479)),
        (180, (10, 20, 30, 40), (769, 439, 789, 459)),
        (90, (0, 0, 0, 0), (0, 479, 0, 479)),
        (90, (0, 0, 479, 799), (0, 0, 799, 479)),  # full screen, swapped
        (270, (0, 0, 0, 0), (799, 0, 799, 0)),
    ],
)
def test_map_rect(rotation, rect, expected):
    assert _map_rect(rotation, *rect) == expected


def test_sleep_wake(display, bus):
    display.open()
    bus.bytes_written.clear()
    display.sleep()
    assert bus.commands() == [0x28, 0x10]
    bus.bytes_written.clear()
    display.wake()
    assert bus.commands() == [0x11, 0x29]


def test_backlight_and_close(display, bus):
    display.open()
    display.backlight(False)
    assert bus.pin_levels[bus.pins.backlight] is False
    display.backlight(True)
    assert bus.pin_levels[bus.pins.backlight] is True
    display.close()
    assert bus.closed is True
    assert bus.pin_levels[bus.pins.backlight] is False  # off on close
    display.close()  # idempotent


def test_context_manager(bus):
    with Display(backend=bus) as lcd:
        assert bus.open_calls == 1
        lcd.fill(0xF800)
    assert bus.closed is True


def test_open_failure_raises_not_on_raspberry_pi():
    with pytest.raises(NotOnRaspberryPi):
        Display(backend=FailOpenBus()).open()


def test_selftest_pass(display, bus):
    display.open()
    bus.bytes_written.clear()
    bus.read_words = [0x01, 0x57, 0x61, 0x01, 0xFF, 0x00] + [0x0F, 0x01, 0x00]
    assert display.selftest() is True
    assert bus.commands()[0] == 0xA1


def test_selftest_fail_on_wrong_ddb(display, bus):
    display.open()
    bus.read_words = [0x00, 0x00, 0x00, 0x00, 0x00, 0x00] + [0x0F, 0x01, 0x00]
    assert display.selftest() is False


def test_gramcheck_pass(display, bus):
    display.open()
    bus.bytes_written.clear()
    bus.read_words = PIXELS + [0x0000, 0x0000]
    assert display.gramcheck() is True
    assert bus.streams[-1] == PIXELS  # exactly the known pixels


def test_gramcheck_fail(display, bus):
    display.open()
    bus.read_words = [0x0000] * 10
    assert display.gramcheck() is False


def test_backlight_off_via_constructor():
    bus = FakeBus()
    Display(backend=bus, backlight=False).open()
    assert bus.pin_levels[bus.pins.backlight] is False


def test_auto_init_false_skips_init():
    bus = FakeBus()
    Display(backend=bus, auto_init=False).open()
    # no init table sent — only the MADCTL write from _apply_rotation
    assert bus.commands() == [0x36]
