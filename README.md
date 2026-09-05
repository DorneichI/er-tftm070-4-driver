# ER-TFTM070-4 Driver

**A bit-banged 16-bit 8080 driver for the EastRising ER-TFTM070-4V2.1**
(7.0" TFT, 800×480, SSD1963 controller) on a Raspberry Pi Zero W.

> **Status: working.** All 8 color bars verified on hardware, September 2026.
> Full-screen updates take ~0.6 s over plain GPIO bit-banging.

---

## What this is

This repo documents how to drive the [ER-TFTM070-4V2.1](https://www.chipcad.hu/letoltes/ER-TFTM070-4V2.1_Datasheet.pdf)
display from a Raspberry Pi, and contains the working driver:

| Path | What it is |
|---|---|
| [`driver/display_test.py`](driver/display_test.py) | Python init sequence, bus self-test, test patterns, diagnostics |
| [`driver/fill.c`](driver/fill.c) | Tiny C pixel-blaster (via `/dev/gpiomem`) that makes full-screen fills fast |
| [`docs/WIRING.md`](docs/WIRING.md) | Pin-by-pin wiring, with diagrams |
| [`docs/INIT-SEQUENCE.md`](docs/INIT-SEQUENCE.md) | The exact register sequence and why each register matters |
| [`docs/LESSONS.md`](docs/LESSONS.md) | Everything we learned the hard way — read this before debugging |

## Quick start

```bash
# On the Pi, in the driver directory:
gcc -O2 -o fill fill.c

# Bus self-test (reads the SSD1963 device ID back):
python3 display_test.py --selftest

# The money shot — 8 full-height color bars in ~0.6 s:
python3 display_test.py --bars --init=utft --pixfmt=50
```

## The one fact that changes everything

**This display board is strapped for 16-bit 8080, not 8-bit.**

The board's R3/R4 jumpers select *8080-vs-6800* — **not** the bus width.
Most of the public example code targets the 8-bit configuration, and
nothing will look right until you wire DB8–DB15 and write one 16-bit
pixel per write strobe.

The sneaky part: the SSD1963 does register access over D[7:0] *regardless
of bus width*, so every register read-back self-test passes in both modes.
Only the pixel path reveals the truth (a horizontally doubled, striped image).

## Working configuration (verified)

| Setting | Value | Why |
|---|---|---|
| Interface | 16-bit 8080, one WR cycle per pixel | Board strap (see above) |
| Init table | UTFT `SSD1963_800` (see [INIT-SEQUENCE.md](docs/INIT-SEQUENCE.md)) | Community-verified for this board family |
| `0xF0` | `0x03` | 16-bit data bus, 565 format 1 |
| `0x3A` | `0x50` | 16 bits per pixel |
| `0x36` | `0x08` | Landscape, **BGR** order (without it, red ⇄ blue swap) |
| PLL | `0xE2 = 1E 02 54` → ~103 MHz | 10 MHz crystal, M=30, N=2 |
| Pixel clock | `0xE6 = 03 FF FF` → ~26 MHz | PLL / 4 |
| Panel display-on | `0xB8`/`0xBA` drive GPIO0 high | GPIO0 is wired to the panel's DISP line |

## Performance

The C helper writes pixels straight to the BCM2835 GPIO registers with no
system calls in the inner loop:

- **~0.6 s** for a full 800×480 redraw (≈640k pixels/s)
- Python-only bit-banging manages ~460 pixels/s — fine for test patterns,
  useless for anything real

## License

No license yet — ask the author if you want to reuse this.
