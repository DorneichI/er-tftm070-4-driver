"""End-to-end demo: self-test, solid fills, a gradient, and text.

Run: python3 examples/small_demo.py
Needs the pillow extra for the gradient/text part.
"""
import sys
import time

try:
    from PIL import Image, ImageDraw
except ImportError:
    sys.exit("this example needs the pillow extra: pip install 'ertftm070[Pillow]'")

from ertftm070 import Display, rgb565


def make_gradient_text(width: int, height: int) -> Image.Image:
    """A horizontal gradient with a caption — drawn with plain Pillow."""
    im = Image.new("RGB", (width, height))
    px = im.load()
    for x in range(width):
        color = (x * 255 // (width - 1), 128, 255 - x * 255 // (width - 1))
        for y in range(0, height, 2):  # every 2nd row keeps it quick
            px[x, y] = color
            px[x, y + 1] = color
    draw = ImageDraw.Draw(im)
    draw.rectangle((0, 0, width - 1, 60), fill=(0, 0, 0))
    draw.text((20, 15), "Hello from ertftm070!", fill=(255, 255, 255))
    return im


with Display() as lcd:
    print("self-test:", "PASSED" if lcd.selftest() else "FAILED")

    for name, color in (("red", rgb565(255, 0, 0)), ("green", rgb565(0, 255, 0))):
        print("filling", name)
        lcd.fill(color)
        time.sleep(1)

    print("blitting gradient + text")
    lcd.image(make_gradient_text(lcd.width, lcd.height))

    print("backlight off 2 s, on again")
    lcd.backlight(False)
    time.sleep(2)
    lcd.backlight(True)

    print("sleep 2 s, wake")
    lcd.sleep()
    time.sleep(2)
    lcd.wake()

print("done (display closed, backlight off)")
