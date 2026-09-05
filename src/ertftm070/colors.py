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


def fit_image(image: Image.Image, box_w: int, box_h: int) -> Image.Image:
    """Return a copy of ``image`` scaled to fit inside ``box_w x box_h``.

    Aspect ratio is preserved; images smaller than the box are left as
    they are (never enlarged).  Used by :meth:`ertftm070.Display.image`
    with ``fit=True`` — e.g. after a rotation swaps the screen's
    dimensions.  The default BICUBIC resample keeps this compatible with
    every Pillow version.
    """
    img = image.copy()  # thumbnail() resizes in place
    img.thumbnail((box_w, box_h))
    return img


def rotate_image(image: Image.Image, rotation: int) -> Image.Image:
    """Rotate an image's *content* to match a software display rotation.

    The SSD1963's MADCTL flip bits scramble the memory-write pointer on
    this board, so ertftm070 rotates in software: the panel stays in its
    native orientation and images are pre-rotated here (an exact pixel
    permutation via ``transpose()`` — no resampling, no blur) before
    being blitted into the mapped window.  The transpose directions were
    derived from the logical→controller mappings in ``display.py`` and
    verified against GRAM read-back on hardware.

    ``rotation=0`` returns the image unchanged (no copy).
    """
    if rotation == 0:
        return image
    from PIL import Image as _PIL  # lazy — pillow extra only for image work

    transposes = {
        90: _PIL.Transpose.ROTATE_90,  # CCW, matches (x,y) -> (y, 479-x)
        180: _PIL.Transpose.ROTATE_180,
        270: _PIL.Transpose.ROTATE_270,  # CW, matches (x,y) -> (799-y, x)
    }
    return image.transpose(transposes[rotation])
