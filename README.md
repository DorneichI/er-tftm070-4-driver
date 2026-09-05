# ertftm070

**A Python driver for the EastRising ER-TFTM070-4V2.1** — 7.0" TFT,
800×480, SSD1963 controller — on a Raspberry Pi Zero/1/2/3/4.

`pip install` it, open the display in three lines, blit Pillow images in
~0.6 s. Verified on hardware (Pi Zero W, Raspberry Pi OS Trixie,
September 2026); zero runtime dependencies in the core.

[![PyPI](https://img.shields.io/pypi/v/ertftm070)](https://pypi.org/project/ertftm070/)
[![Python](https://img.shields.io/pypi/pyversions/ertftm070)](https://pypi.org/project/ertftm070/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![CI](https://github.com/DorneichI/er-tftm070-4-driver/actions/workflows/ci.yml/badge.svg)](https://github.com/DorneichI/er-tftm070-4-driver/actions/workflows/ci.yml)

---

## Install

```bash
pip install ertftm070           # the core
pip install ertftm070[Pillow]   # + Pillow, for the image API
```

`pip` compiles a tiny C extension at install time (the fast pixel path,
~1400× faster than Python bit-banging). No compiler on the machine? The
install still succeeds and a pure-Python fallback takes over — slightly
slower, fully functional, with a warning telling you so.

## Quick start

Wire the display as described in [docs/WIRING.md](docs/WIRING.md), then:

```python
from ertftm070 import Display

with Display() as lcd:                 # opens the bus, inits, backlight on
    lcd.fill(0xF800)                  # red screen, ~0.6 s

    lcd.fill_rect(10, 10, 100, 50, 0x07E0)   # partial update
    lcd.image(pil_image, x=20, y=20)         # blit a Pillow image
    lcd.rotation = 90
    lcd.backlight(False)
    lcd.sleep()
    lcd.wake()

# leaving the with-block turns the backlight off and releases the GPIOs
```

Command-line equivalent:

```bash
ertftm070 selftest        # verify the wiring against the SSD1963 itself
ertftm070 bars            # 8 color bars, the classic test pattern
ertftm070 fill F800       # solid fill
ertftm070 image photo.png # show an image
ertftm070 gramcheck       # write + read back GRAM (pixel-path proof)
```

Or from a source checkout: `python3 examples/color_bars.py`,
`python3 examples/show_image.py photo.png`,
`python3 examples/small_demo.py`.

## The API

| What | How |
|---|---|
| Fill screen / rectangle | `lcd.fill(rgb565)` · `lcd.fill_rect(x, y, w, h, rgb565)` |
| Single pixel | `lcd.set_pixel(x, y, rgb565)` |
| Images | `lcd.image(pil_image, x=0, y=0)` — draw text/shapes/UI in Pillow first |
| Rotation | `lcd.rotation = 0 / 90 / 180 / 270` |
| Backlight | `lcd.backlight(True / False)` |
| Power | `lcd.sleep()` · `lcd.wake()` |
| Diagnostics | `lcd.selftest()` · `lcd.gramcheck()` (both return bool) |
| Colors | `rgb565(r, g, b)` → 16-bit 565 word |

Everything is configurable: `Display(pins=…, init_table=…, rotation=…)`.
Defaults are the hardware-verified values. Full reference in
[docs/API.md](docs/API.md).

## The one fact that changes everything

**This display board is strapped for 16-bit 8080, not 8-bit.**

The board's R3/R4 jumpers select *8080-vs-6800* — **not** the bus width.
Most public example code targets the 8-bit configuration, and nothing
will look right until you wire DB8–DB15 and write one 16-bit pixel per
write strobe. This driver does that out of the box.

The sneaky part: the SSD1963 does register access over D[7:0] *regardless
of bus width*, so every register self-test passes in both modes. Only the
pixel path reveals the truth — a horizontally doubled, striped image.
That's exactly what `lcd.gramcheck()` is for. The full story:
[docs/LESSONS.md](docs/LESSONS.md).

## Supported hardware

| Machine | Status |
|---|---|
| Raspberry Pi Zero W | ✅ verified — the reference platform |
| Raspberry Pi Zero / 1 / 2 / 3 / 4 | expected to work (same GPIO block); strobe timing self-calibrates per CPU |
| Raspberry Pi 5 | ❌ not supported — RP1 GPIO controller, different registers. A libgpiod backend is on the roadmap |
| Anything else | imports fine; `Display()` raises `NotOnRaspberryPi` with an explanation |

The display itself is plain 16-bit 8080 — Arduinos, ESP32s and friends
drive it too (that's where the init tables came from). Only this Python
package is Pi-specific.

## Touch

The V2.1 board ships with a **capacitive touch panel mounted by default**
and its controller (FocalTech FT5206-family) broken out on the display
connector: pins 33–37 carry `/RST`, `SCL`, `SDA`, `INT`, `WAKE` (pin 38
is the resistive-pen ground, unused). It speaks I²C, so the Pi can talk
to it with **no extra hardware** — just SCL/SDA to Pi GPIO 3/2, `/RST`
and `INT` to any two free GPIOs, and I²C enabled.

Touch support (`ertftm070.touch`) is the first roadmap item; v1.0 is
display-only. See [docs/WIRING.md](docs/WIRING.md) for the pin details.

## Roadmap

- **Touch input** — userspace driver for the onboard FT5206 over I²C
  (or kernel `ft5x06` driver + device-tree overlay as an alternative)
- Backlight dimming — software PWM on the backlight pin
- Hardware vertical scroll (SSD1963 `0x33`/`0x37`)
- GRAM screenshots — read the framebuffer back into a PIL image
- Pi 5 support via a libgpiod backend

Text, shapes and UI widgets are deliberately **not** part of the driver —
draw them in Pillow and blit with `lcd.image()`. It's the same pattern
at 60× less code.

## Performance

Full-screen updates (800×480×16-bit):

- **~0.6 s** with the C extension (direct `/dev/gpiomem` register
  writes, no syscalls in the hot loop, ~640k px/s)
- **~3 s** with the pure-Python fallback
- (For context: RPi.GPIO bit-banging manages ~460 px/s — the C path is
  ~1400× faster, which is why the extension exists.)

`ertftm070.BACKEND` tells you which path is active (`"fast"`/`"slow"`);
`ERTFTM070_FORCE_SLOW=1` forces the fallback.

## Documentation

| Doc | What it is |
|---|---|
| [docs/WIRING.md](docs/WIRING.md) | The full wiring table — one table, every pin |
| [docs/INIT-SEQUENCE.md](docs/INIT-SEQUENCE.md) | The exact register sequence and why each register matters |
| [docs/LESSONS.md](docs/LESSONS.md) | Everything learned the hard way — read this before debugging |
| [docs/API.md](docs/API.md) | API reference |
| [docs/RELEASING.md](docs/RELEASING.md) | How releases are cut and published |
| [legacy/](legacy/) | The original verified bring-up scripts (unchanged baseline) |

## Development

```bash
pip install -e ".[dev]"
pytest          # 55 tests, no hardware needed (a fake bus stands in)
ruff check src tests examples
```

CI runs lint + tests + builds on every push and publishes to PyPI from
version tags (see [docs/RELEASING.md](docs/RELEASING.md)).

## License

[MIT](LICENSE) © 2026 Immanuel Dorneich
