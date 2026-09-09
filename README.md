# ertftm070

**A Python driver for the EastRising ER-TFTM070-4V2.1** — 7.0" TFT,
800×480, SSD1963 controller — on a Raspberry Pi Zero/1/2/3/4.

```bash
pip install ertftm070
```

Open the display in three lines, fill the screen in ~0.6 s, blit Pillow
images, read the touch panel later (roadmap). Verified on hardware
(Pi Zero W, Raspberry Pi OS, September 2026); zero runtime dependencies
in the core.

[![PyPI](https://img.shields.io/pypi/v/ertftm070)](https://pypi.org/project/ertftm070/)
[![Python](https://img.shields.io/pypi/pyversions/ertftm070)](https://pypi.org/project/ertftm070/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![CI](https://github.com/DorneichI/er-tftm070-4-driver/actions/workflows/ci.yml/badge.svg)](https://github.com/DorneichI/er-tftm070-4-driver/actions/workflows/ci.yml)

> # ⚠️ THIS PROJECT IS COMPLETELY VIBECODED ⚠️
>
> **No human sat down and wrote this codebase. It was written by
> DeepSeek (the AI model, running in the Claude Code CLI) in a
> vibe-coding session, with a human in the loop whose job was watching
> the screen, describing what was wrong, and demanding better.**
>
> **The good part:** it genuinely works. Every feature was tested on
> real hardware — the display was on a Raspberry Pi Zero W for every
> release candidate, and the human confirmed the pixels with their own
> eyes. 89 automated tests pass, CI is green, and the ugly hardware
> quirks are documented instead of hidden.
>
> **The honest part:** no one has audited every line. There may be bugs
> nobody has stepped on yet, design choices a real engineer would
> question, and comments that overestimate their own cleverness. Treat
> it accordingly: check the code before you trust your life (or your
> graduation project) to it.
>
> MIT license, no warranty, no guarantees. If your display does
> something you don't like, [open an issue](https://github.com/DorneichI/er-tftm070-4-driver/issues)
> and a robot will be with you eventually — whenever its human feels
> like it.

---

## Install

```bash
pip install ertftm070           # the core
pip install ertftm070[Pillow]   # + Pillow, for the image API
```

`pip` compiles a tiny C extension at install time (the fast pixel path,
~17× faster than the pure-Python fallback; see the timing table below).
No compiler on the machine?
The install still succeeds and the fallback takes over — slower, fully
functional, with a warning telling you so.

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

    pclk_khz, hz = lcd.refresh_rate()        # measured clocks (needs the TE wire)

# leaving the with-block turns the backlight off and releases the GPIOs
```

Command-line equivalent, no code required:

```bash
ertftm070 selftest        # verify the wiring against the SSD1963 itself
ertftm070 bars            # 8 color bars, the classic test pattern
ertftm070 fill F800       # solid fill
ertftm070 image photo.png # show an image
ertftm070 gramcheck       # write + read back GRAM (pixel-path proof)
ertftm070 refresh         # measure PCLK + frame rate — names the crystal (needs TE)
ertftm070 touch-test      # stream touches until Ctrl+C (needs the touch wires)
```

The picture drawn by `bars`/`fill`/`image` stays on screen until Ctrl+C
(which turns the backlight off and releases the GPIOs); pass `--once`
to exit immediately instead. `touch-test` also holds until Ctrl+C. The
diagnostics run once and exit with their verdict (0 = passed). Global
options like `--once` and `--rotation` work before or after the
subcommand.

Or from a source checkout: `python3 examples/color_bars.py`,
`python3 examples/show_image.py photo.png`,
`python3 examples/small_demo.py`.

## Simulator (no hardware needed)

Develop and demo on a laptop, no Raspberry Pi, no panel — the driver
renders into a browser instead:

```bash
pip install 'ertftm070[sim]'              # the websockets extra
ERTFTM070_DISPLAY=sim ertftm070 bars      # open http://localhost:8000/
ERTFTM070_DISPLAY=sim python3 examples/touch_paint.py   # draw with the mouse
```

The simulated backend speaks the same `Bus` protocol, so dashboard and
example code runs **unchanged**: draw calls land in an 800×480
framebuffer streamed row-by-row to the browser (raw RGB565, low
latency), the mouse is one touch and phones/tablets on the LAN inject
multi-touch (up to the FT5x06's five points), and the TE line is
emulated at the panel's real ~53.7 Hz so `vsync=`, `refresh_rate()` and
`wait_touch()` behave. `selftest`/`gramcheck` report failure in sim
(register read-back is not simulated), and `lcd.sleep()` does not dim
the picture — only the flow is exercised.

The server binds `0.0.0.0` by default, so the browser does not have to
be on the same machine: run the app on the Pi and open
`http://raspberry.local:8000/` on the Mac. Host/port override with
`ERTFTM070_SIM_HOST`/`ERTFTM070_SIM_PORT`; the env var is read once at
import (set it before starting Python), and an explicit
`Display(backend=…)` still wins. Full details in
[docs/API.md](docs/API.md).

## The API

| What | How |
|---|---|
| Fill screen / rectangle | `lcd.fill(rgb565)` · `lcd.fill_rect(x, y, w, h, rgb565)` |
| Single pixel | `lcd.set_pixel(x, y, rgb565)` |
| Images | `lcd.image(pil_image, x=0, y=0, fit=False)` — draw text/shapes/UI in Pillow first; `fit=True` scales to the (possibly rotated) screen |
| Rotation | `lcd.rotation = 0 / 90 / 180 / 270` |
| Backlight | `lcd.backlight(True / False)` |
| Power | `lcd.sleep()` · `lcd.wake()` |
| Tear-free updates | `vsync=True` on `fill_rect`/`image` — rows paced into vertical blanking (needs the TE wire; opt-in, see the docstring) |
| Skip unchanged pixels | diffing is on by default — only changed spans are written; `force=True` on any draw call · `lcd.invalidate()` |
| Measured clocks | `lcd.refresh_rate()` → `(pclk_khz, hz)` · `lcd.vsync_wait()` |
| Touch | `Touch(lcd.bus)` — `read(mapped=True)`, `wait_touch()`, `reset()` |
| Diagnostics | `lcd.selftest()` · `lcd.gramcheck()` (both return bool) |
| Colors | `rgb565(r, g, b)` → 16-bit 565 word |

Everything is configurable: `Display(pins=…, init_table=…, rotation=…,
write_passes=…)`. Defaults are the hardware-verified values. Full
reference in [docs/API.md](docs/API.md).

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

## Performance

Full-screen fill (800×480×16-bit), measured on a Pi Zero W:

- **~0.6 s** with the C extension (direct `/dev/gpiomem` register writes,
  no syscalls in the hot loop, one C call per row)
- **~10 s** with the pure-Python fallback
- (For context: RPi.GPIO bit-banging manages ~460 px/s — the C path is
  ~1400× faster, which is why the extension exists.)

`ertftm070.BACKEND` tells you which path is active (`"fast"`/`"slow"`);
`ERTFTM070_FORCE_SLOW=1` forces the fallback. If a row ever shows a
shifted pixel on your particular Pi (see LESSONS.md — the SSD1963
occasionally swallows a write strobe), `Display(write_passes=2)` heals
most of it at ~2× the time.

Draw calls are **diffed against a shadow of the panel's contents**: only
the changed spans are written, so dashboard-style updates — a clock or
graph in a mostly static UI — cost the changed spans instead of a full
rewrite. Measured on a Pi Zero W: an identical full-screen redraw diffs
in **~23 ms** and a changed 100×40 rect in **~13 ms**, against ~0.6 s
per full write. An identical redraw emits nothing. `force=True` on any
draw call skips the diff; `lcd.invalidate()` discards the shadow so the
next draw covering a region rewrites it in full — the escapes for the
rare write word the GRAM arbitration swallows in *every* pass, which
would otherwise leave the panel out of sync with the shadow.

## Touch

The V2.1 board ships with a **capacitive touch panel mounted by default**
and its controller (FocalTech FT5206-family) broken out on the display
connector: pins 33–37 carry `/RST`, `SCL`, `SDA`, `INT`, `WAKE` (pin 38
is the resistive-pen ground, unused). It speaks I²C, so the Pi can talk
to it with **no extra hardware** — just SCL/SDA to Pi GPIO 3/2, `/RST`
and `INT` to any two free GPIOs, and I²C enabled.

Touch is implemented: `ertftm070.touch` ships with v1.0. See
[docs/WIRING.md](docs/WIRING.md) for the pin details and
[docs/COMMUNITY-RESEARCH.md](docs/COMMUNITY-RESEARCH.md) §6 for the
controller facts: the V2.1 board ships the FT5206, the current V3 board
the FT5316 (same FT5x06 register map), I²C address 0x38, and the driver
needs almost no init — just timing and a dummy first read.

```python
from ertftm070 import Display
from ertftm070.touch import Touch

with Display() as lcd, Touch(lcd.bus) as touch:
    touch.wait_touch()                # wake-on-touch primitive
    for p in touch.read(mapped=True): # panel-native 800x480 coordinates
        x = min(p.x, lcd.width - 4)   # mapped points can sit right at the
        y = min(p.y, lcd.height - 4)  # edge — keep the mark on screen
        lcd.fill_rect(x, y, 4, 4, 0xFFFF)
```

`mapped=True` points live in the panel-native frame — the frame the
display draws at `rotation=0`.  On a rotated display, pass them through
`Display.unmap_point` first (see the touch module docs).

## Roadmap

- Backlight dimming — software PWM on the backlight pin
  (register-based dimming via `0xBE`/`0xD0` would need the J3/J4 jumper
  mod — see docs/COMMUNITY-RESEARCH.md §5)
- Hardware vertical scroll (SSD1963 `0x33`/`0x37`)
- GRAM screenshots — read the framebuffer back into a PIL image
- Pi 5 support via a libgpiod backend

Text, shapes and UI widgets are deliberately **not** part of the driver —
draw them in Pillow and blit with `lcd.image()`. It's the same pattern
at 60× less code.

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
pytest          # 189 tests, no hardware needed (a fake bus stands in)
ruff check src tests examples
```

CI runs lint + tests + builds on every push, CodeQL scans the Python,
and the main branch is protected: merges go through pull requests,
squash-only, and only when CI is green. Dependabot keeps the handful of
dev dependencies fresh. See [SECURITY.md](SECURITY.md) to report a
vulnerability privately, and [docs/RELEASING.md](docs/RELEASING.md) for
how a version tag becomes a PyPI release.

Contributions are welcome — same rules as everything else here: open a
PR, keep CI green, and yes, feel free to have an AI write it.

## License

[MIT](LICENSE) © 2026 Immanuel Dorneich
