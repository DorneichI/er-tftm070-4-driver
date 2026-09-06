# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `ertftm070` — the first installable release of the driver:
  - `Display` API: `fill`, `fill_rect`, `set_pixel`, `image` (Pillow),
    `rotation`, `backlight`, `sleep`/`wake`, `selftest`, `gramcheck`
  - Optional C extension (`_fastio`, port of the verified `fill.c` pixel
    blaster) with runtime spin calibration; automatic pure-Python fallback
  - `ertftm070` command-line tool: `selftest`, `bars`, `fill`, `image`, `gramcheck`
  - Off-hardware unit test suite, CI, and trusted PyPI publishing
- Touch: `ertftm070.touch` — FT5x06-family driver (FT5206 on V2.1,
  FT5316 on V3) over a stdlib-only I²C layer (`_i2c.py`, no smbus), with
  configurable calibration, INT//RST support, and the wake-on-touch
  primitive `Touch.wait_touch()`.  Verified on hardware 2026-09-06:
  multitouch works; the panel's raw span measures panel-native
  0..799 × 0..479 (not 0..4095) — that span is the default calibration;
  INT polarity is firmware-dependent (measured: idle low, high during
  touches — opposite of the datasheet's active-low convention), so
  `wait_touch` treats any INT change as an event, confirmed on
  TD_STATUS with a ~200 ms safety poll
- TE vsync: `Display.vsync_wait()` and `vsync=` on `fill_rect`/`image`
  pace row bursts into vertical blanking for tear-free updates (opt-in)
- Refresh measurement: `Display.refresh_rate()` derives the pixel clock
  and frame rate from the TE period and the init table's scan totals;
  `crystal_guess()` names the crystal (10 vs 12 MHz — see
  docs/COMMUNITY-RESEARCH.md §2).  Verified on hardware 2026-09-06:
  **53.7 Hz / ~26.2 MHz → 10 MHz crystal** (the `0xE7` register returns
  the FPR value, not a frequency, and is not used)
- `Bus.pin_read()` in both backends and the C extension; `Pins.te`;
  init now enables the tearing effect (`0x35 = 0x00`)
- CLI: `ertftm070 refresh` and `ertftm070 touch-test`
- Examples: `touch_paint.py`, `vsync_demo.py`, `wake_on_touch.py`
- Docs: `docs/COMMUNITY-RESEARCH.md` (the issue #5 research sweep),
  touch/TE wiring table, UART-conflict note

### Changed

- `fill_rect`/`image`/`set_pixel` write one row per burst through a single
  backend call (`Bus.row_blit` — one C call per row on the fast backend
  instead of the ~27 Python↔C round trips per row, which dominated
  per-row cost: a full-screen fill on a Pi Zero W dropped from ~1.5 s to
  ~0.6 s).  The bytes and WR strobes at the pins are unchanged; a custom
  `Bus` passed to `Display(backend=...)` must now implement `row_blit`.

## [0.0.0] — 2026-09-05

Hardware bring-up era (pre-package):

- Verified 16-bit 8080 wiring, init sequence, and C pixel blaster for the
  ER-TFTM070-4V2.1 on a Raspberry Pi Zero W — see `legacy/` and `docs/LESSONS.md`.
