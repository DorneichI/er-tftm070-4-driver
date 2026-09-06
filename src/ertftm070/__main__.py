"""ertftm070 command-line tool.

    ertftm070 selftest          bus self-test (device ID + round-trip)
    ertftm070 bars              8 full-height color bars
    ertftm070 fill F800         fill the whole screen with one RGB565 color
    ertftm070 image photo.png   show an image (needs Pillow)
    ertftm070 gramcheck         write known pixels, read them back from GRAM

The drawing commands (bars/fill/image) hold the picture on screen until
Ctrl+C or SIGTERM (both turn the backlight off); pass --once to exit
immediately instead.  The diagnostics (selftest/gramcheck) run once and
exit 0 on success, 1 on failure.  Global options (--init, --rotation,
--once) are accepted before or after the subcommand.  Works identically
as ``python -m ertftm070``.
"""
from __future__ import annotations

import argparse
import logging
import signal
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

# Commands that leave a picture on the panel: hold until interrupted.
# The diagnostics are one-shot; holding them open would defeat their
# exit codes.
_HOLD_COMMANDS = frozenset({"bars", "fill", "image"})


def _add_global_options(
    parser: argparse.ArgumentParser, *, suppress_defaults: bool = False
) -> None:
    """The options valid in front of the subcommand AND behind it.

    Registered on the top-level parser (with real defaults) and on every
    subparser (via a shared parent).  On the subparser level the defaults
    are SUPPRESSED: argparse versions before 3.12 re-parse the remaining
    args into a fresh namespace and copy it over the outer one, which
    would clobber a ``--rotation 90`` given *before* the subcommand with
    the subparser's default of 0.  With no default to apply, the
    subparser only writes what the user actually typed there — so
    ``ertftm070 bars --rotation 90`` and ``ertftm070 --rotation 90 bars``
    behave identically on every supported Python.
    """
    parser.add_argument(
        "--init",
        choices=sorted(_TABLES),
        default=argparse.SUPPRESS if suppress_defaults else "utft",
        help="init table (default: utft — the one verified on hardware)",
    )
    parser.add_argument(
        "--rotation",
        type=int,
        choices=[0, 90, 180, 270],
        default=argparse.SUPPRESS if suppress_defaults else 0,
        help="logical rotation (default: 0)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        default=argparse.SUPPRESS if suppress_defaults else False,
        help="exit right after drawing instead of keeping the display on "
        "until Ctrl+C (turns the backlight off)",
    )


def _rgb565_hex(value: str) -> int:
    """argparse type: a hex string that must be a 16-bit RGB565 color."""
    try:
        color = int(value, 16)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a hex number: {value!r}") from None
    if not 0 <= color <= 0xFFFF:
        raise argparse.ArgumentTypeError(
            f"{value!r} is outside 0..FFFF — RGB565 colors are 16 bits"
        )
    return color


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ertftm070",
        description="Drive an ER-TFTM070-4V2.1 (SSD1963) display on a Raspberry Pi.",
    )
    _add_global_options(parser)  # before the subcommand
    subparsers = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    _add_global_options(common, suppress_defaults=True)  # ... and after it

    subparsers.add_parser(
        "selftest", parents=[common],
        help="bus self-test (device ID + register round-trip)",
    )
    subparsers.add_parser(
        "bars", parents=[common], help="draw 8 full-height color bars"
    )

    fill = subparsers.add_parser(
        "fill", parents=[common], help="fill the whole screen with one color"
    )
    fill.add_argument(
        "color", type=_rgb565_hex, help="RGB565 hex, e.g. F800 for red (0..FFFF)"
    )

    image = subparsers.add_parser(
        "image", parents=[common], help="show an image file (needs Pillow)"
    )
    image.add_argument("path", help="path to a PNG/JPEG/… file")
    image.add_argument("--x", type=int, default=0, help="top-left x (default 0)")
    image.add_argument("--y", type=int, default=0, help="top-left y (default 0)")

    subparsers.add_parser(
        "gramcheck", parents=[common],
        help="write known pixels, read them back from GRAM",
    )
    return parser


def _make_display(args: argparse.Namespace) -> Display:
    return Display(init_table=_TABLES[args.init], rotation=args.rotation)


def run(args: argparse.Namespace, display: Display | None = None) -> int:
    """Execute parsed args.  ``display`` is injectable for tests."""
    own = display is None
    if own:
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
            t0 = time.time()
            display.fill(args.color)  # already validated by _rgb565_hex
            print("filled in %.1f s" % (time.time() - t0))
            return 0

        if args.command == "image":
            try:
                from PIL import Image  # lazy — needs the pillow extra
            except ImportError:
                print(
                    "the image command needs Pillow: "
                    "pip install 'ertftm070[Pillow]'",
                    file=sys.stderr,
                )
                return 2
            try:
                img = Image.open(args.path)
            except OSError as exc:
                print(f"cannot open {args.path!r}: {exc}", file=sys.stderr)
                return 2
            t0 = time.time()
            display.image(img, args.x, args.y, fit=True)
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
        if own:
            display.close()


def _sigterm(_signum, _frame) -> None:
    """SIGTERM (kill, systemd stop) unwinds like Ctrl-C: the finally
    blocks close the display and the process exits."""
    raise KeyboardInterrupt


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    signal.signal(signal.SIGTERM, _sigterm)  # together with Ctrl-C: exit cleanly
    code = 0
    display = None
    try:
        display = _make_display(args)
        display.open()
        code = run(args, display)
    except Ertftm070Error as exc:
        print(f"error: {exc}", file=sys.stderr)
        code = 1
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        code = 2
    except KeyboardInterrupt:
        print()  # user aborted mid-command; nothing to report
    else:
        if not args.once and args.command in _HOLD_COMMANDS:
            # keep the picture on screen until the user is done looking;
            # Ctrl-C (or SIGTERM) turns the backlight off and exits
            print("display on — press Ctrl+C to exit")
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                print()
    finally:
        if display is not None:
            display.close()
    sys.exit(code)


if __name__ == "__main__":
    main()
