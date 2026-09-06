"""Show an image file on the display.

Run: python3 examples/show_image.py photo.png [--x N] [--y N]
Needs the pillow extra: pip install ertftm070[Pillow]
Images larger than the screen are scaled down (aspect preserved); the
picture stays on screen until you press Enter (or Ctrl+C).
"""
import argparse
import sys

try:
    from PIL import Image
except ImportError:
    sys.exit("this example needs the pillow extra: pip install 'ertftm070[Pillow]'")

from ertftm070 import Display


def main() -> None:
    parser = argparse.ArgumentParser(description="Show an image on the ER-TFTM070-4")
    parser.add_argument("path", help="path to a PNG/JPEG/... file")
    parser.add_argument("--x", type=int, default=0, help="top-left x (default 0)")
    parser.add_argument("--y", type=int, default=0, help="top-left y (default 0)")
    args = parser.parse_args()

    with Display() as lcd:
        if args.x >= lcd.width or args.y >= lcd.height:
            parser.error(f"position ({args.x},{args.y}) is outside the display")
        try:
            img = Image.open(args.path)
        except OSError as exc:
            parser.error(f"cannot open {args.path!r}: {exc}")
        lcd.image(img, args.x, args.y, fit=True)
        print(f"showing {img.size[0]}x{img.size[1]} - press Enter to turn it off...")
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            pass


if __name__ == "__main__":
    main()
