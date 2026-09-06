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
a full-screen read-back as ground truth.  The *write* path has a related
quirk: at pixel cycles faster than the verified ~1.6 µs/px, one write
word per row can be dropped at the x=400 column (visible as a 1 px
color sliver at a quadrant boundary, e.g. the 180° test image) — keep
the calibrated cycle at the legacy/fill.c timing.

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
