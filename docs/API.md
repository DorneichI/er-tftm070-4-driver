# API reference

Everything public lives at the top level: `from ertftm070 import Display`.

## `Display`

```python
Display(
    pins: Pins = DEFAULT_PINS,
    init_table: Table = INIT_UTFT,
    backend: Bus | None = None,
    auto_init: bool = True,
    backlight: bool = True,
    rotation: int = 0,
    write_passes: int = 1,
)
```

A context manager. `with Display() as lcd:` opens the bus, applies the
verified init sequence (hardware reset → register table → `0x3A=0x50`,
→ `0x35=0x00` when `pins.te` is set), switches the backlight on, and
applies the rotation. Leaving the block (or Ctrl-C) turns the
backlight off and releases the GPIO mapping.

| Parameter | Meaning |
|---|---|
| `pins` | Wiring; defaults to the hardware-verified `DEFAULT_PINS` |
| `init_table` | Init sequence; defaults to the verified `INIT_UTFT`. `INIT_ALT`/`INIT_BD` exist for tinkering |
| `backend` | A `Bus` instance, or `None` to auto-select (C extension → pure-Python fallback) |
| `auto_init` | If `False`, skip reset+init on open (advanced) |
| `backlight` | Whether to switch the backlight on after init |
| `rotation` | `0`, `90`, `180` or `270` — logical orientation |
| `write_passes` | How often each pixel is written (default 1). `2` heals most swallowed write words (a controller arbitration quirk, see `docs/LESSONS.md`) at 2× write time |

Attributes: `width`, `height` (follow the rotation), `pins`, `rotation`,
`bus` (the opened `Bus`, `None` until `open()` — hand it to `Touch`).

### Methods

**Drawing** — all colors are 16-bit RGB565 words (`rgb565(r, g, b)`).
Every drawing call accepts `vsync=False`: with `True` each span waits
for the start of a fresh vertical-blanking window (TE pin) before it is
written — tear-free, at one frame per span.  Meant for narrow,
fast-moving content; see the `vsync_wait` note below for the cost.
Every drawing call also accepts `force=False`: `True` skips the
shadow diff and writes every pixel of the window in full.

- `fill(color, force=False)` — fill the whole (rotated) screen. Fast path, ~0.6 s.
- `fill_rect(x, y, w, h, color, vsync=False, force=False)` — one window,
  written one span per burst (a swallowed write word can then shift only
  one span — see `docs/LESSONS.md`); the right way to do partial updates.
- `set_pixel(x, y, color, force=False)` — single-pixel window write.
  Fine for sparse updates; use `fill_rect`/`image` for anything dense.
- `image(pil_image, x=0, y=0, fit=False, vsync=False, force=False)` —
  convert a Pillow image to RGB565 rows and blit it at the given
  top-left corner.  With `fit=True` the image is scaled down (aspect
  preserved) to fit the current logical screen — handy after a rotation
  swaps `width`/`height`.  Requires the `Pillow` extra.

  Drawing is diffed against a shadow of the panel's contents (kept in
  controller space, so it survives rotation changes): only the changed
  spans are written — an identical redraw emits nothing, and a
  mostly-static frame (a dashboard clock) costs milliseconds instead
  of the ~0.6 s of a full rewrite.  The shadow can rarely diverge from
  the panel (a write word swallowed by the GRAM arbitration in every
  pass): `force=True` or `invalidate()` below are the escapes.

**Display state**

- `rotation` (property) — `0`/`90`/`180`/`270`.  Rotation is pure
  software: the panel keeps its verified `0x36 = 0x08` (the flip bits
  scramble the GRAM write pointer on this controller) and
  `width`/`height` swap to follow.  See `docs/LESSONS.md`.
- `unmap_point(x, y)` — inverse of the rotation mapping: a
  panel-native point (what `Touch.read(mapped=True)` returns) → the
  current logical frame, ready for the draw calls.  Identity at
  `rotation=0`.
- `backlight(on)` — backlight pin high/low.
- `invalidate()` — discard the driver's shadow of the panel's
  contents: the next draw re-emits every pixel it touches, like a full
  redraw.  Use after anything that changes the panel behind the
  driver's back, or to rewrite a pixel the GRAM arbitration swallowed
  in every write pass; for the same guarantee on a single draw, pass
  `force=True` instead.
- `sleep()` / `wake()` — display off + enter sleep / exit sleep +
  display on.

**Timing (TE pin)** — when `pins.te` is `None` (no TE wire) these
raise `RuntimeError` instead of hanging; init also skips `0x35`.

- `vsync_wait(timeout=0.1)` — block until the TE line next goes high:
  the start of a vertical-blanking window.  The primitive behind
  `vsync=`; also usable directly to pace hand-rolled drawing.  Raises
  `TimeoutError` when no TE pulse arrives.
- `refresh_rate(samples=5, timeout=2.0)` → `(pclk_khz, hz)` — the
  panel's actual clocks, measured: the frame rate directly from TE
  pulses (median over `samples`), PCLK from `hz × HT × VT` (scan
  totals read out of the init table).  The `0xE7` register does *not*
  report a frequency on this chip.
- `read_register(reg, n)` — read `n` bytes from a controller register
  (`0xE7`, `0xB9`, ...), low byte of each 16-bit word.  Keep `n` small:
  long reads drop words (see the `_read_words` caveat).
- `crystal_guess(pclk_khz, init_table=INIT_UTFT)` (module function in
  `ertftm070.display`) — name the crystal behind a measured pixel
  clock (12 / 10 / 6.5 MHz), deriving the PLL math from the table's
  own `0xE2`/`0xE6` entries.  Raises `ValueError` if the table lacks
  them.

**Diagnostics** (both return `bool` and log details)

- `selftest()` — DDB read (`0xA1` must answer `01 57 61 01 FF`) plus a
  `0xB8`/`0xB9` register round-trip. Proves CS/DC/WR/RD + DB0-7; works
  right after reset, in either bus-width mode.
- `gramcheck()` — writes the 8 known RGB565 words, reads GRAM back via
  `0x2E`, compares. Proves the **16-bit pixel path** — window, stream,
  byte order — independent of the panel.

**Lifecycle**

- `open()` — idempotent; raises `NotOnRaspberryPi` off the supported
  hardware.
- `close()` — backlight off + release the bus; idempotent.
- `reset()` — hardware reset pulse.

### Errors

- `Ertftm070Error` — base class for package errors.
- `NotOnRaspberryPi` — unsupported hardware: not a Pi, or a Pi 5 (RP1
  GPIO controller; different registers, not supported yet).
- `ValueError` — bad bounds, rotation values, or pin configuration.

## `Pins`

Frozen dataclass of BCM GPIO numbers: `data_low` (DB0-7), `data_high`
(DB8-15), `cs`, `dc`, `wr`, `rd`, `reset`, `backlight`, `te`
(default GPIO14; the panel's TE output on connector pin 8). Validated
on construction (unique, in range, 8+8 data pins). `te=None` disables
TE support: pin 8 left unwired, `0x35` skipped, the TE-based timing
calls above raise. `DEFAULT_PINS` is the verified wiring; pass a custom
`Pins(...)` to `Display` to rewire.

## Touch (`ertftm070.touch`)

The FT5x06-family controller on the panel (FT5206 on V2.1, FT5316 on
V3 — same register map). I²C address 0x38 on I2C-1; the chip needs
almost no init. `Display` and `Touch` share the bus — the fast backend
is single-owner, so `Touch(lcd.bus)` is the only wiring, and the touch
must be closed before its `Display`.

```python
from ertftm070 import Display
from ertftm070.touch import Touch

with Display() as lcd, Touch(lcd.bus) as touch:
    for p in touch.read(mapped=True):
        x, y = lcd.unmap_point(p.x, p.y)  # panel-native -> logical frame
        lcd.fill_rect(x, y, 4, 4, 0xFFFF)
```

`Touch(bus, touch_pins=DEFAULT_TOUCH_PINS, calibration=DEFAULT_CALIBRATION,
i2c=None)` — context manager, `open()` idempotent. `open()` (also run
by the context manager) drives the configured `/RST` pulse (high→low
≥5 ms, then the 300 ms Trsi settle before the chip's first report),
opens I2C-1 at 0x38, does the one dummy read the chip needs, and then
polls TD_STATUS for up to ~5 s waiting out the phantom power-on state
(impossible touch claims — see `reset()`). A phantom that outlives the
wait is warned about (once per episode) and carried on from; either
way it never surfaces as touches (`read()` drops its impossible-id
records, `wait_touch()` does not wake on it). If the I2C bus itself
fails to answer during `open()`, the transport is closed again and the
error propagates — a retry runs the full `open()`.

- `read(mapped=False)` → `[TouchPoint]` — one TD_STATUS byte + one
  6-byte record per touch; drops release events, records with
  impossible finger ids (the phantom state), and the chip family's
  bogus full-scale DOWN coordinates. `mapped=True` returns
  panel-native coordinates via the calibration (always the 800×480
  rotation-0 frame — pass them through `Display.unmap_point` when the
  display is rotated). Raises if the touch is not open.
- `wait_touch(timeout=None, poll_interval=0.02)` → `bool` — True when a
  touch shows up, False on timeout.  Nothing hard-blocks: the INT pin
  is read every `poll_interval` seconds (any level *change* confirms on
  TD_STATUS; TD_STATUS is also confirmed on the first poll and every
  ~200 ms, so a stuck or missing INT degrades to plain polling).  The
  wake-on-touch primitive.
- `reset()` — pulse `/RST` low ≥5 ms (Trst), wait 300 ms (Trsi), then
  flush the first-report garbage. The recovery hammer for a wedged
  chip; it also waits out the phantom power-on state (impossible
  TD_STATUS claims) for up to ~5 s before proceeding. The pulse does
  not clear a phantom and one can outlive the wait — reads and wakes
  stay clean anyway (`read()`/`wait_touch()` drop the impossible-id
  records). Raises if no `/RST` pin is configured.
- `close()` — release the I²C bus.

### `TouchPoint`

Frozen dataclass: `x`, `y` (12-bit raw panel coordinates), `id`, `event`
(`EVENT_DOWN`/`EVENT_CONTACT` — releases are dropped).

### `TouchCalibration`

Frozen dataclass mapping raw → panel-native coordinates: `width`
(800), `height` (480), `raw_x_min/max` (0/799), `raw_y_min/max`
(0/479) — the span measured on this panel — plus `swap_xy`,
`mirror_x`, `mirror_y` for other mountings. `map(point)` does the
conversion (clamped); `DEFAULT_CALIBRATION` is the measured one.

### `TouchPins`

Frozen dataclass of the touch GPIOs: `int_pin` (default 15), `rst_pin`
(default 0); `None` disables a pin.  Validated on construction like
`Pins` (range and INT//RST uniqueness); collisions with the display's
`Pins` are rejected by `Touch` at construction (`_validate_pin_layout`)
— both drivers would otherwise fight over the shared GPIO register
bank.

## `rgb565(r, g, b)`

Pack 8-bit RGB into a 16-bit 565 word. No red/blue swap — the controller
(`0x36 = 0x08`, BGR) handles that for this panel.

## Init tables

- `INIT_UTFT` — **the verified one** (UTFT SSD1963_800: ~103 MHz PLL,
  ~26 MHz pixel clock, 928×525 timings, GPIO0-driven panel enable).
- `INIT_ALT` / `INIT_BD` — alternative tables kept for tinkering on
  other board revisions.

Table format: a list of `(command, [data bytes...])` tuples and `float`
delay entries. Note `0x3A = 0x50` (16 bpp) is applied by `Display`
**after** the table, matching the verified driver order.

## Backends

`ertftm070.BACKEND` is `"fast"` (compiled `_fastio` C extension),
`"slow"` (pure-Python `/dev/gpiomem` fallback) or `"sim"` (the browser
simulator), chosen at import time — set env vars before starting
Python. Set `ERTFTM070_FORCE_SLOW=1` to force the fallback (also
silences the missing-extension warning). Advanced users can pass a
custom object implementing the `ertftm070.backends.Bus` protocol as
`Display(backend=…)` — note that the protocol now includes
`row_blit(x0, x1, y, buf)`, which every `fill_rect`/`image`/`set_pixel`
call uses to write one row (window commands + burst + CS/DC framing in
one call).

## Simulator (browser backend)

`ERTFTM070_DISPLAY=sim` makes `Display()` and `Touch` pick simulated
components automatically — no code changes in the app:

```bash
pip install 'ertftm070[sim]'             # the websockets extra
ERTFTM070_DISPLAY=sim python3 examples/touch_paint.py
# → open http://localhost:8000/ and draw with the mouse
```

**Behavior contract.** `SimulatedBus` keeps an 800×480 panel-native
framebuffer (controller space, so `rotation` works exactly as on
hardware) and streams one binary row message per `row_blit` call to
every connected browser — the same row-by-row update behavior the panel
shows. Each connect first receives a full-frame snapshot. Register and
pin writes are no-ops; `pin_read(TE)` answers an emulated ~53.7 Hz
blanking waveform so `vsync=`, `vsync_wait()` and `refresh_rate()`
behave (and measure plausible clocks), and pins set to *input* answer
the touch INT line — idle low, high while any finger is down — so
`wait_touch()` works with any `TouchPins` wiring. `read_word()` returns
0: register read-back is not simulated, so `selftest()`/`gramcheck()`
report failure.

**Touch.** Browser mouse = one touch; Touch Events (phones/tablets) =
multi-touch, up to the FT5x06's five points (ids 0–4). Both input
paths stay live on every device (a touch-capable laptop still draws
with its trackpad; the compatibility mouse events a touch screen
synthesizes after each touch are ignored). The server encodes the
shared touch state into real FT5x06 registers, served by
`SimulatedI2C` through the same interface `Touch` drives on hardware —
`read()`, `read(mapped=True)`, `wait_touch()`, `reset()` all work.
Each viewer owns the finger ids it pressed: a viewer that disconnects
mid-press has its fingers lifted (no touch can stick down forever),
and concurrent viewers cannot yank each other's points around the
shared five-id map.

**Server.** Binds `0.0.0.0:8000` by default (override:
`ERTFTM070_SIM_HOST` / `ERTFTM070_SIM_PORT`), so a sim running on the
Pi is viewable from any browser on the LAN. Port conflicts and a
missing `websockets` package raise `Ertftm070Error` with a clear
message — never `NotOnRaspberryPi`. Every connection is greeted with a
full-framebuffer snapshot and may inject touches, so the WebSocket
endpoint enforces a same-origin policy: handshakes carrying an
`Origin` that is not the page's own host are refused (open the page at
`http://<host>:<port>/`, not as a `file://` or from another site), and
raw clients without an `Origin` header are admitted. On an untrusted
network, bind the loopback explicitly with
`ERTFTM070_SIM_HOST=127.0.0.1`; binding `0.0.0.0` logs a warning.

**Selection.** The env var is read once at import (like `BACKEND`
itself); an explicit `Display(backend=…)` always wins. `Touch` keys its
default transport off the *bus*, not the env var, so an injected
backend keeps real-I2C semantics; an explicit `i2c=` wins over both.
Headless use (framebuffer only, no server): `SimulatedBus(serve=False)`.
