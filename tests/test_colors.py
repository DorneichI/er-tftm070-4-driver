"""Color conversion: RGB888 -> RGB565."""
from __future__ import annotations

from PIL import Image

from ertftm070.colors import rgb565, rgb888_to_565_buffer


def test_rgb565_primaries():
    assert rgb565(255, 0, 0) == 0xF800
    assert rgb565(0, 255, 0) == 0x07E0
    assert rgb565(0, 0, 255) == 0x001F
    assert rgb565(0, 0, 0) == 0x0000
    assert rgb565(255, 255, 255) == 0xFFFF


def test_rgb565_secondaries():
    assert rgb565(255, 255, 0) == 0xFFE0
    assert rgb565(0, 255, 255) == 0x07FF
    assert rgb565(255, 0, 255) == 0xF81F


def test_rgb565_truncates_low_bits():
    # 5 bits red, 6 green, 5 blue — low bits are dropped, not rounded
    assert rgb565(1, 0, 0) == 0x0000
    assert rgb565(0, 1, 0) == 0x0000
    assert rgb565(0, 0, 1) == 0x0000
    assert rgb565(248, 252, 248) == 0xFFFF  # 0xF8 / 0xFC / 0xF8


def test_buffer_packing_row_major():
    im = Image.new("RGB", (2, 1))
    im.putpixel((0, 0), (255, 0, 0))
    im.putpixel((1, 0), (0, 0, 255))
    buf = rgb888_to_565_buffer(im)
    assert list(buf) == [0xF800, 0x001F]


def test_buffer_packing_two_rows():
    im = Image.new("RGB", (1, 2))
    im.putpixel((0, 0), (255, 255, 255))
    im.putpixel((0, 1), (0, 0, 0))
    buf = rgb888_to_565_buffer(im)
    assert list(buf) == [0xFFFF, 0x0000]


def test_buffer_packing_converts_modes():
    # RGBA/L images are converted to RGB first
    im = Image.new("RGBA", (1, 1), (255, 0, 0, 128))
    buf = rgb888_to_565_buffer(im)
    assert list(buf) == [0xF800]
