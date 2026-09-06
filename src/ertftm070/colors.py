"""Color helpers: RGB888 <-> RGB565.

The SSD1963 is configured for 16 bpp (``0x3A = 0x50``) with 565 format 1
(``0xF0 = 0x03``): one pixel is one 16-bit word — red in bits 11..15,
green in bits 5..10, blue in bits 0..4 — with bit 0 on DB0.

The red/blue *swap* you might expect here is deliberately NOT done in
software: the controller's address-mode register (``0x36 = 0x08``, BGR)
handles it for this panel.  See ``docs/INIT-SEQUENCE.md``.
"""
from __future__ import annotations

import sys
from array import array
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image

# Point LUTs for the 565 packing below: hi = (r & 0xF8) | (g >> 5),
# lo = ((g & 0x1C) << 3) | (b >> 3) — the same truncation rgb565() does,
# split into per-plane 8-bit lookups so Pillow's C loop does the work.
_HI_R = [i & 0xF8 for i in range(256)]
_HI_G = [i >> 5 for i in range(256)]
_LO_G = [(i & 0x1C) << 3 for i in range(256)]
_LO_B = [i >> 3 for i in range(256)]


def rgb565(r: int, g: int, b: int) -> int:
    """Pack 8-bit RGB into a 16-bit 565 word (0..65535).

    Each channel must be in 0..255 (as Pillow delivers); anything else
    raises ``ValueError`` instead of silently wrapping into a different
    color.

    >>> rgb565(255, 0, 0)
    63488
    """
    if not (0 <= r <= 255 and 0 <= g <= 255 and 0 <= b <= 255):
        raise ValueError(f"RGB channels must be 0..255, got ({r}, {g}, {b})")
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def _has_alpha(image: Image.Image) -> bool:
    """True if the image carries transparency of any kind."""
    return "A" in image.mode or "transparency" in image.info


def _flatten_alpha(image: Image.Image) -> Image.Image:
    """Composite an image's alpha onto black (no-op without alpha).

    ``convert("RGB")`` *drops* the alpha band instead of blending it, so
    semi-transparent pixels would otherwise show at full intensity.
    Covers RGBA/LA/PA, paletted images with a transparency index, and
    PNG-style color-key transparency in RGB/L (Pillow stores those as
    ``image.info["transparency"]`` and honors them on ``convert("RGBA")``).
    """
    if _has_alpha(image):
        from PIL import Image as _PIL  # lazy — pillow extra only

        rgba = image.convert("RGBA")
        black = _PIL.new("RGBA", rgba.size, (0, 0, 0, 255))
        return _PIL.alpha_composite(black, rgba)
    return image


def rgb888_to_565_buffer(image: Image.Image) -> array:
    """Convert a Pillow image to an RGB565 word buffer.

    Returns an ``array('H')`` of ``width * height`` 16-bit words in
    row-major order, ready for :meth:`ertftm070.Display.image`.
    Anything Pillow can open (PNG, JPEG, GIF, …) works.  Alpha is
    flattened onto black (RGBA, LA, and every transparency flavor).
    Packing runs as Pillow point LUTs — tens of milliseconds per full
    screen, not seconds.

    Requires the ``Pillow`` extra, Pillow >= 9.1.
    """
    from PIL import ImageChops  # lazy — pillow extra only

    im = _flatten_alpha(image).convert("RGB")
    w, h = im.size
    r, g, b = im.split()
    hi = ImageChops.add(r.point(_HI_R), g.point(_HI_G))
    lo = ImageChops.add(g.point(_LO_G), b.point(_LO_B))
    buf = bytearray(2 * w * h)
    buf[0::2] = lo.tobytes()  # low byte first: bit 0 = DB0
    buf[1::2] = hi.tobytes()
    out = array("H")
    out.frombytes(buf)
    if sys.byteorder != "little":
        out.byteswap()
    return out


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
