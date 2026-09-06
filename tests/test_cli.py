"""CLI logic with an injected fake display."""
from __future__ import annotations

import pytest

from ertftm070.__main__ import BARS, build_parser, run
from tests.conftest import FakeBus


def _fake_display(bus=None):
    from ertftm070.display import Display

    return Display(backend=bus or FakeBus())


def _args(argv):
    return build_parser().parse_args(argv)


def test_bars_draws_eight_rects(bus):
    display = _fake_display(bus)
    display.open()
    assert run(_args(["bars"]), display=display) == 0
    # 8 bars x one burst per row
    assert len(bus.streams) == len(BARS) * display.height
    w = display.width // 8
    assert bus.streams[0] == [0x0000] * w  # first row of the black bar
    assert bus.streams[-1] == [0xF81F] * w  # last row of the magenta bar


def test_fill_parses_hex_color(bus):
    display = _fake_display(bus)
    display.open()
    assert run(_args(["fill", "F800"]), display=display) == 0
    assert len(bus.streams) == 480  # one burst per row
    assert all(len(s) == 800 for s in bus.streams)
    assert all(set(s) == {0xF800} for s in bus.streams)


def test_selftest_exit_codes(bus):
    display = _fake_display(bus)
    display.open()
    bus.read_words = [0x01, 0x57, 0x61, 0x01, 0xFF, 0x00] + [0x0F, 0x01, 0x00]
    assert run(_args(["selftest"]), display=display) == 0
    bus.read_words = [0x00] * 6 + [0x00] * 3
    assert run(_args(["selftest"]), display=display) == 1


def test_gramcheck_exit_codes(bus):
    from ertftm070.display import Display

    PIXELS = [0x0000, 0xF800, 0x07E0, 0x001F, 0xFFFF, 0xFFE0, 0x07FF, 0xF81F]
    display = Display(backend=bus)
    display.open()
    bus.read_words = PIXELS + [0x0000, 0x0000]
    assert run(_args(["gramcheck"]), display=display) == 0
    bus.read_words = [0x0000] * 10
    assert run(_args(["gramcheck"]), display=display) == 1


def test_image_subcommand(bus):
    from PIL import Image

    display = _fake_display(bus)
    display.open()
    im = Image.new("RGB", (2, 2), (0, 255, 0))
    path = "/tmp/ertftm070-test-image.png"
    im.save(path)
    try:
        assert run(_args(["image", path]), display=display) == 0
    finally:
        import os

        os.unlink(path)
    assert len(bus.streams) == 2  # 2x2 image: one burst per row
    assert [w for s in bus.streams for w in s] == [0x07E0] * 4


def test_parser_requires_a_command():
    with pytest.raises(SystemExit):
        build_parser().parse_args([])
