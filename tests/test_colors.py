"""Color conversion: RGB888 -> RGB565."""
from __future__ import annotations

import pytest
from PIL import Image

from ertftm070.colors import fit_image, rgb565, rgb888_to_565_buffer


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


def test_rgb565_rejects_out_of_range_channels():
    # silent wrapping (256 -> 0) would paint a wrong color with no error
    with pytest.raises(ValueError):
        rgb565(256, 0, 0)
    with pytest.raises(ValueError):
        rgb565(0, -1, 0)
    with pytest.raises(ValueError):
        rgb565(255, 255, 256)


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


def test_buffer_matches_per_pixel_packing():
    # the LUT pipeline must agree with rgb565() on every pixel
    im = Image.new("RGB", (3, 2))
    for y in range(2):
        for x in range(3):
            im.putpixel((x, y), (7 + x * 80, 5 + y * 120, 40 + x * 90))
    buf = rgb888_to_565_buffer(im)
    expected = [rgb565(*im.getpixel((x, y))) for y in range(2) for x in range(3)]
    assert list(buf) == expected


def test_buffer_alpha_flattened_onto_black():
    # convert("RGB") would DROP alpha — transparent red must blend to black
    opaque = Image.new("RGBA", (1, 1), (255, 0, 0, 255))
    assert list(rgb888_to_565_buffer(opaque)) == [0xF800]
    half = Image.new("RGBA", (1, 1), (255, 0, 0, 128))
    assert list(rgb888_to_565_buffer(half)) == [0x8000]  # ~128/255 red
    clear = Image.new("RGBA", (1, 1), (255, 0, 0, 0))
    assert list(rgb888_to_565_buffer(clear)) == [0x0000]


def test_buffer_flattens_la():
    im = Image.new("LA", (1, 1), (200, 128))
    # 200 * 128/255 ≈ 100 on every channel -> gray 100 = 0x632C
    assert list(rgb888_to_565_buffer(im)) == [0x632C]


def test_buffer_flattens_palette_transparency():
    im = Image.new("P", (1, 2))
    im.putpalette([255, 0, 0, 0, 255, 0] + [0, 0, 0] * 254)
    im.putpixel((0, 0), 0)  # red, marked transparent
    im.putpixel((0, 1), 1)  # green, opaque
    im.info["transparency"] = 0
    assert list(rgb888_to_565_buffer(im)) == [0x0000, 0x07E0]


def test_buffer_flattens_color_key():
    # PNG-style RGB color key (info["transparency"] as an RGB triple)
    im = Image.new("RGB", (1, 2))
    im.putpixel((0, 0), (10, 20, 30))  # keyed -> transparent
    im.putpixel((0, 1), (255, 255, 255))
    im.info["transparency"] = (10, 20, 30)
    assert list(rgb888_to_565_buffer(im)) == [0x0000, 0xFFFF]


def test_buffer_grayscale_without_alpha_passes_through():
    im = Image.new("L", (1, 1), 128)  # no alpha: plain gray, no flattening
    assert list(rgb888_to_565_buffer(im)) == [0x8410]


def test_fit_image_preserves_aspect_and_never_enlarges():
    im = Image.new("RGB", (800, 480))
    scaled = fit_image(im, 480, 800)
    assert scaled.size == (480, 288)  # aspect preserved
    assert im.size == (800, 480)  # original untouched
    small = Image.new("RGB", (100, 50))
    assert fit_image(small, 480, 800).size == (100, 50)  # not enlarged
