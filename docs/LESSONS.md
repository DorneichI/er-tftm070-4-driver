# Lessons learned the hard way

A field guide to the traps this project fell into, in the order they
were found. If you are debugging this display, read this first.

## 1. The board is 16-bit, and nothing tells you

**Symptom:** image horizontally doubled, vertical 1-pixel stripes in
solid areas, colors wrong, everything looks gray from arm's length.

**Cause:** the SSD1963 was strapped for **16-bit 8080**, but the driver
sent 8 bits per write cycle. Each WR latched 16 bits: our byte on
D[7:0] plus *floating* D[15:8] noise — and one WR = one pixel, so the
"2 bytes per pixel" stream produced two pixels each.

**Why it fooled us:** the SSD1963 does **register access over D[7:0]
regardless of bus width**. The device-ID self-test (`0xA1` → `01 57 61
01 FF`) and every register round-trip passed identically in both modes.
Only pixel data exposes the width.

**Decisive test:** fill only the *left half* of the screen. 8-bit mode
shows red on the left half; 16-bit mode doubles the stream and fills
the whole screen. (Cheap, needs no instruments.)

**Lesson:** R3/R4 on this board select 8080-vs-6800, **not** bus width.
Don't trust "it says 8080" to mean 8-bit.

Since confirmed against the primary sources: the SSD1963 has no bus-width
strap at all — its only mode pin is CONF (8080/6800, datasheet Table 6-3),
registers only ever use D[7:0] "regardless the width of the pixel data"
(datasheet §7.1.3), and the board's R3/R4 is exactly that CONF strap
(board datasheet §4.4). See [COMMUNITY-RESEARCH.md](COMMUNITY-RESEARCH.md) §3.

## 2. A white screen can be a perfectly healthy display

An un-driven TFT with the backlight on is *uniform white*. It tells you
nothing except that power and backlight work. "Nothing displays" can
mean: init wrong, pixel clock wrong, panel display-line not asserted,
wrong bus width — or all four, stacked.

## 3. The register that switches the panel on

SSD1963 **GPIO0 is wired to the panel's display on/off control** on
EastRising boards. `0xB8`/`0xBA` must configure the GPIOs as outputs and
drive GPIO0 = 1, or the panel never leaves its white state.

## 4. The pixel clock must be plausible

`0xE6` sets LSHIFT = PLL × (FPR+1)/2²⁰. The 800×480 panel scans ~928×525
dots; it needs roughly 24–34 MHz. A value like `00 FF BE` (~7 MHz) gives
no visible picture at all. Also set the PLL (`0xE2`) — the POR M/N is
uncalibrated.

## 5. `0x3A` matters on some chips

POR value of `0x3A` (pixel format) is "Reserved". Leaving it unset made
this panel render written data desaturated/gray while unwritten GRAM
(noise) looked colorful — because random data "looks right" either way.
Setting `0x3A = 0x50` (16 bpp) fixed the panel path.

## 6. The BGR bit

`0x36` bit 3 swaps red and blue. This panel wants **BGR** (`0x36 = 0x08`
for landscape). Easy to miss because the symptom (red ⇄ blue) is subtle
if you're not showing pure colors.

## 7. BCM2835 GPIO set/clear registers — a classic footgun

| Register | Offset | Word index (uint32) |
|---|---|---|
| GPSET0 | 0x1C | 7 |
| GPCLR0 | 0x28 | 10 |

Swapping them writes inverted data and pulses WR on the wrong edges —
the display "works" (self-tests still pass!) but pixels are garbage.
**Always verify new write code with GRAM read-back before trusting the
screen.**

## 8. GRAM read-back is your best debugger

The SSD1963 lets you read its framebuffer back (`0x2E` after setting the
window). Write known bytes → read them back → you know exactly whether
the bus, the byte order, and the addressing are right, *independent* of
the panel. This is how every write-path bug above was found.

Also note: the first read after `0x2E` is **not** a dummy on this chip —
it returns real data. And bursts lose their final ~1.5 pixels when CS
releases; write two trailing dummy pixels per burst to absorb it.

**Panel-scale reads are unreliable** (added after the v1.0 bring-up):
reading tens of thousands of words in one go drops ~1 word per 400 —
the SSD1963's memory-read path does not stride rows the way the write
path does, and sustained reads hit the same GRAM arbitration as writes.
GRAM read-back remains an excellent debugger for *small* windows (the
`gramcheck()` single-row check passes 8/8 every time), but do not treat
a full-screen read-back as ground truth.

**Writes drop words too** — one swallowed WR strobe mid-burst shifts
every pixel after it (observed as "the bottom half shifted one pixel"
on the 180° test image; the drop position moves with timing, so it is
controller-side GRAM arbitration, not strobe width).  The fix that works
on hardware: **write one row per burst** (`Display._blit_rows`) so any
drop is contained to a single row, with each row's own 2 trailing dummy
pixels absorbing the burst-tail loss — the same geometry `gramcheck()`
verifies byte-perfect.  Keep the calibrated cycle at the legacy/fill.c
~1.6 µs/px; faster cycles drop more.

## 9. Speed limits

- Python `RPi.GPIO` bit-banging: ~460 px/s. Fine for tests, absurd for
  real use (a full screen took 14 minutes).
- The C helper (direct `/dev/gpiomem` register writes, no syscalls in
  the loop): ~640k px/s ≈ 0.6 s full screen, verified byte-perfect.
- This specific board dropped bytes at sub-microsecond strobe rates in
  8-bit experiments; the 16-bit path at ~0.5 µs/cycle verified clean.
  Verify after any timing change.

## 10. Power

Display VDD (5 V, up to 300 mA) from the Pi's 5 V pin browned out the Pi
once at plug-in (inrush). Steady state is fine with a good PSU; a
separate 5 V ≥ 1 A supply with common ground is the safe permanent choice.

## 11. Community corroboration (2026)

A sweep of the Arduino/ESP32/STM32/RPi community's experience with this
panel was done for issue #5; the full write-up with sources is
[COMMUNITY-RESEARCH.md](COMMUNITY-RESEARCH.md). The parts that refine
this page:

- **The rotation claim, made precise** (refines §6-adjacent lore): on
  the SSD1963, `0x36` A[7]/A[6] are *host fill-pointer direction* bits,
  not ILI-style MY/MX — they reverse the write order inside the window
  and scramble partial-window blits, which is exactly what we saw. The
  true mirror bits are A[1]/A[0] (`0x22` ⇄ `0x21` rotates 180° with
  zero coordinate changes, community-verified). **90°/270° hardware
  rotation does not exist on this controller** — our software rotation
  is the ecosystem's working pattern too (TFT_eSPI fakes it with A[5] +
  width/height swap; UTFT rotates in software).
- **The crystal frequency question is settled: 10 MHz.** Every figure
  in §4 assumed a 10 MHz crystal, while UTFT's own "set PLL clock to
  120M" comment next to our `0x1E` byte implies the 12 MHz crystals of
  the boards UTFT wrote the table for. Measured via TE on connector
  pin 8 (2026-09-06): 53.7 Hz → ~26.2 MHz PCLK — the 10 MHz prediction
  (25.8 MHz) to within 2%; the 12 MHz prediction (31 MHz / 63.4 Hz) is
  out. (`0xE7` read-back returns the FPR value, not a frequency —
  don't use it for this.)
- **`0x10`/`0x11` never touch the panel enable with our init.** Because
  `0xB8 = 07 01` configures GPIO0 as a plain host output, the sleep
  commands don't toggle it (datasheet §9.8); the effective power saving
  of `Display.sleep()` is `0x28` plus the backlight GPIO.
- **`0xBE`/`0xD0` are inert on the stock board** — jumper J3/J4 ships
  with the backlight under *external* control (our GPIO), not the
  SSD1963 PWM. The init writes are harmless no-ops; register dimming
  only works after bridging J3.
- **Our dropped-write mitigation is the community standard** — nobody
  has a register fix; row-per-burst + trailing dummies is what works.

## The full debugging saga

1. White screen, program runs fine → init was incomplete.
2. Added a read-back self-test (DDB via `0xA1`): **passed** → wiring proven, issue must be config.
3. Yellow-tinted, striped picture → kept chasing registers (B0, LLINE polarity, inversion, pixel format).
4. "Almost colourless" with one table, inverted with another → pixel format + panel width confusion.
5. Gray bars with colorful noise + a photo of the half-screen test → **16-bit mode discovered.**
6. Wired DB8–15, switched to one-WR-per-pixel, `0xF0 = 0x03` → **solid colors, red/blue swapped.**
7. `0x36 = 0x08` (BGR) → **everything correct.**

Total: one undocumented strap, three software bugs, one swapped
register pair, and a lot of read-back tests.
