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
verified init sequence (hardware reset → register table → `0x3A=0x50`),
switches the backlight on, and applies the rotation. Leaving the block
(or Ctrl-C) turns the backlight off and releases the GPIO mapping.

| Parameter | Meaning |
|---|---|
| `pins` | Wiring; defaults to the hardware-verified `DEFAULT_PINS` |
| `init_table` | Init sequence; defaults to the verified `INIT_UTFT`. `INIT_ALT`/`INIT_BD` exist for tinkering |
| `backend` | A `Bus` instance, or `None` to auto-select (C extension → pure-Python fallback) |
| `auto_init` | If `False`, skip reset+init on open (advanced) |
| `backlight` | Whether to switch the backlight on after init |
| `rotation` | `0`, `90`, `180` or `270` — logical orientation |
| `write_passes` | How often each pixel is written (default 1). `2` heals most swallowed write words (a controller arbitration quirk, see `docs/LESSONS.md`) at 2× write time |

Attributes: `width`, `height` (follow the rotation), `pins`, `rotation`.

### Methods

**Drawing** — all colors are 16-bit RGB565 words (`rgb565(r, g, b)`).

- `fill(color)` — fill the whole (rotated) screen. Fast path, ~0.6 s.
- `fill_rect(x, y, w, h, color)` — one window, written one row per burst
  (a swallowed write word can then shift only one row — see
  `docs/LESSONS.md`); the right way to do partial updates (redraw only
  what changed).
- `set_pixel(x, y, color)` — single-pixel window write. Fine for sparse
  updates; use `fill_rect`/`image` for anything dense.
- `image(pil_image, x=0, y=0, fit=False)` — convert a Pillow image to
  RGB565 rows and blit it at the given top-left corner. With `fit=True`
  the image is scaled down (aspect preserved) to fit the current logical
  screen — handy after a rotation swaps `width`/`height`. Requires the
  `Pillow` extra.

**Display state**

- `rotation` (property) — `0`/`90`/`180`/`270`.  Rotation is pure
  software: the panel keeps its verified `0x36 = 0x08` (the flip bits
  scramble the GRAM write pointer on this controller) and
  `width`/`height` swap to follow.  See `docs/LESSONS.md`.
- `backlight(on)` — backlight pin high/low.
- `sleep()` / `wake()` — display off + enter sleep / exit sleep +
  display on.

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
(DB8-15), `cs`, `dc`, `wr`, `rd`, `reset`, `backlight`. Validated on
construction (unique, in range, 8+8 data pins). `DEFAULT_PINS` is the
verified wiring; pass a custom `Pins(...)` to `Display` to rewire.

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

`ertftm070.BACKEND` is `"fast"` (compiled `_fastio` C extension) or
`"slow"` (pure-Python `/dev/gpiomem` fallback), chosen at import time.
Set `ERTFTM070_FORCE_SLOW=1` to force the fallback (also silences the
missing-extension warning). Advanced users can pass a custom object
implementing the `ertftm070.backends.Bus` protocol as `Display(backend=…)`
— note that the protocol now includes `row_blit(x0, x1, y, buf)`, which
every `fill_rect`/`image`/`set_pixel` call uses to write one row
(window commands + burst + CS/DC framing in one call).
