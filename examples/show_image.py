"""Show an image file on the display.

Run: python3 examples/show_image.py photo.png [--x N] [--y N]
Needs the pillow extra: pip install ertftm070[Pillow]
"""
import argparse

from PIL import Image

from ertftm070 import Display


def main() -> None:
    parser = argparse.ArgumentParser(description="Show an image on the ER-TFTM070-4")
    parser.add_argument("path", help="path to a PNG/JPEG/... file")
    parser.add_argument("--x", type=int, default=0, help="top-left x (default 0)")
    parser.add_argument("--y", type=int, default=0, help="top-left y (default 0)")
    args = parser.parse_args()

    with Display() as lcd:
        lcd.image(Image.open(args.path), args.x, args.y)


if __name__ == "__main__":
    main()
