"""Color helpers: RGB888 <-> RGB565.

The SSD1963 is configured for 16 bpp (``0x3A = 0x50``) with 565 format 1
(``0xF0 = 0x03``): one pixel is one 16-bit word — red in bits 11..15,
green in bits 5..10, blue in bits 0..4 — with bit 0 on DB0.

The red/blue *swap* you might expect here is deliberately NOT done in
software: the controller's address-mode register (``0x36 = 0x08``, BGR)
handles it for this panel.  See ``docs/INIT-SEQUENCE.md``.
"""
from __future__ import annotations

from array import array
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image


def rgb565(r: int, g: int, b: int) -> int:
    """Pack 8-bit RGB into a 16-bit 565 word (0..65535).

    >>> rgb565(255, 0, 0)
    63488
    """
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def rgb888_to_565_buffer(image: Image.Image) -> array:
    """Convert a Pillow image to an RGB565 word buffer.

    Returns an ``array('H')`` of ``width * height`` 16-bit words in
    row-major order, ready for :meth:`ertftm070.Display.image`.
    The image is converted to RGB first, so anything Pillow can open
    (PNG, JPEG, GIF, …) works.  Alpha is flattened onto black.
    """
    im = image.convert("RGB")
    w, h = im.size
    buf = array("H", [0]) * (w * h)
    px = im.load()
    i = 0
    for y in range(h):
        for x in range(w):
            r, g, b = px[x, y]
            buf[i] = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
            i += 1
    return buf
