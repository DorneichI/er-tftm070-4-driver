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

## [0.0.0] — 2026-09-05

Hardware bring-up era (pre-package):

- Verified 16-bit 8080 wiring, init sequence, and C pixel blaster for the
  ER-TFTM070-4V2.1 on a Raspberry Pi Zero W — see `legacy/` and `docs/LESSONS.md`.
