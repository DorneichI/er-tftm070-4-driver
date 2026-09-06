"""Finger-paint on the panel — the most visceral proof of the touch path.

Draw with one finger; a second finger adds a labeled circle showing its
track ID (multi-touch).  Only the changed region is re-blitted, so the
ink follows your finger.

Run: python3 examples/touch_paint.py
Needs: the pillow extra, the touch wires (docs/WIRING.md), I2C enabled.
"""
import sys

try:
    from PIL import Image, ImageDraw
except ImportError:
    sys.exit("this example needs the pillow extra: pip install 'ertftm070[Pillow]'")

from ertftm070 import Display
from ertftm070.touch import Touch


def canvas(draw: ImageDraw.ImageDraw) -> None:
    """The background: dark, with a hint of where the panel edges are."""
    draw.rectangle((0, 0, 799, 479), fill=(16, 16, 24))
    draw.rectangle((0, 0, 799, 479), outline=(80, 80, 90))


with Display() as lcd, Touch(lcd.bus) as touch:
    img = Image.new("RGB", (lcd.width, lcd.height))
    draw = ImageDraw.Draw(img)
    canvas(draw)
    lcd.image(img)
    print("touch paint — draw with one finger, press with two for IDs (Ctrl+C exits)")

    prev = []  # last touches as (x, y, id) in logical coordinates
    while True:
        points = touch.read()
        mapped = [touch.calibration.map(p) for p in points]
        # erase the previous marks, then draw the new ones
        for x, y, _p_id in prev:
            draw.rectangle((x - 10, y - 10, x + 10, y + 10), fill=(16, 16, 24))
        # the erase boxes also wipe the 1-px border under the finger
        # trail — restore it (the blit region covers these spots)
        draw.rectangle((0, 0, 799, 479), outline=(80, 80, 90))
        for p in mapped:
            if len(points) > 1:
                draw.ellipse(
                    (p.x - 9, p.y - 9, p.x + 9, p.y + 9), outline=(255, 255, 0)
                )
                draw.text((p.x + 11, p.y - 6), str(p.id), fill=(255, 255, 0))
            else:
                draw.ellipse((p.x - 5, p.y - 5, p.x + 5, p.y + 5), fill=(0, 200, 255))
        old = prev
        prev = [(p.x, p.y, p.id) for p in mapped]

        # blit only the union of the erased and drawn areas (small, fast)
        spots = [(x, y) for x, y, _id in old] + [(p.x, p.y) for p in mapped]
        if spots:
            xs = [x for x, _y in spots]
            ys = [y for _x, y in spots]
            x0, x1 = max(min(xs) - 12, 0), min(max(xs) + 12, lcd.width - 1)
            y0, y1 = max(min(ys) - 12, 0), min(max(ys) + 12, lcd.height - 1)
            region = img.crop((x0, y0, x1 + 1, y1 + 1))
            lcd.image(region, x0, y0)
