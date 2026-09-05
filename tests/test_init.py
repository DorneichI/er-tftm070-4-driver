"""Invariants of the verified init table."""
from __future__ import annotations

from ertftm070.init import INIT_ALT, INIT_BD, INIT_UTFT


def _commands(table):
    return {cmd for cmd, _ in (e for e in table if isinstance(e, tuple))}


def _data_for(table, cmd):
    for c, data in (e for e in table if isinstance(e, tuple)):
        if c == cmd:
            return data
    raise AssertionError(f"command 0x{cmd:02X} not in table")


def test_utft_has_the_verified_registers():
    cmds = _commands(INIT_UTFT)
    for cmd in (0xE2, 0xE0, 0x01, 0xE6, 0xB0, 0xB4, 0xB6, 0xBA, 0xB8, 0x28, 0x11, 0x29):
        assert cmd in cmds, f"missing 0x{cmd:02X}"


def test_utft_16bit_bus_and_bgr():
    # F0=0x03: 16-bit data bus, 565 format 1 — must match the board strap
    assert _data_for(INIT_UTFT, 0xF0) == [0x03]
    # 0x36=0x08: landscape + BGR (red/blue swap lives in the controller)
    assert _data_for(INIT_UTFT, 0x36) == [0x08]


def test_utft_panel_display_on_via_gpio0():
    # GPIO0 is wired to the panel's DISP line on EastRising boards
    assert _data_for(INIT_UTFT, 0xB8) == [0x07, 0x01]
    assert _data_for(INIT_UTFT, 0xBA) == [0x0F]


def test_utft_pll_and_pixel_clock():
    assert _data_for(INIT_UTFT, 0xE2) == [0x1E, 0x02, 0x54]  # ~103 MHz
    assert _data_for(INIT_UTFT, 0xE6) == [0x03, 0xFF, 0xFF]  # ~26 MHz


def test_3a_is_not_in_the_table():
    # 0x3A=0x50 is applied by Display._init AFTER the table, matching the
    # order of the verified driver — it must not sneak into the table.
    assert 0x3A not in _commands(INIT_UTFT)


def test_delay_entries_are_floats():
    delays = [e for e in INIT_UTFT if not isinstance(e, tuple)]
    assert delays, "the table needs its PLL lock / reset delays"
    assert all(isinstance(d, float) for d in delays)


def test_alt_and_bd_are_usable_fallbacks():
    for table in (INIT_ALT, INIT_BD):
        assert _data_for(table, 0xF0) == [0x03]  # 16-bit bus everywhere
        assert _data_for(table, 0x36) == [0x08]  # BGR everywhere
        assert 0xE2 in _commands(table)
