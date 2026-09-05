"""ertftm070 command-line tool.

    ertftm070 selftest          bus self-test (device ID + round-trip)
    ertftm070 bars              8 full-height color bars
    ertftm070 fill F800         fill the whole screen with one RGB565 color
    ertftm070 image photo.png   show an image (needs Pillow)
    ertftm070 gramcheck         write known pixels, read them back from GRAM

Works identically as ``python -m ertftm070``.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time

from . import init
from .display import Display
from .errors import Ertftm070Error

BARS = [
    ("black", 0x0000),
    ("red", 0xF800),
    ("green", 0x07E0),
    ("blue", 0x001F),
    ("white", 0xFFFF),
    ("yellow", 0xFFE0),
    ("cyan", 0x07FF),
    ("magenta", 0xF81F),
]

_TABLES = {"utft": init.INIT_UTFT, "alt": init.INIT_ALT, "bd": init.INIT_BD}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ertftm070",
        description="Drive an ER-TFTM070-4V2.1 (SSD1963) display on a Raspberry Pi.",
    )
    parser.add_argument(
        "--init",
        choices=sorted(_TABLES),
        default="utft",
        help="init table (default: utft — the one verified on hardware)",
    )
    parser.add_argument(
        "--rotation",
        type=int,
        choices=[0, 90, 180, 270],
        default=0,
        help="logical rotation (default: 0)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("selftest", help="bus self-test (device ID + register round-trip)")

    sub.add_parser("bars", help="draw 8 full-height color bars")

    fill = sub.add_parser("fill", help="fill the whole screen with one color")
    fill.add_argument("color", help="RGB565 hex, e.g. F800 for red")

    image = sub.add_parser("image", help="show an image file (needs Pillow)")
    image.add_argument("path", help="path to a PNG/JPEG/… file")
    image.add_argument("--x", type=int, default=0, help="top-left x (default 0)")
    image.add_argument("--y", type=int, default=0, help="top-left y (default 0)")

    sub.add_parser("gramcheck", help="write known pixels, read them back from GRAM")
    return parser


def _make_display(args: argparse.Namespace) -> Display:
    return Display(init_table=_TABLES[args.init], rotation=args.rotation)


def run(args: argparse.Namespace, display: Display | None = None) -> int:
    """Execute parsed args.  ``display`` is injectable for tests."""
    own_display = display is None
    if display is None:
        display = _make_display(args)
        display.open()
    try:
        if args.command == "selftest":
            return 0 if display.selftest() else 1

        if args.command == "bars":
            t0 = time.time()
            w = display.width // len(BARS)
            for i, (_name, color) in enumerate(BARS):
                display.fill_rect(i * w, 0, w, display.height, color)
            print("8 color bars drawn in %.1f s" % (time.time() - t0))
            print("Expected left->right: {}".format(", ".join(n for n, _ in BARS)))
            return 0

        if args.command == "fill":
            color = int(args.color, 16)
            t0 = time.time()
            display.fill(color)
            print("filled in %.1f s" % (time.time() - t0))
            return 0

        if args.command == "image":
            from PIL import Image  # lazy — needs the pillow extra

            img = Image.open(args.path)
            t0 = time.time()
            display.image(img, args.x, args.y)
            print(
                f"{img.size[0]}x{img.size[1]} image blitted in "
                f"{time.time() - t0:.1f} s"
            )
            return 0

        if args.command == "gramcheck":
            return 0 if display.gramcheck() else 1

        print("no command given — see ertftm070 --help", file=sys.stderr)
        return 2
    finally:
        if own_display:
            display.close()


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    try:
        sys.exit(run(args))
    except Ertftm070Error as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
