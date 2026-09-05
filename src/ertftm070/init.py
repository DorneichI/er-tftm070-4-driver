"""SSD1963 initialization sequences for the ER-TFTM070-4V2.1.

Each table is a list of entries: ``(command, [data bytes...])`` tuples
are written as command-then-data; a bare float is a delay in seconds.

``INIT_UTFT`` is the one verified on this hardware.  The other two are
kept because they were instrumental during debugging and may help if a
different board revision misbehaves — see ``docs/INIT-SEQUENCE.md`` and
``docs/LESSONS.md``.
"""
from __future__ import annotations

from typing import Union

Entry = Union[tuple[int, list[int]], float]
Table = list[Entry]

# Complete original UTFT v2.81 SSD1963_800 table (includes Display Off,
# Sleep Out, Display On, and B0 byte 1 = 0x24: 24-bit panel width +
# LSHIFT latch on falling edge).  VERIFIED on the ER-TFTM070-4V2.1.
INIT_UTFT: Table = [
    (0xE2, [0x1E, 0x02, 0x54]),  # PLL M=30, N=2 -> ~103 MHz
    (0xE0, [0x01]),  # PLL enable
    0.1,
    (0xE0, [0x03]),  # lock PLL; system clock = PLL
    0.01,
    (0x01, []),  # software reset
    0.1,
    (0xE6, [0x03, 0xFF, 0xFF]),  # pixel clock LSHIFT ~ 25.8 MHz
    (0xB0, [0x24, 0x00, 0x03, 0x1F, 0x01, 0xDF, 0x00]),  # 800x480 TFT
    (0xB4, [0x03, 0xA0, 0x00, 0x2E, 0x30, 0x00, 0x0F, 0x00]),  # HSYNC
    (0xB6, [0x02, 0x0D, 0x00, 0x10, 0x10, 0x00, 0x08]),  # VSYNC
    (0xBA, [0x0F]),  # GPIO[3:0] as outputs, all high
    (0xB8, [0x07, 0x01]),  # GPIO3=input, GPIO[2:0]=output, GPIO0 normal
    (0xF0, [0x03]),  # 16-bit MCU data bus, 565 format 1
    0.001,
    (0x28, []),  # display off
    (0x11, []),  # sleep out
    0.1,
    (0x36, [0x08]),  # address mode: landscape, BGR order
    (0x29, []),  # display on
    0.1,
    (0xBE, [0x06, 0xF0, 0x01, 0xF0, 0x00, 0x00]),  # backlight PWM config
    (0xD0, [0x0D]),  # dynamic backlight control config
]

# UTFT's SSD1963_800ALT table — community-verified for the 800x480 board
# family on the Arduino forum.  PLL = 120 MHz, PCLK = 34.3 MHz.
INIT_ALT: Table = [
    (0xE2, [0x23, 0x02, 0x04]),  # PLL M/N -> 120 MHz
    (0xE0, [0x01]),
    0.1,
    (0xE0, [0x03]),
    0.01,
    (0x01, []),
    0.1,
    (0xE6, [0x04, 0x93, 0xE0]),  # LSHIFT = 34.33 MHz
    (0xB0, [0x00, 0x00, 0x03, 0x1F, 0x01, 0xDF, 0x00]),  # 800x480 TFT
    (0xB4, [0x03, 0xA0, 0x00, 0x2E, 0x30, 0x00, 0x0F, 0x00]),  # HSYNC
    (0xB6, [0x02, 0x0D, 0x00, 0x10, 0x10, 0x00, 0x08]),  # VSYNC
    (0xBA, [0x05]),  # GPIO[3:0] output, GPIO0=1
    (0xB8, [0x07, 0x01]),  # GPIO3=input, GPIO[2:0]=output, GPIO0 normal
    (0x36, [0x08]),  # landscape, BGR
    (0xF0, [0x03]),  # 16-bit bus, 565 format 1
    0.01,
    (0x29, []),
    0.1,
]

# EastRising's own example code ("Copied from Buy Display code" in
# TFT_eSPI's SSD1963_800BD).  PLL = 120 MHz, PCLK = 24.0 MHz.
INIT_BD: Table = [
    (0xE2, [0x23, 0x02, 0x54]),  # PLL M/N -> 120 MHz
    (0xE0, [0x01]),
    0.1,
    (0xE0, [0x03]),
    0.01,
    (0x01, []),
    0.1,
    (0xE6, [0x03, 0x33, 0x33]),  # LSHIFT = 24.0 MHz
    (0xB0, [0x20, 0x00, 0x03, 0x1F, 0x01, 0xDF, 0x00]),  # 800x480 TFT
    (0xB4, [0x04, 0x1F, 0x00, 0xD2, 0x00, 0x00, 0x00, 0x00]),  # HSYNC
    (0xB6, [0x02, 0x0C, 0x00, 0x22, 0x00, 0x00, 0x00]),  # VSYNC
    (0xB8, [0x0F, 0x01]),  # all GPIO output, GPIO0 normal
    (0xBA, [0x01]),  # GPIO0 = 1 (panel display on)
    (0x36, [0x08]),  # landscape, BGR
    (0xF0, [0x03]),  # 16-bit bus, 565 format 1
    (0xBC, [0x40, 0x80, 0x40, 0x01]),  # post processor enable
    0.01,
    (0x29, []),
    0.1,
]
