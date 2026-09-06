# What the SSD1963 community knows (2026 research sweep)

A systematic sweep of the Arduino/ESP32/STM32/Raspberry Pi community
experience with the ER-TFTM070-4 and the SSD1963 in general, done for
[issue #5](https://github.com/DorneichI/er-tftm070-4-driver/issues/5).
It answers the six questions the issue posed, weighs the findings
against this driver, and ends with a ranked list of things to act on.

**Facts and register values only — no code was copied from any source.**
See [License notes](#license-notes) at the end.

## TL;DR

- Everything this project learned the hard way is **confirmed by the
  datasheet and the community**, and is now citable to primary sources:
  R3/R4 = 8080/6800 strap, registers only ever use D[7:0], bus width is
  pure wiring, `0x36` has no hardware 90°/270° rotation, dropped write
  words are a host-timing problem with no register fix.
- **One number in our docs is unverified**: the crystal frequency. Our
  103 MHz PLL / 25.8 MHz PCLK / ~53 Hz figures assume a 10 MHz crystal;
  UTFT's own table comments imply the 7" boards it targeted use 12 MHz
  (→ 124 MHz / 31 MHz / ~63 Hz). Cheap to settle: measure TE on pin 8.
- **New hardware fact**: the stock board's backlight is *not* wired to
  the SSD1963 PWM (jumper J3/J4), so our `0xBE`/`0xD0` init writes are
  inert — and `0x10`/`0x11` (sleep/wake) never touch the panel enable
  with our `0xB8 = 07 01` GPIO config.
- **Touch**: the V3 board ships the FT5316 (FT5206 discontinued by
  FocalTech in 2024); the FT5x06 register map is the same, needs almost
  no init, and there are MIT-licensed precedents to build on.

## 1. Init sequence variations

The entire 800×480 table family traces to one root: Henning Karlsen's
UTFT `SSD1963_800`/`SSD1963_800ALT` (MCUFRIEND_kbv's source comments
credit them to UTFT v2.81/v2.82; TFT_eSPI re-types the same tables).
There is **no "ER" variant** anywhere — the closest thing to an
EastRising-endorsed table is TFT_eSPI's `SSD1963_800BD`, commented
"Copied from Buy Display code" in the source.

Byte-level differences between the tables in circulation (ours is the
UTFT-800 column):

| Register | UTFT-800 / ours | TFT_eSPI 800 | 800ALT | 800BD (BuyDisplay) |
|---|---|---|---|---|
| `0xE2` | `1E 02 54` | same | `23 02 04` | `23 02 54` |
| `0xE6` | `03 FF FF` | same | `04 93 E0` | `03 33 33` |
| `0xB0` p1 | `24` | `20` | `00` | `20` |
| `0xB4` | `03A0/2E/30/0F` | same | same | `041F/D2/00/00` |
| `0xB6` | `020D/10/10/08` | same | same | `020C/22/00/00` |
| GPIO | `BA 0F`, `B8 07 01` | same + re-asserts `B8 0F 01`, `BA 01` after `0xF0` | `BA 05`, `B8 07 01` | `B8 0F 01`, then `BA 01` |
| `0x36` | `22` | `21\|BGR` | `21\|BGR` | `21\|BGR` |
| `0xF0` | `03` | `00` (8-bit bus) | `00` | `00` |
| `0x28`/`0x11` | absent | absent | absent | absent |
| `0xBC` | — | — | — | `40 80 40 01` |
| `0xBE` | `06 F0 01 F0 00 00` | same | same | `06 80 01 F0 00 00` |
| `0x3A` | — | — | — | commented out |
| `0xD0` | `0D` | `0D` | `0D` | `0D` |

What the variants are for:

- **800ALT** targets 7" panels where "800" fails (white/blank).
  Differences: 120 MHz PLL target, ~34.3 MHz PCLK (the panel class's
  spec-typical 33 MHz), `0xB0` p1 = `00` (18-bit panel data + rising
  edge latch vs 24-bit everywhere else). "ALT fixes it" vs "800 fixes
  it" is panel-lot hearsay with no documented root cause.
- **800BD** is the only table containing `0xBC` (image post-processor:
  contrast/brightness/saturation at POR defaults + enable bit — expect
  no visible change). It also uses a much wider horizontal total
  (1056 px vs our 929) and 50% PWM duty. Mixing 800BD timing with other
  tables' timing is the classic recipe for "shifted picture" complaints
  (EEVblog example: ["dead space / white chunk fixed by correct
  PLL+timing"](https://www.eevblog.com/forum/beginners/tft-help/msg5744649/)).
- **`0xB0` p1 `0x24` vs `0x20`** is bit A2, LSHIFT polarity (data latch
  on falling vs rising edge, datasheet §9.35). Both polarities
  demonstrably work in the field; `0x24` is the UTFT default and is
  verified on our hardware.
- **Registers in some table but not ours**: only `0xBC` (at neutral
  values — skip unless output looks washed-out/over-saturated, readable
  back via `0xBD`). `0x26` gamma is *not* a real gap: on the SSD1963 it
  only selects one of four factory curves (A[3:0], POR = curve 3); there
  is no gamma RAM, and the ILI-style "0xB8/0xB9 = gamma" pairing is a
  misconception (`0xB8` is GPIO config here).
- **`0x3A = 0x50` after display-on is our validated divergence**: no
  community table writes `0x3A` at all (TFT_eSPI omits it, BuyDisplay
  comments it out) even though the POR value A[6:4] = 000 is
  "Reserved" per the datasheet (§9.29). Keep it; a future port would
  silently drop it.

Sources: UTFT (facts only, non-commercial license — see
[License notes](#license-notes)),
[MCUFRIEND_kbv](https://github.com/board707/MCUFRIEND_kbv) (attribution
facts only), [TFT_eSPI
SSD1963_Init.h](https://github.com/Bodmer/TFT_eSPI/blob/master/TFT_Drivers/SSD1963_Init.h),
SSD1963 datasheet Rev 0.20 (vendor doc; §9.14, §9.29, §9.35, §9.41),
[MikroE SSD1963 command
reference](https://docs.mikroe.com/mikrosdk/ref-manual/group__ssd1963__commands.html).

## 2. PLL / timing (`0xE2` / `0xE6`)

**The datasheet's PLL formula text is misleading.** Rev 0.20 states
`VCO = reference × N`, `PLL = VCO / M` with the raw byte values — read
literally, our `1E 02` would give 150 MHz, which matches nothing anyone
measures. The same datasheet's own worked example writes `0x21` while
calling it "N=34" (i.e. value+1). Every working implementation —
TFT_eSPI's "set PLL clock to 120M" comment, the Rev 1.4 example
`1D 02 54` for 100 MHz, and the from-scratch Pi driver
[fbrausse/ssd1963](https://github.com/fbrausse/ssd1963) (BSD 2-clause,
bit-banged GPIO like ours), which validates
`VCO ∈ (250, 800) MHz` and stores multiplier−1/divider−1 — uses:

> PLL = f_ref × (byte1 + 1) / ((byte2 & 0x0F) + 1)

Our 103.33 MHz reading of `1E 02 54` at 10 MHz is the correct one.

**The open question: is our crystal 10 MHz or 12 MHz?** UTFT's table
comment "set PLL clock to 120M" next to byte `0x1E` is only true for a
12 MHz crystal (12×31/3 = 124 MHz), and UTFT's comment key
("N=0x36 for 6.5M, 0x23 for 10M crystal") shows each multiplier byte
targeting ~120 MHz for a *different* crystal. The 7" ITead boards UTFT
wrote the table for use 12 MHz. If our board's crystal is 10 MHz
(assumed from the datasheet-typical figure), all our documented numbers
hold (103 MHz, 25.8 MHz, ~53 Hz). If it is 12 MHz, they're ~20% off
(124 MHz, 31.0 MHz, ~63.4 Hz) — still inside panel spec (typical
33 MHz, max 40 MHz), but a page of our docs would be wrong. The
datasheet's XTAL spec (2.5–10 MHz) cannot arbitrate: 12 MHz-crystal
boards exceed it and work anyway.

Cheapest definitive check: **measure the TE period on connector pin 8**
(18.9 ms at 52.9 Hz vs 15.8 ms at 63.4 Hz), or read back `0xE4` PLL
status, or scope LSHIFT.

Other timing facts:

- `PCLK = PLL × (LCDC_FPR+1) / 2²⁰` confirmed independently by the
  Solomon EVK app note [PCLK Setup
  SSD1963](http://techtoys.com.hk/Displays/Solomon%20SSD1963%20EVK%20R4_1/AppNotes/PCLK%20Setup%20SSD1963%20Rev0_1.pdf)
  and by fbrausse's driver. Our `03 FF FF` is exactly PLL/4.
- Before the PLL locks, register writes must be slower than half the
  reference clock (§9.69) — irrelevant to our bit-banging, matters if
  the init path is ever accelerated.
- **Dropped/swallowed write words are not a PLL problem.** Our mid-burst
  swallowed strobes and ~1-per-400 read-back drop have direct community
  analogs, all traced to host-interface timing margins (CS setup/hold
  against the controller spec), not the pixel clock: see the RPi
  [write-strobe noise / shifted pixels
  thread](https://forums.raspberrypi.com/viewtopic.php?p=650251) and the
  ST community FMC/CS-timing threads. Our mitigation (one row per burst,
  2 trailing dummy pixels, generous strobe width) is the standard,
  correct one — nobody has a register fix.
- Community clock lore: the panel class (HannStar HSD050IDW1-A spec,
  TechToys TY500TFT800480 note) wants ~33 MHz typical / 40 MHz max;
  there are reports of instability at high refresh
  ([51hei thread](http://www.51hei.com/bbs/forum.php?mod=viewthread&tid=157925)).
  Our ~25.8 MHz is below typical but legal; `0xE6 = 04 93 E0`
  (~29.6 MHz at 103.3 MHz PLL, ~60 Hz) is a safe bump if motion
  smoothness ever matters — but re-measure panel-scale read-back drops
  after any clock change, since they scale with scan rate.

## 3. 16-bit vs 8-bit — the strap question

**Confirmed, with primary sources.** The SSD1963 has **no IM/width
strap pins at all** — a full-text scan of the 93-page datasheet finds
zero strap/jumper/width-selection pins. The only mode pin is **CONF**
(Table 6-3, p.14): "0: 6800 Interface, 1: 8080 Interface". The board's
R3/R4 is exactly that strap — the ER-TFTM070-4V2.1 datasheet §4.4
(p.11): "Solder 0 ohm resistor on R3, R4 no connection: 8080
Interface; Solder 0 ohm resistor on R4, R3 no connection: 6800
Interface", shipped with R3 populated. Bus width is defined purely by
which DB lines are wired (board datasheet §4.1: DB0–DB7 = 8-bit,
DB0–DB15 = 16-bit, unused pins float).

The datasheet says verbatim (§7.1.3, "Register Pin Mapping"):

> "When user access the registers via the parallel MCU interface, only
> D[7:0] will be used regardless the width of the pixel data is."

— the exact trap of [LESSONS.md §1](LESSONS.md), now citable.

**`0xF0` detail worth knowing**: there are *two* 16-bit flavors —
`0x03` = RGB565 (one word = one pixel, what we use) and `0x02` =
"16-bit packed" (RGB888 components packed across consecutive words).
POR is `101` = 24-bit. A mismatch between `0xF0` and the wiring
mis-packs pixel data while commands keep working. One uGFX participant
claimed width is jumper-selected — contradicted by the datasheet and by
that thread's own register-only fix; treat it as confusion with other
controllers.

Sources: SSD1963 datasheet Rev 1.1 (Table 6-3, §7.1.3, §7.1.4, §9.74)
at [Newhaven app
notes](https://www.newhavendisplay.com/app_notes/SSD1963.pdf);
[ER-TFTM070-4V2.1 datasheet](https://www.chipcad.hu/letoltes/ER-TFTM070-4V2.1_Datasheet.pdf)
§2.3/§4.1/§4.4; [uGFX thread](https://community.ugfx.io/topic/456-ssd1963-8-bit-interface/);
[Arduino Forum 576184](https://forum.arduino.cc/t/looking-for-experiences-with-ssd1963/576184);
[RPi forums t=45603](https://forums.raspberrypi.com/viewtopic.php?t=45603).

## 4. MADCTL / rotation

Our claim is **confirmed, and the datasheet gives the precise
mechanism**: on the SSD1963, `0x36` is *not* ILI-style MADCTL (datasheet
§9.24):

- **A[7]/A[6]** — direction the *host fill pointer* walks pages/columns
  while writing a window. These are the bits ILI-style MY/MX values
  land on, and they neither rotate nor mirror the image — they reverse
  the write order inside your window, which scrambles any driver whose
  partial-window/row-blit math assumes ascending fill. Exactly our
  observed failure.
- **A[5]** — page/column fill order (the "MV" stand-in; TFT_eSPI's
  rotation table uses it plus a width/height swap, and never touches
  A[7]/A[6]).
- **A[1]/A[0]** — the *true* glass flips (horizontal/vertical mirror);
  "no change is made to the frame buffer". Community-verified: `0x36 =
  0x22` → `0x21` rotates the picture 180° with zero coordinate changes
  ([Arduino Forum
  178637](https://forum.arduino.cc/t/utft-rotate-the-screen-180-degrees/178637)).
- A[2]/A[4] explicitly say "image unaffected".

**90°/270° hardware rotation does not exist on this controller.**
UTFT has no setRotation API — it is orientation-fixed per model and
rotates conceptually in software. TFT_eSPI fakes rotation with A[5] +
width/height swap. Our software-rotation decision is the right one and
matches the ecosystem's working patterns.

Related lore: on some boards GPIO0 is wired to the panel's L⇔R input,
so mirroring is a GPIO problem, not a `0x36` problem
([Arduino Forum 148497](https://forum.arduino.cc/t/fixed-ssd1963-controller-with-7-tft-horizontally-mirrored-image/148497)).
Our board's datasheet is silent on GPIO0's destination beyond the
display-on behavior our init already exploits.

## 5. Panel quirks

**Backlight: `0xBE`/`0xD0` are wired to nothing on the stock board.**
The V2.1 datasheet §4.4 documents jumpers J3/J4: J3 short + J4 open =
backlight control signal from the SSD1963 (PWM); J4 short + J3 open =
external input — and the board **ships J4 short, J3 open**. So the
SSD1963's PWM output drives nothing, our GPIO25 is the real backlight
control, and the `0xBE`/`0xD0` init writes are inert (harmless).
If register-based dimming is ever wanted: bridge J3, open J4; duty is
`0xBE` param2 (/256), `0xD0 = 0x0D` enables dynamic backlight control.
Two cautions from the field: verify jumper semantics against the
silkscreen (the sibling ER-TFTM050-4 is reported with the *opposite*
shipped state and a broken internal-PWM mode,
[TFT_eSPI discussion #2194](https://github.com/Bodmer/TFT_eSPI/discussions/2194)),
and expect an init-time bright flash when switching to internal PWM
(dim immediately after init). Also note the `0xBE` byte layout is not
agreed between sources — follow the datasheet layout (§9.47), not
forum recipes. For what "PWM-driven backlight behaves like a power
fault" looks like, see [TFT_eSPI issue
#1401](https://github.com/Bodmer/TFT_eSPI/issues/1401).

**Sleep/wake are effectively no-ops for the panel on our config.**
Datasheet §9.8: `0x10` turns off the panel and pulls GPIO0 low — *but
only if GPIO0 is configured as panel power control*. Every init table
in circulation (including ours, `0xB8 = 07 01`) configures GPIO0 as a
plain host output, so `0x10`/`0x11` never touch the panel enable. That
is exactly why no SSD1963 wake-corruption reports exist in the wild:
nobody actually drives it. Our `sleep()`/`wake()` currently send
`0x28`+`0x10` / `0x11`+`0x29`; the real power saving comes from `0x28`
(display off, framebuffer preserved) plus our backlight GPIO. If true
suspend is ever wanted, set `0xB8` p2 B0 = 0 so GPIO0 follows the sleep
commands — noting a polarity conflict between datasheet revisions (Rev
0.20 says `0x10` pulls GPIO0 low, MikroE's docs say high) and the
5 ms-after-transition / ≥120 ms-between lore (CodeVisionAVR library).
Avoid `0xE5` deep sleep: it stops the PLL and is reboot-equivalent.

**TE is available on pin 8** — the V2.1 datasheet's 40-pin table lists
"TE Tearing effect" on connector pin 8; our wiring currently leaves it
unconnected. `0x35` takes one param: `0x00` = V-blanking only, `0x01` =
V+H. The Solomon EVK [Tearing Effect app
note](http://techtoys.com.hk/Displays/Solomon%20SSD1963%20EVK%20R4_1/AppNotes/Tearing%20Effect%20SSD1963%20Rev0_1.pdf)
and LVGL users ([forum
thread](https://forum.lvgl.io/t/ssd1963-tearing-effect/653), [PJRC
thread](https://forum.pjrc.com/index.php?threads/73471)) use TE to sync
full-frame updates and flip the frame base address mid-TE. For us TE is
the natural way to (a) make full-screen updates tear-free and (b) settle
the crystal-frequency question empirically.

**Read-back dummy-read question: a genuine community conflict, and both
sides can be right.** Several community recipes insist a dummy read
precedes valid GRAM data after `0x2E` (Microchip forum; NXP/emWin
thread m-p/710252), while our hardware shows the first read is real.
The required dummy count varies with interface width and lane mode; our
no-dummy behavior is valid for our 16-bit 8080 setup — re-test if the
bus ever changes. The ~1-word-per-400 panel-scale drop and the
2-trailing-dummy workaround mirror the general experience that long
bursts are where these controllers lose words; burst discipline is the
only fix anyone has.

## 6. Touch (FT5x06 family)

- **Which chip**: the V2.1 board ships the **FT5206** (the board
  datasheet's pin 37 description literally reads "CTP_WAKE … F5206 …
  hibernates to active"); BuyDisplay's product update of 2024-05-28
  says FocalTech discontinued the FT5206 and the upgraded
  **ER-TFTM070-4V3 ships the FT5316**. Never a GT911; FT5216 appears on
  EastRising's separate ER-TPC070-6 module only. Target the FT5x06
  *family*, not one part.
- **I2C address `0x38`** (7-bit) consistently across every driver.
  The `0x70` seen in seller pages is 8-bit notation. Real Pi failure
  case: an EastRising 7" panel probed at ghost addresses
  (0x35/0x60/0x70) while the chip was **hibernating**; the fix was
  `0x38` plus driving CTP_WAKE (pin 37) high
  ([RPi StackExchange
  Q112383](https://raspberrypi.stackexchange.com/questions/112383/edt-ft5x06-touchscreen-probe-failed-with-code-121)).
- **Init: almost nothing.** Every driver — Adafruit FT6206/FT5336
  (MIT), sumotoy's FT5206 (EastRising→Langehaug lineage),
  PyFTtxx6 (MIT, the closest Python precedent), Linux `edt-ft5x06` —
  writes at most `0x00 ← 0` (device mode = working). The real
  requirement is **timing**: ≥300 ms after power-on/reset before the
  first read (datasheet Tpon/Trsi), plus one dummy read to flush
  first-access garbage (kernel practice). Write no config registers,
  no factory mode.
- **Read format**: TD_STATUS `0x02`, touch count = low nibble (0 = none,
  >5 = invalid); 5 touch-point records, 6 bytes each, base addresses
  `0x03, 0x09, 0x0F, 0x15, 0x1B`; each record: XH (bits 7:6 event flag,
  bits 3:0 X[11:8]), XL, YH (bits 7:4 track ID, bits 3:0 Y[11:8]), YL,
  2 weight bytes (ignored by all drivers). Event flags: 00 = down,
  10 = contact/move (the only two worth acting on), 01 = up. 12-bit
  raw coordinates, nominal 0–4095 — the span is set by per-panel
  firmware and contradictory across panels, so expose raw and scale
  with configurable per-axis min/max + optional axis swap/mirror.
  **Measured on our panel (2026-09-06, corner touches): raw spans
  essentially panel-native 0..799 × 0..479** — the driver's default
  calibration uses that span.
- **Never gate on chip-ID registers** (0xA3/0xA6/0xA8 are unreliable
  across firmwares; the two Adafruit libs even swap the meanings of
  0xA3/0xA8). Gestures (0x01) are firmware-dependent; treat as
  optional.
- **INT (pin 36)** is active-low/falling-edge, internal pull-up; polling
  works fine without it — the chip auto-wakes from Monitor mode on
  touch.

Sources: [FT5x06 datasheet](http://www.buydisplay.com/download/ic/FT5206.pdf),
[sumotoy/FT5206](https://github.com/sumotoy/FT5206),
[Adafruit_FT6206](https://github.com/adafruit/Adafruit_FT6206_Library),
[Adafruit_FT5336](https://github.com/adafruit/Adafruit_FT5336_Library),
[PyFTtxx6](https://gitea.tourolle.paris/dtourolle/PyFTtxx6),
[Linux edt-ft5x06.c](https://github.com/torvalds/linux/blob/master/drivers/input/touchscreen/edt-ft5x06.c),
[esp-bsp esp_lcd_touch_ft5x06.c](https://github.com/espressif/esp-bsp/blob/master/components/lcd_touch/esp_lcd_touch_ft5x06/esp_lcd_touch_ft5x06.c),
[ESPHome ft5x06](https://github.com/esphome/esphome/tree/dev/esphome/components/ft5x06),
[BuyDisplay ER-TFTM070-4 page](https://www.buydisplay.com/7-tft-screen-touch-lcd-display-module-w-ssd1963-controller-board-mcu).

## Ranked actionable list

> Status (2026-09-06): items 1 and 2 are **done and verified on
> hardware** — TE wired, `0x35` enabled, measured **53.7 Hz / ~26.2 MHz
> PCLK → 10 MHz crystal** (docs updated); item 4's driver shipped and
> works (multitouch verified; measured raw span is panel-native
> 0..799 × 0..479, not 0..4095); item 3 verified on hardware — the
> current scheme (0x28 + backlight GPIO) suspends and **the picture
> survives the wake cycle** (green → off → green → red test), so the
> `0xB8`-scheme is not worth it. Only item 5's optional extras remain.

1. **Verify the crystal frequency / true refresh rate.** ~~Everything in
   our docs assumes 10 MHz; UTFT's comment implies 12 MHz for the
   boards it targeted. Measure TE on pin 8 (18.9 ms vs 15.8 ms) or
   read back `0xE4`. Correct the docs if 12 MHz.~~ **Done: 10 MHz
   confirmed by TE measurement (53.7 Hz ≈ 929×526/25.8 MHz); `0xE7`
   turned out to return FPR, not kHz.**
2. **Wire TE (pin 8) and set `0x35`** — tear-free full-screen updates
   and the empirical frame-rate measurement for item 1. One GPIO.
3. **Decide the sleep() story.** With `0xB8 = 07 01`, `0x10`/`0x11`
   don't toggle the panel enable; document that `0x28` + backlight off
   is the effective suspend, or move to the `0xB8` p2 B0 = 0 scheme and
   test on hardware (5 ms/120 ms lore, polarity conflict between
   revisions).
4. **Touch driver**: implement `ertftm070.touch` for the FT5x06 family
   per the §6 minimal recipe — MIT precedents exist, so no license
   contamination.
5. **Optional**: hardware 180° fast path via `0x36 = 0x21` (skip —
   software rotation is byte-perfect); re-measure panel-scale read-back
   drops if PCLK is ever raised toward 30 MHz; `0xBC 40 80 40 01` only
   if image quality ever looks wrong on another panel sample.

## License notes

- **UTFT** (incl. its MCUFRIEND_kbv copies): CC BY-NC-SA 3.0,
  non-commercial — **register values and ordering are facts and are
  cited as such; no code was copied**.
- **TFT_eSPI**: BSD-style; **fbrausse/ssd1963**: BSD 2-clause (a
  bit-banged Pi driver, the closest analog to ours);
  **esp_lcd_ssd1963**: BSD 3-clause — permissive references.
- **Adafruit_FT6206 / Adafruit_FT5336 / PyFTtxx6**: MIT — permissive
  touch precedents. **Linux edt-ft5x06.c**: GPL-2.0 — facts only.
- SSD1963 datasheet and ER-TFTM070-4V2.1 datasheet: vendor documents,
  freely redistributed.
- Forum threads (Arduino, RPi, EEVblog, LVGL, PJRC, ST/NXP/Microchip,
  uGFX, StackExchange): user posts, hearsay weight as flagged. All
  register semantics above were checked against datasheet text, not
  forum posts.
