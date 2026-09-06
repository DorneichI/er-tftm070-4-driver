# Initialization sequence

The working init for this panel is the UTFT `SSD1963_800` table, with
three changes: `0xF0 = 0x03` (16-bit bus), `0x3A = 0x50` (16 bpp, set
right after init), and `0x36 = 0x08` (BGR color order).

## The sequence, annotated

| Step | Command / data | What it does |
|---|---|---|
| 1 | Hardware reset: RESET low 100 ms → high | Master reset |
| 2 | `0xE2` = `1E 02 54` | **PLL M/N.** M=30, N=2 → VCO = 10 MHz × 31 = 310 MHz, PLL = VCO/3 ≈ **103 MHz**. Last byte applies M/N. (Skipping this runs the PLL on uncalibrated POR values.) **All figures below assume a 10 MHz crystal; unverified — see [Timing](#timing).** |
| 3 | `0xE0` = `01`, wait 100 ms | Enable PLL |
| 4 | `0xE0` = `03`, wait 10 ms | Lock PLL, switch system clock to it |
| 5 | `0x01`, wait 100 ms | Software reset |
| 6 | `0xE6` = `03 FF FF` | **Pixel clock.** LSHIFT = PLL × (FPR+1)/2²⁰ = 103 MHz/4 ≈ **26 MHz** ≈ 928×525×53 Hz scan. (The 800×480 panel needs ~24–34 MHz; values like `00 FF BE` give ~7 MHz — nothing displays.) |
| 7 | `0xB0` = `24 00 03 1F 01 DF 00` | LCD mode: 24-bit panel width, LSHIFT latch on falling edge; HDP=799, VDP=479 (800×480) |
| 8 | `0xB4` = `03 A0 00 2E 30 00 0F 00` | Horizontal timing: HT=928, HBP=46, HPW=48, LPS=15 |
| 9 | `0xB6` = `02 0D 00 10 10 00 08` | Vertical timing: VT=525, VBP=16, VPW=16, FPS=8 |
| 10 | `0xBA` = `0F` | GPIO[3:0] as outputs, all high |
| 11 | `0xB8` = `07 01` | GPIO3=input, GPIO[2:0]=output, GPIO0 normal polarity |
| 12 | `0xF0` = `03` | **Pixel data interface: 16-bit bus, 565 format 1.** (Must match the hardware strap!) |
| 13 | `0x28` | Display off |
| 14 | `0x11`, wait 100 ms | Sleep out |
| 15 | `0x36` = `08` | Address mode: landscape, **BGR**. (With `00`, red and blue swap.) |
| 16 | `0x29` | **Display on** |
| 17 | `0xBE` = `06 F0 01 F0 00 00` | Backlight PWM config (only matters if backlight were board-controlled; harmless here) |
| 18 | `0xD0` = `0D` | Dynamic backlight control config (harmless here) |
| 19 | `0x3A` = `50` | **16 bits per pixel.** Set after display-on in the driver; the POR value is "reserved" and misbehaves on some chips. |
| 20 | `0x35` = `00` | **Tearing effect on**, V-blanking only. A driver-level addition — no community table sets it (see docs/COMMUNITY-RESEARCH.md §5). Feeds `Display.vsync_wait()` and `refresh_rate()` via the TE pin (connector pin 8). Skipped when `Pins.te` is `None` (no TE wire) — `vsync_wait()`/`refresh_rate()` then raise. |

Then, to draw: `0x2A` (column window) → `0x2B` (row window) → `0x2C`
(memory write) → stream one 16-bit pixel per WR strobe, low byte on
DB0–7, high byte on DB8–15, CS held low for the whole stream.

## Why GPIO0 matters

`0xB8`/`0xBA` configure the SSD1963's GPIO pins as outputs and drive
**GPIO0 = 1**. On EastRising boards GPIO0 is wired to the panel's
display on/off control — skip these writes and the panel stays in its
un-driven white state no matter what else you do.

## Timing

**Crystal frequency — verified on hardware (2026-09-06).** The figures
above assume a 10 MHz crystal; UTFT's own comment next to the `0x1E`
byte ("set PLL clock to 120M") is only true for a 12 MHz crystal, so
this was measured. TE (`0x35`) on connector pin 8 reads **53.7 Hz**
(18.6 ms), i.e. PCLK ≈ 929×526×53.7 ≈ **26.2 MHz** — the 10 MHz
prediction (25.8 MHz) to within 2%, and incompatible with the 12 MHz
prediction (31 MHz → 63.4 Hz). **The board carries a 10 MHz crystal;
all MHz/Hz numbers on this page hold.** The `0xE7` register was not
usable for this: on hardware it returns the FPR value (`0x03FFFF`),
not a frequency. See [COMMUNITY-RESEARCH.md](COMMUNITY-RESEARCH.md)
§2.

The controller's write-cycle timing is nominally ~100 ns, but this board
was observed to drop bytes with sub-microsecond strobes in some
configurations. The C helper writes with ~0.5 µs cycles and is verified
byte-perfect against GRAM read-back at that speed. Python bit-banging
(~30 µs/byte) also works and is a useful reference for debugging.
