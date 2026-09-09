# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Shadow-framebuffer diffing, on by default for every draw call
  (`fill`, `fill_rect`, `set_pixel`, `image`): `Display` keeps a RAM
  copy of what the panel shows (controller space, so it survives
  rotation changes; a per-word "known" map keeps never-written pixels
  always-dirty) and emits `row_blit` traffic only for the **changed
  spans** — changed runs within a row are coalesced (gaps below 32 px
  are cheaper to bridge than a new window costs) and blitted through
  narrower windows; scattered rows fall back to one whole-row span.
  An identical redraw emits nothing, and a mostly-static frame (a
  dashboard clock) costs milliseconds instead of the ~0.6 s of a full
  rewrite. The diff runs in pure Python at C speed: a memcmp per clean
  row, one big-int XOR + OR-reduction for a changed row (a Python loop
  only per changed run), and one memcmp each for an unchanged
  full-width window. Measured on the Pi Zero W: an identical
  full-screen redraw diffs in **~23 ms** and a changed 100×40 rect in
  **~13 ms**, against ~0.6 s per full write. Spans are computed once
  and replayed for every `write_passes` pass; the shadow is committed
  only after all passes succeed, and any exception mid-blit
  invalidates it (the next draw re-emits everything). Verified on
  hardware 2026-09-09: a span-window blit read back from GRAM leaves
  the untouched columns intact.
- `force=True` on `fill`/`fill_rect`/`set_pixel`/`image` — skip the
  diff and write every pixel of the window (the brute-force escape
  hatch).
- `Display.invalidate()` — discard the shadow; the next draw is a full
  redraw. Also invalidated internally on `reset()`, `close()`, and the
  raw `_blit` path (gramcheck). The two escapes cover the one real
  trade-off of diffing on this panel: a write word swallowed by the
  GRAM arbitration in *every* pass leaves panel ≠ shadow until a forced
  redraw (see `docs/LESSONS.md` §8).

### Changed

- `fill_rect` and `image` now write one burst per *span* rather than
  per row — a fully-changed row still emits byte-identical traffic to
  before (`vsync=True` paces each emitted span).
- `tests/test_diffing.py` — 22 tests: FakeBus traffic (first draw full,
  identical redraw silent, span narrowing, gap bridging/splitting,
  `_MAX_SPANS` fallback, full-width fast path + its known-map guard,
  `force`, `invalidate`, `write_passes`, vsync TE-pulse counts,
  rotation invariance, exception invalidation) plus simulator-oracle
  checks (`_shadow` == `SimulatedBus.full_frame()` after mixed draws).

## [0.2.0] — 2026-09-08

### Added

- Simulated display backend (`ertftm070.simulator`): set
  `ERTFTM070_DISPLAY=sim` (read once at import, like the existing backend
  choice) and `Display()`/`Touch` pick simulated components below the
  interfaces they already speak — dashboard and example code runs
  **unchanged** on a laptop with no Pi and no panel.
  - `SimulatedBus` implements the `Bus` protocol: `row_blit` copies the
    RGB565 words into an in-memory 800×480 framebuffer and streams each
    row to connected browsers over WebSocket (one message per call,
    raw little-endian RGB565 — the browser decodes, no encoding on the
    hot path). Register/pin writes are no-ops; pin levels are tracked so
    the touch INT line (idle low, high while touching) and the emulated
    TE waveform (~53.7 Hz, the measured panel refresh) read like the
    real panel's — `vsync=`, `vsync_wait()`, `refresh_rate()` and
    `wait_touch()` behave, and `ertftm070 refresh` reports the same
    clocks as hardware (53.8 Hz / 26.3 MHz → 10 MHz crystal). Rows are
    paced at the panel's ~1.3 ms per burst, so a continuously redrawing
    app drives the sim at hardware rates instead of flooding it.
  - Browser page served at `http://<host>:8000/` (binds `0.0.0.0`, so a
    sim running on the Pi is viewable from the Mac — override with
    `ERTFTM070_SIM_HOST`/`ERTFTM070_SIM_PORT`): 800×480 canvas scaled to
    fit the window, mouse as one touch, phone/tablet Touch Events as
    multi-touch (up to the FT5x06's five points, ids 0..4), auto-
    reconnect (every connect receives a full-frame snapshot, then rows).
  - `SimulatedI2C` serves the FT5x06 register file (TD_STATUS + point
    records) from the shared touch state, so `Touch` drives it unchanged;
    the framebuffer part is fully testable headless
    (`SimulatedBus(serve=False)` starts no server).
  - New optional `[sim]` extra (`websockets>=14`, imported lazily — core
    installs stay dependency-free). An explicit `Display(backend=…)`
    still wins over the env var, and `Touch` keys its transport off the
    bus, not the environment, so injected backends keep real-I2C
    semantics.
- `tests/test_simulator.py` — 33 tests: headless framebuffer/validation/
  pin/TE coverage, touch-state ↔ register round-trips, `Touch`
  end-to-end over the simulated register file, env selection, and
  WebSocket server smoke tests (page, protocol, touch injection, port
  conflicts, missing-package diagnostics), plus regression coverage for
  the origin gate, per-viewer touch ownership, disconnect cleanup, and
  poison-frame survival. CI's `dev` extra gained `websockets` so the
  server tests run in the matrix.

### Security

- The sim's WebSocket server refuses cross-origin browser handshakes
  (the `Origin` header must match the `Host`), so a page open anywhere
  on the operator's machine or LAN cannot silently read the framebuffer
  or inject touches into the app under development; binding a
  non-loopback host logs a startup warning. Non-browser clients that
  send no `Origin` (raw LAN scripts) stay admitted by design.

### Changed

- `ertftm070.BACKEND` may now be `"sim"`; `backends.get_backend()`
  returns a `SimulatedBus` when `ERTFTM070_DISPLAY=sim`. `selftest`/
  `gramcheck` report failure in sim (register read-back is not
  simulated); `lcd.sleep()` runs the command sequence but the picture
  does not go dark.

## [0.1.0] — 2026-09-06

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

### Fixed

- `Touch.open()`/`reset()` now drive the `/RST` pin with a real low pulse
  (`open()` previously only set it high) and then poll TD_STATUS for up to
  ~5 s until the FT5x06 leaves its phantom power-on state — a status
  claiming five touches whose records carry impossible finger ids (> 4)
  and frozen garbage coordinates, which a write-0 to `0x02` or a `/RST`
  pulse alone does not clear (bench-verified 2026-09-06).  A timeout logs
  a one-time warning per episode (the latch re-arms once the chip is next
  seen sane) and continues — the chip usually self-recovers within
  minutes — and reads/wakes stay clean anyway: `read()` drops the
  phantom's impossible-id points and `wait_touch()` does not wake on
  them.
- I2C transfers are bounded best-effort: `_i2c.I2C.open()` sets the
  i2c-dev `I2C_TIMEOUT` ioctl (100 × 10 ms = 1 s per transfer) where the
  adapter accepts it (the Pi's i2c-bcm2835 times single transfers
  against it), so a wedged FT5x06 holding the bus cannot stall the
  process indefinitely there.  The ioctl is not verified and an adapter
  that ignores it keeps its own default.

## [0.0.0] — 2026-09-05

Hardware bring-up era (pre-package):

- Verified 16-bit 8080 wiring, init sequence, and C pixel blaster for the
  ER-TFTM070-4V2.1 on a Raspberry Pi Zero W — see `legacy/` and `docs/LESSONS.md`.
