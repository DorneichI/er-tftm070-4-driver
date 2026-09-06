"""Display logic against the FakeBus — no hardware needed."""
from __future__ import annotations

from array import array

import pytest
from PIL import Image

from ertftm070 import NotOnRaspberryPi, rgb565
from ertftm070.display import Display, _map_rect
from tests.conftest import FailOpenBus, FakeBus

PIXELS = [0x0000, 0xF800, 0x07E0, 0x001F, 0xFFFF, 0xFFE0, 0x07FF, 0xF81F]


def test_open_configures_pins_and_inits(display, bus):
    display.open()
    # 22 pins configured as outputs, TE as the sole input
    pins = list(bus.pin_modes)
    assert len(pins) == 23
    assert bus.pin_modes[bus.pins.te] is False
    assert all(bus.pin_modes[p] is True for p in pins if p != bus.pins.te)
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
    # 0x35=0x00 (tearing effect on, V-blanking) follows, driver-level
    assert stream.index((False, 0x3A)) < stream.index((False, 0x35))
    assert stream[stream.index((False, 0x35)) + 1] == (True, 0x00)


def test_fill_rect_window_and_stream(display, bus):
    display.open()
    display.fill_rect(0, 0, 100, 50, 0xF800)
    # one burst PER ROW (contains a swallowed word to a single row)
    assert len(bus.streams) == 50
    assert all(len(s) == 100 for s in bus.streams)
    assert all(set(s) == {0xF800} for s in bus.streams)
    # first row's window: 0x2A then 4 data bytes (x0=0, x1=99), 0x2B (y0=y1=0)
    stream = bus.bytes_written
    i = stream.index((False, 0x2A))
    assert [b for _, b in stream[i + 1 : i + 5]] == [0x00, 0x00, 0x00, 99]
    j = stream.index((False, 0x2B))
    assert [b for _, b in stream[j + 1 : j + 5]] == [0x00, 0x00, 0x00, 0x00]
    # last row's window: y0=y1=49
    last = [k for k, (dc, b) in enumerate(stream) if dc is False and b == 0x2B][-1]
    assert [b for _, b in stream[last + 1 : last + 5]] == [0x00, 0x31, 0x00, 0x31]
    # and a memory-write command before each burst
    assert (False, 0x2C) in stream


def test_fill_uses_full_logical_screen(display, bus):
    display.open()
    display.fill(0x07E0)
    assert len(bus.streams) == 480  # one burst per row
    assert all(len(s) == 800 for s in bus.streams)
    assert all(set(s) == {0x07E0} for s in bus.streams)


def test_set_pixel(display, bus):
    display.open()
    display.set_pixel(400, 240, 0xFFFF)
    assert bus.streams[-1] == [0xFFFF]
    assert len(bus.streams) == 1  # one row-burst


def test_set_pixel_honors_write_passes(bus):
    # set_pixel goes through _blit_rows: heal passes apply to it too
    display = Display(backend=bus, write_passes=2)
    display.open()
    bus.streams.clear()
    display.set_pixel(100, 50, 0x001F)
    assert len(bus.streams) == 2
    assert bus.streams[0] == bus.streams[1] == [0x001F]
    # both passes address the same single-row window (y0 == y1 == 50)
    stream = bus.bytes_written
    rows = [k for k, (dc, b) in enumerate(stream) if dc is False and b == 0x2B]
    for i in rows:
        assert [b for _, b in stream[i + 1 : i + 5]] == [0x00, 0x32, 0x00, 0x32]


def test_set_pixel_rotation_maps_the_window(display, bus):
    display.open()
    display.rotation = 90
    display.set_pixel(0, 0, 0xF800)  # logical origin -> controller col 0, row 479
    stream = bus.bytes_written
    i = stream.index((False, 0x2A))
    assert [b for _, b in stream[i + 1 : i + 5]] == [0x00, 0x00, 0x00, 0x00]  # col 0
    j = stream.index((False, 0x2B))
    assert [b for _, b in stream[j + 1 : j + 5]] == [0x01, 0xDF, 0x01, 0xDF]  # row 479


def test_image_blit(display, bus):
    im = Image.new("RGB", (2, 3))
    for y in range(3):
        for x in range(2):
            im.putpixel((x, y), (x * 255, y * 100, 128))
    display.open()
    display.image(im, x=5, y=7)
    # one burst per row: 3 rows x 2 words
    assert len(bus.streams) == 3
    expected = [rgb565(*im.getpixel((x, y))) for y in range(3) for x in range(2)]
    assert [w for s in bus.streams for w in s] == expected
    assert bus.streams[1] == expected[2:4]
    # window covers (5,7)-(6,9); first row y0=y1=7, last row y0=y1=9
    stream = bus.bytes_written
    i = stream.index((False, 0x2A))
    assert [b for _, b in stream[i + 1 : i + 5]] == [0x00, 0x05, 0x00, 0x06]
    j = stream.index((False, 0x2B))
    assert [b for _, b in stream[j + 1 : j + 5]] == [0x00, 0x07, 0x00, 0x07]
    last = [k for k, (dc, b) in enumerate(stream) if dc is False and b == 0x2B][-1]
    assert [b for _, b in stream[last + 1 : last + 5]] == [0x00, 0x09, 0x00, 0x09]


def test_image_fit_at_rotation(display, bus):
    # regression: an 800x480 image does not fit a 480x800 (rotated) screen;
    # fit=True must scale it instead of raising
    display.open()
    display.rotation = 90
    im = Image.new("RGB", (800, 480), (255, 0, 0))
    display.image(im, fit=True)
    # scaled to 480x288, rotated to 288x480: 480 rows x 288 words
    assert len(bus.streams) == 480
    assert all(len(s) == 288 for s in bus.streams)


def test_image_without_fit_raises_when_rotated(display, bus):
    display.open()
    display.rotation = 90
    im = Image.new("RGB", (800, 480), (255, 0, 0))
    with pytest.raises(ValueError):
        display.image(im)  # 800 wide does not fit the 480-wide screen


def test_image_origin_outside_screen_raises_value_error(display, bus):
    # regression: fit=True used to compute the fit box from (width - x)
    # FIRST, so x == width surfacing a raw PIL ZeroDivisionError
    display.open()
    im = Image.new("RGB", (10, 10))
    for x, y in ((800, 0), (0, 480), (801, 5), (-1, 0), (0, -5)):
        with pytest.raises(ValueError):
            display.image(im, x=x, y=y, fit=True)


def _asymmetric_image():
    """A 2x3 image with a distinct color per pixel (rotation direction check)."""
    im = Image.new("RGB", (2, 3))
    colors = [
        (255, 0, 0), (0, 255, 0), (0, 0, 255),  # x=0 column, top to bottom
        (255, 255, 0), (255, 0, 255), (0, 255, 255),  # x=1 column
    ]
    for y in range(3):
        for x in range(2):
            im.putpixel((x, y), colors[x * 3 + y])
    return im


@pytest.mark.parametrize(
    "rotation,transpose",
    [
        (90, Image.Transpose.ROTATE_90),  # CCW transpose, matches (x,y)->(y,479-x)
        (180, Image.Transpose.ROTATE_180),
        (270, Image.Transpose.ROTATE_270),  # CW transpose, matches (x,y)->(799-y,x)
    ],
)
def test_image_stream_is_rotated_in_software(display, bus, rotation, transpose):
    """The stream must equal the transposed image buffer: the panel never
    rotates (MADCTL stays at its verified value); PIL pre-rotates instead."""
    display.open()
    display.rotation = rotation
    im = _asymmetric_image()
    display.image(im)
    expected = list(rgb565_buffer(im.transpose(transpose)))
    assert [w for s in bus.streams for w in s] == expected


def test_rotate_image_direction():
    """Pin the transpose direction against the algebra:
    at rotation 90 the stream row r, col c must be img(W-1-r, c) —
    i.e. PIL's counter-clockwise ROTATE_90."""
    from ertftm070.colors import rotate_image

    im = _asymmetric_image()
    rotated = rotate_image(im, 90)
    assert rotated.size == (3, 2)  # 2x3 -> 3x2
    for y_ in range(2):
        for x_ in range(3):
            assert rotated.getpixel((x_, y_)) == im.getpixel((1 - y_, x_))


def rgb565_buffer(im):
    from ertftm070.colors import rgb888_to_565_buffer

    return rgb888_to_565_buffer(im)


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
    # the logical full screen maps onto the full controller window:
    # 480 controller rows, one burst per row
    assert len(bus.streams) == 480
    assert all(len(s) == 800 for s in bus.streams)
    stream = bus.bytes_written
    i = stream.index((False, 0x2A))
    assert [b for _, b in stream[i + 1 : i + 5]] == [0x00, 0x00, 0x03, 0x1F]  # 0..799
    j = stream.index((False, 0x2B))
    assert [b for _, b in stream[j + 1 : j + 5]] == [0x00, 0x00, 0x00, 0x00]  # row 0
    # last row: y0=y1=479
    last = [k for k, (dc, b) in enumerate(stream) if dc is False and b == 0x2B][-1]
    assert [b for _, b in stream[last + 1 : last + 5]] == [0x01, 0xDF, 0x01, 0xDF]
    # software rotation never touches MADCTL: only the init table writes 0x36
    assert bus.commands().count(0x36) == 1


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


def test_blit_rows_one_row_blit_call_per_row(display, bus):
    display.open()
    display.fill_rect(10, 20, 3, 4, 0xF800)
    # controller-space coordinates, one backend call per row
    assert bus.row_blit_calls == [
        (10, 12, 20),
        (10, 12, 21),
        (10, 12, 22),
        (10, 12, 23),
    ]


def test_row_blit_honors_write_passes_order(bus):
    display = Display(backend=bus, write_passes=2)
    display.open()
    bus.row_blit_calls.clear()
    display.fill_rect(0, 0, 2, 2, 0x07E0)
    # pass 1 covers all rows before pass 2 repeats them
    assert bus.row_blit_calls == [(0, 1, 0), (0, 1, 1), (0, 1, 0), (0, 1, 1)]


def test_blit_rows_sends_each_rows_own_words(display, bus):
    display.open()
    # two distinct rows of three words; a bug that repeated, dropped or
    # mis-sliced a row's words must show up here
    display._blit_rows(
        array("H", [0x1111, 0x2222, 0x3333, 0x4444, 0x5555, 0x6666]),
        x0=0, y0=0, x1=2, y1=1,
    )
    assert bus.row_blit_calls == [(0, 2, 0), (0, 2, 1)]
    assert bus.row_blit_words == [
        [0x1111, 0x2222, 0x3333],
        [0x4444, 0x5555, 0x6666],
    ]


def test_write_passes_doubles_the_streams():
    bus = FakeBus()
    display = Display(backend=bus, write_passes=2)
    display.open()
    bus.streams.clear()
    display.fill_rect(0, 0, 10, 4, 0xF800)
    assert len(bus.streams) == 8  # 4 rows x 2 passes
    assert bus.streams[0] == bus.streams[4]  # pass 2 repeats pass 1


def test_write_passes_rejects_zero():
    with pytest.raises(ValueError):
        Display(backend=FakeBus(), write_passes=0)


def test_auto_init_false_skips_init():
    bus = FakeBus()
    Display(backend=bus, auto_init=False).open()
    # no init table, no MADCTL writes — rotation is pure software
    assert bus.commands() == []
