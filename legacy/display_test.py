#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
display_test.py -- ER-TFTM070-4V2.1 (SSD1963, 800x480) on Raspberry Pi Zero W
================================================================================
16-bit 8080 MCU parallel interface.  The display board is STRAPPED for
16-bit mode (R3/R4 only select 8080-vs-6800, NOT the bus width), so one
WR cycle = one 16-bit pixel: DB0-7 carry the low byte, DB8-15 the high
byte.  The C helper (fill.c -> 'fill') blasts pixels via /dev/gpiomem;
the whole 800x480 screen updates in ~0.6 s.

Working configuration (verified):
  * init table: UTFT 800 ("utft") with F0=0x03 (16-bit bus),
    0x3A=0x50 (16bpp) set after init, 0x36=0x08 (landscape, BGR order)
  * DDB self-test (0xA1 -> 01 57 61 01 FF) proves the wiring
  * command line:  python3 display_test.py --bars --init=utft --pixfmt=50

Usage:
    python3 display_test.py               # self-test + init + small pattern
    python3 display_test.py --bars        # 8 full-height color bars
    python3 display_test.py --full        # fill whole screen red
    python3 display_test.py --half        # fill left half (bus-width test)
    python3 display_test.py --selftest    # bus self-test only, then exit
    python3 display_test.py --gramcheck2  # helper write + read-back verify
    --init=alt|bd|utft   --b0=XX --seq=XX --mad=XX --pixfmt=XX --invon

Wiring (verified against the ER-TFTM070-4V2.1 datasheet, 40-pin connector):
    display pin 1  VSS      -> Pi GND   (phys. pin 6)
    display pin 2  VDD      -> Pi 5V    (phys. pin 2)
    display pin 3  /CS      -> GPIO26   (phys. pin 37)
    display pin 4  D/C      -> GPIO20   (phys. pin 38)
    display pin 5  E_/RD    -> GPIO16   (phys. pin 36)
    display pin 6  R/W_/WR  -> GPIO21   (phys. pin 40)
    display pin 7  RESET    -> GPIO12   (phys. pin 32)
    display pin 9  DB0      -> GPIO4    (phys. pin 7)
    display pin 10 DB1      -> GPIO17   (phys. pin 11)
    display pin 11 DB2      -> GPIO27   (phys. pin 13)
    display pin 12 DB3      -> GPIO22   (phys. pin 15)
    display pin 13 DB4      -> GPIO5    (phys. pin 29)
    display pin 14 DB5      -> GPIO6    (phys. pin 31)
    display pin 15 DB6      -> GPIO13   (phys. pin 33)
    display pin 16 DB7      -> GPIO19   (phys. pin 35)
    display pin 17 DB8      -> GPIO7    (phys. pin 26)
    display pin 18 DB9      -> GPIO8    (phys. pin 24)
    display pin 19 DB10     -> GPIO9    (phys. pin 21)
    display pin 20 DB11     -> GPIO10   (phys. pin 19)
    display pin 21 DB12     -> GPIO11   (phys. pin 23)
    display pin 22 DB13     -> GPIO18   (phys. pin 12)
    display pin 23 DB14     -> GPIO23   (phys. pin 16)
    display pin 24 DB15     -> GPIO24   (phys. pin 18)
    display pin 39 BL_ON/OFF-> GPIO25   (phys. pin 22)

Electrical notes (from the ER-TFTM070-4V2.1 datasheet, section 4.6):
  * Board VDDIO (logic I/O) is 3.3 V even when VDD = 5 V (J10 open), so the
    Pi's 3.3 V GPIOs are safe AND meet VIH (min 0.8*VDDIO = 2.64 V).
  * VDD needs 4.8-5.2 V at up to 300 mA.  The Pi's 5 V pin works in steady
    state with a good PSU, but the inrush when connecting the display can
    brown out the Pi.  For permanent use, power the display from a
    separate 5 V >= 1 A supply with common ground.
  * /RD idles HIGH (it does).  It is only driven LOW during reads.
  * Register access uses only D[7:0] regardless of bus width, so the
    self-test and init register writes work the same in 8/16-bit mode.
"""

import sys
import time
import argparse

import RPi.GPIO as GPIO


# ----------------------------------------------------------------------
# Pin map (BCM numbering)
# ----------------------------------------------------------------------
DATA_PINS = [4, 17, 27, 22, 5, 6, 13, 19]   # DB0..DB7 (display pins 9-16)
HI_PINS = [7, 8, 9, 10, 11, 18, 23, 24]     # DB8..DB15 (display pins 17-24)
ALL_DATA = DATA_PINS + HI_PINS              # 16-bit pixel bus
CS, DC, WR, RD, RESET, BL = 26, 20, 21, 16, 12, 25

# Pre-computed pin states for every byte value: one tuple lookup instead
# of 8 pin writes per byte.
_BUS_STATES = [tuple((v >> b) & 1 for b in range(8)) for v in range(256)]


# ----------------------------------------------------------------------
# Low-level GPIO helpers
# ----------------------------------------------------------------------
def setup_gpio():
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    GPIO.setup(ALL_DATA, GPIO.OUT, initial=GPIO.LOW)
    for pin in (CS, DC, WR, RESET, BL):
        GPIO.setup(pin, GPIO.OUT, initial=GPIO.HIGH)
    GPIO.setup(RD, GPIO.OUT, initial=GPIO.HIGH)   # write-only unless reading


def cleanup_gpio():
    try:
        GPIO.output(BL, GPIO.LOW)
    except Exception:
        pass
    GPIO.cleanup()


def write_bus(value):
    GPIO.output(DATA_PINS, _BUS_STATES[value])


def strobe_wr():
    GPIO.output(WR, GPIO.LOW)
    GPIO.output(WR, GPIO.HIGH)


def write_command(cmd):
    GPIO.output(CS, GPIO.LOW)
    GPIO.output(DC, GPIO.LOW)          # command cycle
    write_bus(cmd)
    strobe_wr()
    GPIO.output(CS, GPIO.HIGH)


def write_data(value):
    GPIO.output(CS, GPIO.LOW)
    GPIO.output(DC, GPIO.HIGH)         # data cycle
    write_bus(value)
    strobe_wr()
    GPIO.output(CS, GPIO.HIGH)


def write_data_list(values):
    """Several data bytes with CS held low (used by init sequences)."""
    GPIO.output(CS, GPIO.LOW)
    GPIO.output(DC, GPIO.HIGH)
    for value in values:
        write_bus(value)
        strobe_wr()
    GPIO.output(CS, GPIO.HIGH)


def read_bus():
    """Sample DB0-7 while RD is strobed low. Returns one byte."""
    value = 0
    GPIO.output(RD, GPIO.LOW)
    time.sleep(0.000002)               # let the controller drive the bus
    for bit, pin in enumerate(DATA_PINS):
        if GPIO.input(pin):
            value |= (1 << bit)
    GPIO.output(RD, GPIO.HIGH)
    return value


def read_word():
    """Sample all 16 data lines while RD is strobed low. Returns 16 bits."""
    value = 0
    GPIO.output(RD, GPIO.LOW)
    time.sleep(0.000002)               # let the controller drive the bus
    for bit, pin in enumerate(ALL_DATA):
        if GPIO.input(pin):
            value |= (1 << bit)
    GPIO.output(RD, GPIO.HIGH)
    return value


def read_bytes(count):
    """Read `count` data bytes (DC high, CS held low)."""
    for pin in DATA_PINS:
        GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
    out = []
    GPIO.output(CS, GPIO.LOW)
    GPIO.output(DC, GPIO.HIGH)
    for _ in range(count):
        out.append(read_bus())
    GPIO.output(CS, GPIO.HIGH)
    for pin in DATA_PINS:
        GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)
    return out


def hardware_reset():
    print("Hardware reset: RESET low 100 ms, then high, wait 200 ms")
    GPIO.output(RESET, GPIO.LOW)
    time.sleep(0.1)
    GPIO.output(RESET, GPIO.HIGH)
    time.sleep(0.2)


# ----------------------------------------------------------------------
# Bus self-test (no init needed - works right after hardware reset)
# ----------------------------------------------------------------------
def selftest():
    """Read back the SSD1963 DDB (0xA1) and round-trip 0xB8/0xB9.

    A correct SSD1963 answers 0xA1 with: 01 57 61 01 FF
    (supplier ID 0x0157 = Solomon Systech, product 0x61, rev 0x01, exit FF)
    """
    print("-" * 60)
    print("BUS SELF-TEST")
    print("-" * 60)

    # --- Test 1: Device Descriptor Block (read-only, safe) ---
    write_command(0xA1)
    ddb = read_bytes(6)          # read one extra in case of a dummy byte
    print("DDB read-back (0xA1):", ["0x%02X" % b for b in ddb])
    expected = [0x01, 0x57, 0x61, 0x01, 0xFF]
    if ddb[:5] == expected:
        print("  OK: device ID matches SSD1963 exactly")
        ok = True
    elif ddb[1:6] == expected:
        print("  OK: device ID matches (one leading dummy byte)")
        ok = True
    else:
        ok = False
        print("  FAIL: expected 01 57 61 01 FF, got", ["0x%02X" % b for b in ddb[:5]])

    # --- Test 2: GPIO config round trip (write 0xB8, read 0xB9) ---
    write_command(0xB8)
    write_data_list([0x0F, 0x01])
    write_command(0xB9)
    got = read_bytes(3)
    print("0xB8/0xB9 round-trip:", ["0x%02X" % b for b in got])
    if got[:2] == [0x0F, 0x01] or got[1:3] == [0x0F, 0x01]:
        print("  OK: register write/read round-trip works")
    else:
        ok = False
        print("  FAIL: wrote 0F 01, read back", ["0x%02X" % b for b in got[:2]])

    print("-" * 60)
    if ok:
        print("SELF-TEST PASSED: CS, DC, WR, RD and DB0-7 all verified.")
        print("The display is answering - wiring and interface mode are good.")
    else:
        print("SELF-TEST FAILED. The display did not answer correctly.")
        print("If reads return all 0x00 or 0xFF (bus stuck):")
        print("  * check /RD wiring (display pin 5 -> GPIO16)")
        print("  * check DB0-7 wiring and that VDD/VSS really reach the board")
        print("  * check the FPC cable is not flipped or shifted by one pin")
        print("Writes may still work even if reads fail - the init will run")
        print("regardless, so keep watching the screen.")
    print("-" * 60)
    return ok


# ----------------------------------------------------------------------
# Init sequences (byte-for-byte copies of known-good tables)
# ----------------------------------------------------------------------
#
# "alt" = UTFT library's SSD1963_800ALT table (TFT_eSPI copy), the table
#         community-verified for the ER-TFTM070-4V2.1 on the Arduino
#         forum.  PLL = 120 MHz, PCLK = 34.3 MHz, HT=928/VT=525.
#
# "bd"  = EastRising's own example code ("Copied from Buy Display code"
#         in TFT_eSPI's SSD1963_800BD).  PLL = 120 MHz, PCLK = 24.0 MHz,
#         HT=1055/VT=524.
#
# Each entry: (command, [data bytes...]) or a float = delay in seconds.
#
# Notes:
#  * 0xE2: PLL M=0x23 (35), N=0x02 -> VCO = 10 MHz * 36 = 360 MHz,
#    PLL = 360/3 = 120 MHz.  Last byte (0x04/0x54) applies M/N.
#  * 0xE6: LSHIFT = PLL * (FPR+1)/2^20.
#      alt: FPR=0x0493E0=300000 -> 34.33 MHz
#      bd:  FPR=0x033333=209715 -> 24.0 MHz
#    (the old script's 0x00FFBE -> ~7 MHz: far too slow to scan)
#  * 0xB8/0xBA: configure SSD1963 GPIOs as outputs and drive GPIO0 = 1.
#    On this board GPIO0 is the panel's display on/off control.
#  * 0x36: address mode 0x00 = landscape, no flip, RGB order. If red and
#    blue appear swapped, change to 0x08 (BGR). If the image is mirrored
#    or rotated, see the SSD1963 datasheet 0x36 bits and adjust.
#  * Deliberately NOT included (matching the known-good tables exactly):
#    0x11 (exit sleep) and 0x3A (pixel format).

INIT_ALT = [
    (0xE2, [0x23, 0x02, 0x04]),   # PLL M/N -> 120 MHz
    (0xE0, [0x01]),               # PLL enable
    0.1,                          # wait for PLL to lock (10 ms is common, 100 ms is safe)
    (0xE0, [0x03]),               # lock PLL; system clock = PLL
    0.01,
    (0x01, []),                   # software reset
    0.1,                          # must be >= 5 ms
    (0xE6, [0x04, 0x93, 0xE0]),   # LSHIFT = 34.33 MHz
    (0xB0, [0x00, 0x00, 0x03, 0x1F, 0x01, 0xDF, 0x00]),  # 800x480 TFT
    (0xB4, [0x03, 0xA0, 0x00, 0x2E, 0x30, 0x00, 0x0F, 0x00]),  # HSYNC
    (0xB6, [0x02, 0x0D, 0x00, 0x10, 0x10, 0x00, 0x08]),        # VSYNC
    (0xBA, [0x05]),               # GPIO[3:0] output, GPIO0=1
    (0xB8, [0x07, 0x01]),         # GPIO3=input, GPIO[2:0]=output, GPIO0 normal
    (0x36, [0x08]),               # address mode: landscape, BGR order: landscape, RGB
    (0xF0, [0x03]),               # 16-bit MCU data bus, 565 format 1
    0.01,
    (0x29, []),                   # display ON
    0.1,
]

INIT_BD = [
    (0xE2, [0x23, 0x02, 0x54]),   # PLL M/N -> 120 MHz
    (0xE0, [0x01]),
    0.1,
    (0xE0, [0x03]),
    0.01,
    (0x01, []),
    0.1,
    (0xE6, [0x03, 0x33, 0x33]),   # LSHIFT = 24.0 MHz
    (0xB0, [0x20, 0x00, 0x03, 0x1F, 0x01, 0xDF, 0x00]),  # 800x480 TFT
    (0xB4, [0x04, 0x1F, 0x00, 0xD2, 0x00, 0x00, 0x00, 0x00]),  # HSYNC
    (0xB6, [0x02, 0x0C, 0x00, 0x22, 0x00, 0x00, 0x00]),        # VSYNC
    (0xB8, [0x0F, 0x01]),         # all GPIO output, GPIO0 normal
    (0xBA, [0x01]),               # GPIO0 = 1 (panel display on)
    (0x36, [0x08]),               # address mode: landscape, BGR order: landscape, RGB
    (0xF0, [0x03]),               # 16-bit MCU data bus, 565 format 1
    (0xBC, [0x40, 0x80, 0x40, 0x01]),  # post processor enable
    0.01,
    (0x29, []),                   # display ON
    0.1,
]


# Complete original UTFT v2.81 SSD1963_800 table (includes Display Off,
# Sleep Out, Display On, and B0 byte1 = 0x24: 24-bit panel width +
# LSHIFT latch on falling edge).
INIT_UTFT = [
    (0xE2, [0x1E, 0x02, 0x54]),   # PLL M=30,N=2 -> ~103 MHz
    (0xE0, [0x01]),
    0.1,
    (0xE0, [0x03]),
    0.01,
    (0x01, []),
    0.1,
    (0xE6, [0x03, 0xFF, 0xFF]),   # LSHIFT ~ 25.8 MHz
    (0xB0, [0x24, 0x00, 0x03, 0x1F, 0x01, 0xDF, 0x00]),
    (0xB4, [0x03, 0xA0, 0x00, 0x2E, 0x30, 0x00, 0x0F, 0x00]),
    (0xB6, [0x02, 0x0D, 0x00, 0x10, 0x10, 0x00, 0x08]),
    (0xBA, [0x0F]),               # GPIO[3:0] out 1
    (0xB8, [0x07, 0x01]),         # GPIO3=input, GPIO[2:0]=output, GPIO0 normal
    (0xF0, [0x03]),               # 16-bit bus
    0.001,
    (0x28, []),                   # display off
    (0x11, []),                   # sleep out
    0.1,
    (0x36, [0x08]),               # address mode: landscape, BGR order
    (0x29, []),                   # display on
    0.1,
    (0xBE, [0x06, 0xF0, 0x01, 0xF0, 0x00, 0x00]),
    (0xD0, [0x0D]),
]


def init_display(table, name, overrides=None):
    """Send an init table.  overrides = {cmd: [new data bytes...]} applied
    on the fly (used for quick register sweeps)."""
    print("Initializing SSD1963 (table: %s)..." % name)
    for entry in table:
        if isinstance(entry, tuple):
            cmd, data = entry
            if overrides and cmd in overrides:
                data = list(overrides[cmd])
                print("  [override] cmd 0x%02X data %s" % (cmd, data))
            write_command(cmd)
            if data:
                write_data_list(data)
        else:
            time.sleep(entry)      # delay entry
    print("Init complete.")


# ----------------------------------------------------------------------
# Drawing
# ----------------------------------------------------------------------
def set_window(x0, y0, x1, y1):
    write_command(0x2A)  # column address
    write_data_list([(x0 >> 8) & 0xFF, x0 & 0xFF,
                     (x1 >> 8) & 0xFF, x1 & 0xFF])
    write_command(0x2B)  # page (row) address
    write_data_list([(y0 >> 8) & 0xFF, y0 & 0xFF,
                     (y1 >> 8) & 0xFF, y1 & 0xFF])


def fill_rect(x0, y0, x1, y1, color):
    """Fill rectangle with an RGB565 color. CS held low for the stream."""
    hi, lo = (color >> 8) & 0xFF, color & 0xFF
    hi_state, lo_state = _BUS_STATES[hi], _BUS_STATES[lo]
    width, height = x1 - x0 + 1, y1 - y0 + 1
    total = width * height

    set_window(x0, y0, x1, y1)
    write_command(0x2C)            # memory write
    GPIO.output(CS, GPIO.LOW)
    GPIO.output(DC, GPIO.HIGH)

    t0 = time.time()
    for i in range(total):
        GPIO.output(DATA_PINS, hi_state)
        strobe_wr()
        GPIO.output(DATA_PINS, lo_state)
        strobe_wr()
        if (i + 1) % (total // 4 + 1) == 0:
            pct = (i + 1) * 100 // total
            print("  %d%%" % pct)
    dt = time.time() - t0

    GPIO.output(CS, GPIO.HIGH)
    print("  done in %.1f s (%.0f px/s)" % (dt, total / dt))


RGB565 = {"red": 0xF800, "green": 0x07E0, "blue": 0x001F,
          "white": 0xFFFF, "black": 0x0000, "yellow": 0xFFE0,
          "cyan": 0x07FF, "magenta": 0xF81F}


def gram_check():
    """Write 4 known pixels to GRAM, read them back via 0x2E, compare.

    This tells us whether the Pi->SSD1963 write path stores the bytes
    we think it stores.  Expected stream: 00 00 F8 00 07 E0 00 1F
    (black, red, green, blue in RGB565, high byte first)
    """
    print("-" * 60)
    print("GRAM WRITE/READ CHECK")
    print("-" * 60)
    pixels = [0x0000, 0xF800, 0x07E0, 0x001F, 0xFFFF, 0xFFE0, 0x07FF, 0xF81F]
    #          black   red    green  blue   white  yellow cyan   magenta

    set_window(0, 0, 3, 1)
    write_command(0x2C)                      # memory write
    GPIO.output(CS, GPIO.LOW)
    GPIO.output(DC, GPIO.HIGH)
    for p in pixels:
        GPIO.output(DATA_PINS, _BUS_STATES[(p >> 8) & 0xFF])
        strobe_wr()
        GPIO.output(DATA_PINS, _BUS_STATES[p & 0xFF])
        strobe_wr()
    GPIO.output(CS, GPIO.HIGH)
    time.sleep(0.05)

    set_window(0, 0, 3, 1)
    write_command(0x2E)                      # memory read
    for pin in DATA_PINS:
        GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
    GPIO.output(CS, GPIO.LOW)
    GPIO.output(DC, GPIO.HIGH)
    dummy = read_bus()                       # first byte after 0x2E is a dummy
    got = [read_bus() for _ in range(16)]
    GPIO.output(CS, GPIO.HIGH)
    for pin in DATA_PINS:
        GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)

    expected = []
    for p in pixels:
        expected += [(p >> 8) & 0xFF, p & 0xFF]
    print("dummy byte : 0x%02X" % dummy)
    print("read back  :", ["0x%02X" % b for b in got])
    print("expected   :", ["0x%02X" % b for b in expected])
    if got == expected:
        print("GRAM OK: the controller stored exactly what we sent.")
    elif got[1:] == expected[:15] or got == [dummy] + expected[:15]:
        print("GRAM OK (one extra dummy byte): data matches.")
    else:
        print("GRAM MISMATCH: compare the bytes above.")
        print("  * all 0x00/0xFF -> bus stuck during writes")
        print("  * low bytes zero -> second WR strobe per pixel lost")
        print("  * shifted -> byte order / format issue")
    print("-" * 60)


def gram_check_helper():
    """Write 8 known pixels using the C helper, read back via 0x2E.

    Each helper call writes 1 pixel + 2 trailing dummies, so test pixels
    are placed 3 addresses apart in a 24-wide window.
    """
    import subprocess
    import os
    helper = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fill")

    print("-" * 60)
    print("GRAM CHECK VIA C HELPER (helper writes, python reads)")
    print("-" * 60)
    print("single run: fill 0x1234, 20x1 (+2 trailing) = 44 bytes")
    set_window(0, 0, 21, 1)
    write_command(0x2C)
    GPIO.output(CS, GPIO.LOW)
    GPIO.output(DC, GPIO.HIGH)
    subprocess.run([helper, "0x1234", "20", "1"], check=True)
    GPIO.output(CS, GPIO.HIGH)
    time.sleep(0.05)

    set_window(0, 0, 21, 1)
    write_command(0x2E)
    for pin in ALL_DATA:
        GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
    GPIO.output(CS, GPIO.LOW)
    GPIO.output(DC, GPIO.HIGH)
    dummy = read_word()
    got = [read_word() for _ in range(22)]
    GPIO.output(CS, GPIO.HIGH)
    for pin in ALL_DATA:
        GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)

    print("dummy : 0x%04X" % dummy)
    for i in range(0, 22, 6):
        print("  %2d:" % i, " ".join("0x%04X" % w for w in got[i:i+6]))
    bad = [i for i, w in enumerate(got) if w != 0x1234]
    print("word positions that differ from 0x1234:", bad)
    print("-" * 60)


def draw_small_pattern():
    """Five 100x100 squares - fast (~50k pixels), impossible to miss if
    the panel is scanning, and each color confirms the 565 mapping."""
    print("Drawing small test pattern (5 x 100x100 squares)...")
    squares = [("red", 0, 0), ("green", 120, 0), ("blue", 240, 0),
               ("white", 360, 0), ("black", 480, 0)]
    for name, x, y in squares:
        print("  %s square at (%d,%d)" % (name, x, y))
        fill_rect(x, y, x + 99, y + 99, RGB565[name])
    print("Pattern complete.")


def draw_half():
    """Fill ONLY the left half of the screen red.

    8-bit mode  -> left half red, right half noise
    16-bit mode -> the write stream is pixel-doubled, so the WHOLE
                   screen fills with (striped) red content.
    """
    import subprocess
    import os
    helper = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fill")
    print("Filling LEFT HALF (400x480) with red...")
    set_window(0, 0, 399, 479)
    write_command(0x2C)
    GPIO.output(CS, GPIO.LOW)
    GPIO.output(DC, GPIO.HIGH)
    subprocess.run([helper, "0xF800", "400", "480"], check=True)
    GPIO.output(CS, GPIO.HIGH)
    print("Done. 8-bit: left half red only.  16-bit: whole screen red-ish.")


def draw_full_red():
    print("Filling the whole 800x480 screen RED...")
    fill_rect(0, 0, 799, 479, RGB565["red"])
    print("Full red fill complete.")


def draw_bars():
    """Eight full-height 100x480 bars using the fast C helper.

    Left to right: black, red, green, blue, white, yellow, cyan, magenta.
    Each bar = one fill call (subprocess) - the whole screen in seconds.
    """
    import subprocess
    import os
    helper = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fill")
    if not os.path.exists(helper):
        print("ERROR: helper binary 'fill' not found next to this script.")
        print("Build it with:  gcc -O2 -o fill fill.c")
        return

    bars = [("black", 0x0000), ("red", 0xF800), ("green", 0x07E0),
            ("blue", 0x001F), ("white", 0xFFFF), ("yellow", 0xFFE0),
            ("cyan", 0x07FF), ("magenta", 0xF81F)]

    print("Drawing 8 full-height color bars with the fast C helper...")
    print("Expected left->right: %s" % ", ".join(n for n, _ in bars))

    set_window(0, 0, 799, 479)
    write_command(0x2C)                      # memory write
    GPIO.output(CS, GPIO.LOW)
    GPIO.output(DC, GPIO.HIGH)

    t0 = time.time()
    # one process, one continuous burst across all 8 bars
    args = [helper]
    for name, color in bars:
        print("  %s bar..." % name)
        args += ["0x%04X" % color, "100", "480"]
    subprocess.run(args, check=True)
    dt = time.time() - t0

    GPIO.output(CS, GPIO.HIGH)
    print("All 8 bars done in %.1f s." % dt)
    print("Report the colors you see, LEFT to RIGHT, including any that")
    print("look wrong or black.")


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="ER-TFTM070-4V2.1 SSD1963 test")
    parser.add_argument("--selftest", action="store_true",
                        help="run bus self-test only, then exit")
    parser.add_argument("--full", action="store_true",
                        help="fill the whole screen red instead of the small pattern")
    parser.add_argument("--gramcheck", action="store_true",
                        help="write 4 known pixels to GRAM, read them back, exit")
    parser.add_argument("--gramcheck2", action="store_true",
                        help="gram check using the C helper for writes, exit")
    parser.add_argument("--bars", action="store_true",
                        help="draw 8 full-height color bars (needs 'fill' helper binary)")
    parser.add_argument("--half", action="store_true",
                        help="fill only the left half red (8-vs-16 bit mode test)")
    parser.add_argument("--b0", metavar="HEX",
                        help="override 0xB0 byte 1 (panel width/polarity bits)")
    parser.add_argument("--seq", metavar="HEX",
                        help="override 0xB0 byte 7 (even/odd line RGB sequence)")
    parser.add_argument("--mad", metavar="HEX",
                        help="override 0x36 address mode byte")
    parser.add_argument("--invon", action="store_true",
                        help="send 0x21 (display inversion ON) after init")
    parser.add_argument("--invmode", metavar="HEX",
                        help="send 0x20/0x21 (0=off,1=on) - same as --invon but explicit")
    parser.add_argument("--pixfmt", metavar="HEX",
                        help="set 0x3A pixel format after init (e.g. 50 = 16bpp)")
    parser.add_argument("--init", choices=["alt", "bd", "utft"], default="alt",
                        help="init table: alt (default), bd (EastRising), utft (original UTFT 800)")
    args = parser.parse_args()

    tables = {"alt": INIT_ALT, "bd": INIT_BD, "utft": INIT_UTFT}

    try:
        setup_gpio()
        hardware_reset()
        selftest()
        if args.selftest:
            print("Self-test only requested - exiting.")
            return

        overrides = {}
        if args.b0 is not None:
            t = tables[args.init]
            b0_entry = next(e for e in t if isinstance(e, tuple) and e[0] == 0xB0)
            data = list(b0_entry[1])
            data[0] = int(args.b0, 16)
            overrides[0xB0] = data
        if args.seq is not None:
            t = tables[args.init]
            b0_entry = next(e for e in t if isinstance(e, tuple) and e[0] == 0xB0)
            data = list(overrides.get(0xB0, b0_entry[1]))
            data[6] = int(args.seq, 16)
            overrides[0xB0] = data
        if args.mad is not None:
            overrides[0x36] = [int(args.mad, 16)]

        init_display(tables[args.init], args.init, overrides)

        if args.invon or (args.invmode is not None and int(args.invmode, 16) == 1):
            print("Display inversion ON (0x21)")
            write_command(0x21)
        elif args.invmode is not None:
            print("Display inversion OFF (0x20)")
            write_command(0x20)

        if args.pixfmt is not None:
            val = int(args.pixfmt, 16)
            print("Setting 0x3A (pixel format) = 0x%02X (post-init)" % val)
            write_command(0x3A)
            write_data(val)

        print("Turning backlight on...")
        GPIO.output(BL, GPIO.HIGH)
        time.sleep(0.3)

        print("Watch the panel: if the init took effect, the screen should")
        print("have changed from uniform white to noise/random content,")
        print("even before anything is drawn.")

        if args.gramcheck:
            gram_check()
            print("GRAM check done - exiting (backlight off).")
            return

        if args.gramcheck2:
            gram_check_helper()
            print("GRAM check done - exiting (backlight off).")
            return

        if args.bars:
            draw_bars()
        elif args.half:
            draw_half()
        elif args.full:
            draw_full_red()
        else:
            draw_small_pattern()

        print()
        print("Display test complete.")
        print("If colors are wrong but you see SOMETHING: check the 0x36")
        print("(address mode) note at the top of this file - red<->blue")
        print("swap means BGR order (use 0x08).")
        print("If the screen is still white, try: python3 %s --init=bd" %
              sys.argv[0])
        print("Press Ctrl+C to exit (backlight off, GPIO released).")

        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\nStopping...")

    finally:
        cleanup_gpio()


if __name__ == "__main__":
    main()
