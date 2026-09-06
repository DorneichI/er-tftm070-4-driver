"""Scrolling bar: tear-free with TE, sheared without — compare them.

A 40-pixel column of gradient scrolls sideways across the panel.
Without vsync each row lands mid-scan wherever the panel happens to
be, so the column shears diagonally while it moves; with vsync every
row is placed inside vertical blanking and the column stays straight.

Run: python3 examples/vsync_demo.py --vsync off   (then try `on`)
Needs: the pillow extra and the TE wire (panel pin 8 -> GPIO14).
"""
import argparse
import sys

try:
    from PIL import Image
except ImportError:
    sys.exit("this example needs the pillow extra: pip install 'ertftm070[Pillow]'")

from ertftm070 import Display

COLUMN_WIDTH = 40


def column() -> Image.Image:
    """A 40x480 gradient column — content that tears visibly when it moves."""
    im = Image.new("RGB", (COLUMN_WIDTH, 480))
    px = im.load()
    for yy in range(480):
        for xx in range(COLUMN_WIDTH):
            px[xx, yy] = (xx * 6, 255 - yy // 2, yy // 2)
    return im


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--vsync", choices=["on", "off"], default="off",
        help="pace each row into vertical blanking (default: off)",
    )
    args = parser.parse_args()
    vsync = args.vsync == "on"

    with Display() as lcd:
        lcd.fill(0x0000)
        x = 0
        step = 4
        print(f"scrolling with vsync={'on' if vsync else 'off'} — Ctrl+C exits")
        while True:
            # erase the old position: background-colored column
            lcd.fill_rect(x, 0, COLUMN_WIDTH, 480, 0x0000, vsync=vsync)
            x = (x + step) % (lcd.width - COLUMN_WIDTH)
            lcd.image(column(), x, 0, vsync=vsync)


if __name__ == "__main__":
    main()
