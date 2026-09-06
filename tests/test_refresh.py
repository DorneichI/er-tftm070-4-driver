"""Refresh measurement and TE vsync against the FakeBus."""
from __future__ import annotations

import pytest

import ertftm070.display as display_module
from ertftm070.display import Display, crystal_guess
from ertftm070.init import INIT_ALT, INIT_BD
from ertftm070.pins import Pins
from tests.conftest import FakeBus
from tests.test_touch import _FakeClock

# ----------------------------------------------------------------------
# crystal_guess (pure)
# ----------------------------------------------------------------------


def test_crystal_guess_names_known_crystals():
    # PCLK = ref_MHz x 31 / 3 / 4 in kHz, so ref = PCLK x 12 / 31000 MHz
    assert crystal_guess(25833) == "10 MHz"
    assert crystal_guess(31000) == "12 MHz"
    assert crystal_guess(16791) == "6.5 MHz"
    assert crystal_guess(20000).startswith("unknown")


def test_crystal_guess_reads_the_clock_chain_from_any_table():
    # INIT_ALT: PLL x12, FPR 0x0493E0 -> PCLK = ref x 3.4332 -> 34.333 MHz
    assert crystal_guess(34333, INIT_ALT) == "10 MHz"
    # INIT_BD: PLL x12, FPR 0x033333 -> PCLK = ref / 5 -> 24.0 MHz
    assert crystal_guess(24000, INIT_BD) == "10 MHz"
    # the utft pixel clock does not fit the alt table's chain -> unknown
    assert crystal_guess(25833, INIT_ALT).startswith("unknown")


def test_crystal_guess_requires_the_clock_entries():
    with pytest.raises(ValueError):
        crystal_guess(20000, [])


# ----------------------------------------------------------------------
# read_register
# ----------------------------------------------------------------------


def test_read_register_masks_low_bytes():
    display = Display(backend=FakeBus())
    display.open()
    bus = display.bus
    bus.read_words = [0x1234, 0x5678, 0x9ABC]
    # register reads always arrive on D[7:0] — low byte of each word
    assert display.read_register(0xE7, 3) == [0x34, 0x78, 0xBC]
    assert bus.commands()[-1] == 0xE7


# ----------------------------------------------------------------------
# vsync_wait
# ----------------------------------------------------------------------


def test_vsync_wait_blocks_until_te_high(display, bus):
    display.open()
    bus.pin_read_script = [False, False, True]
    display.vsync_wait(timeout=5)  # returns as soon as TE is high


def test_vsync_wait_times_out_when_te_stays_low(display, bus, monkeypatch):
    display.open()
    monkeypatch.setattr(display_module.time, "sleep", lambda s: None)
    monkeypatch.setattr(display_module.time, "monotonic", _FakeClock())
    bus.pin_read_script = [False] * 50
    with pytest.raises(TimeoutError):
        display.vsync_wait(timeout=0.1)


def test_blit_rows_with_vsync_waits_per_row(display, bus):
    display.open()
    bus.pin_read_script = [False, True] * 3  # one TE pulse per row
    display.fill_rect(0, 0, 2, 3, 0xF800, vsync=True)
    # 3 rows, one burst each, every burst gated on a blanking start
    assert [c for c in bus.row_blit_calls] == [(0, 1, 0), (0, 1, 1), (0, 1, 2)]


def test_blit_rows_with_vsync_waits_for_a_fresh_window(display, bus):
    # a vsynced row must start at a FRESH blanking start: TE high at the
    # call (a window already in progress) is not enough — the driver
    # waits for the fall, then the next rise
    display.open()
    bus.pin_read_script = [True, False, True, False, True]
    display.fill_rect(0, 0, 2, 1, 0xF800, vsync=True)
    # high (in progress), low, high consumed for the one row's window;
    # the second pulse is left unconsumed
    assert bus.pin_read_script == [False, True]
    assert [c for c in bus.row_blit_calls] == [(0, 1, 0)]


def test_blit_rows_without_vsync_does_not_wait(display, bus):
    display.open()
    display.fill_rect(0, 0, 2, 3, 0xF800)  # default: no TE gating
    assert len(bus.row_blit_calls) == 3


# ----------------------------------------------------------------------
# refresh_rate: TE period + init-table scan totals
# ----------------------------------------------------------------------


def test_scan_totals_reads_b4_b6_from_the_init_table(display):
    # 0xB4 = 03 A0 ... -> HT 0x3A0 = 928, +1 = 929
    # 0xB6 = 02 0D ... -> VT 0x20D = 525, +1 = 526
    assert display._scan_totals() == (929, 526)


def test_refresh_rate_derives_pclk_from_te(display, bus, monkeypatch):
    display.open()
    # _te_period_seconds(samples=2): settle to low, wait rising, then
    # two rising-edge timestamps; the fake clock makes the gap 50 ms
    bus.pin_read_script = [False, True, False, True, False, True]
    monkeypatch.setattr(display_module.time, "sleep", lambda s: None)
    monkeypatch.setattr(display_module.time, "monotonic", _FakeClock())
    pclk_khz, hz = display.refresh_rate(samples=2)
    assert hz == pytest.approx(20.0)
    # PCLK = hz x HT x VT = 20 x 929 x 526 / 1000 kHz
    assert pclk_khz == 9773


def test_refresh_rate_times_out_without_te(display, bus, monkeypatch):
    display.open()
    bus.pin_read_script = [False] * 100
    monkeypatch.setattr(display_module.time, "sleep", lambda s: None)
    monkeypatch.setattr(display_module.time, "monotonic", _FakeClock())
    with pytest.raises(TimeoutError):
        display.refresh_rate(samples=2, timeout=0.1)


def test_refresh_rate_needs_at_least_two_samples(display, bus):
    display.open()
    with pytest.raises(ValueError):
        display.refresh_rate(samples=1)


def test_te_none_skips_0x35_and_raises_for_te_timing(bus, monkeypatch):
    # Pins.te=None means no TE wire: init must not enable 0x35 (the pin
    # is not configured either), and TE-based timing raises RuntimeError
    # instead of hanging on a pin nobody wired
    monkeypatch.setattr(display_module.time, "sleep", lambda s: None)
    d = Display(pins=Pins(te=None), backend=bus)
    d.open()
    assert 0x35 not in bus.commands()
    assert 14 not in bus.pin_modes  # TE GPIO never touched
    with pytest.raises(RuntimeError):
        d.vsync_wait()
    with pytest.raises(RuntimeError):
        d.refresh_rate()
    # the rest of the drawing API is unaffected
    d.fill(0)
    assert len(bus.row_blit_calls) == d.height
