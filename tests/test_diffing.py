"""Diffing: draws emit traffic only for the spans that changed against
the shadow framebuffer — no hardware needed (FakeBus traffic + the
simulator's framebuffer as the end-to-end oracle)."""
from __future__ import annotations

from array import array

import pytest
from PIL import Image

from ertftm070 import Display, rgb565
from ertftm070.pins import DEFAULT_PINS
from ertftm070.simulator import SimulatedBus
from tests.conftest import FakeBus


def _stripes(width: int, red) -> Image.Image:
    """A 1-row RGB image, red at the given columns, black elsewhere."""
    im = Image.new("RGB", (width, 1))
    for x in red:
        im.putpixel((x, 0), (255, 0, 0))
    return im


class ExplodingBus(FakeBus):
    """A bus whose row_blit raises once, on call ``explode_at``."""

    def __init__(self, pins=DEFAULT_PINS):
        super().__init__(pins)
        self._calls = 0
        self.explode_at = None

    def row_blit(self, x0, x1, y, buf):
        self._calls += 1
        if self._calls == self.explode_at:
            raise RuntimeError("boom")
        super().row_blit(x0, x1, y, buf)


@pytest.fixture
def sim_bus():
    """An opened SimulatedBus with the server off (headless)."""
    bus = SimulatedBus(serve=False)
    bus.open()
    try:
        yield bus
    finally:
        bus.close()


@pytest.fixture
def sim_display(sim_bus):
    """A Display on the headless SimulatedBus, row pacing removed."""
    import ertftm070.simulator as simulator

    original_sleep = simulator.time.sleep
    simulator.time.sleep = lambda seconds: None  # noqa: ARG005
    try:
        with Display(backend=sim_bus, auto_init=False, backlight=False) as lcd:
            yield lcd
    finally:
        simulator.time.sleep = original_sleep


# ----------------------------------------------------------------------
# Traffic: what reaches the bus
# ----------------------------------------------------------------------


def test_first_draw_is_full(display, bus):
    display.open()
    display.fill(0)
    # the shadow starts unsynchronized: every row is written in full
    assert bus.row_blit_calls == [(0, 799, y) for y in range(480)]


def test_identical_redraw_emits_nothing(display, bus):
    display.open()
    display.fill(0)
    bus.row_blit_calls.clear()
    display.fill(0)
    assert bus.row_blit_calls == []


def test_partial_change_emits_only_changed_rows(display, bus):
    display.open()
    display.fill(0)
    bus.row_blit_calls.clear()
    display.fill_rect(10, 10, 50, 20, 0xF800)
    assert bus.row_blit_calls == [(10, 59, y) for y in range(10, 30)]


def test_narrow_change_emits_narrow_windows(display, bus):
    display.open()
    display.fill(0)
    bus.row_blit_calls.clear()
    display.fill_rect(400, 0, 20, 480, 0xF800)
    assert bus.row_blit_calls == [(400, 419, y) for y in range(480)]


def test_short_gap_inside_a_row_is_bridged(display, bus):
    display.open()
    display.fill(0)
    bus.row_blit_calls.clear()
    bus.row_blit_words.clear()
    im = _stripes(30, list(range(10)) + list(range(20, 30)))
    display.image(im, x=0, y=100)
    # 10 unchanged words: cheaper to bridge than to open a new window
    assert bus.row_blit_calls == [(0, 29, 100)]
    assert bus.row_blit_words[0] == [0xF800] * 10 + [0] * 10 + [0xF800] * 10


def test_long_gap_inside_a_row_splits_spans(display, bus):
    display.open()
    display.fill(0)
    bus.row_blit_calls.clear()
    im = _stripes(260, list(range(10)) + list(range(250, 260)))
    display.image(im, x=0, y=200)
    # 240 unchanged words: two separate windows
    assert bus.row_blit_calls == [(0, 9, 200), (250, 259, 200)]


def test_scattered_row_falls_back_to_one_span(display, bus, monkeypatch):
    import ertftm070.display as display_module

    monkeypatch.setattr(display_module, "_MAX_SPANS", 3)
    display.open()
    display.fill(0)
    bus.row_blit_calls.clear()
    display.image(_stripes(400, [0, 100, 200, 300]), x=0, y=300)
    assert bus.row_blit_calls == [(0, 399, 300)]


def test_force_rewrites_identical_content(display, bus):
    display.open()
    display.fill(0)
    bus.row_blit_calls.clear()
    display.fill(0, force=True)
    assert bus.row_blit_calls == [(0, 799, y) for y in range(480)]


def test_force_on_fill_rect_image_and_set_pixel(display, bus):
    display.open()
    display.fill_rect(5, 5, 10, 4, 0x07E0)
    bus.row_blit_calls.clear()
    display.fill_rect(5, 5, 10, 4, 0x07E0, force=True)
    assert bus.row_blit_calls == [(5, 14, y) for y in range(5, 9)]

    im = _stripes(3, [0, 1, 2])
    display.image(im, x=20, y=20)
    bus.row_blit_calls.clear()
    display.image(im, x=20, y=20, force=True)
    assert bus.row_blit_calls == [(20, 22, 20)]

    display.set_pixel(1, 1, 0x001F)
    bus.row_blit_calls.clear()
    display.set_pixel(1, 1, 0x001F, force=True)
    assert bus.row_blit_calls == [(1, 1, 1)]


def test_invalidate_forces_a_full_redraw(display, bus):
    display.open()
    display.fill(0)
    bus.row_blit_calls.clear()
    display.invalidate()
    display.fill(0)
    assert bus.row_blit_calls == [(0, 799, y) for y in range(480)]


def test_write_passes_repeat_only_dirty_spans(bus):
    display = Display(backend=bus, write_passes=2)
    display.open()
    display.fill_rect(0, 0, 2, 2, 0x07E0)
    bus.row_blit_calls.clear()
    display.fill_rect(0, 0, 2, 2, 0x07E0)  # identical: nothing, no passes
    assert bus.row_blit_calls == []
    display.fill_rect(0, 0, 2, 1, 0xF800)  # only row 0 changed
    assert bus.row_blit_calls == [(0, 1, 0), (0, 1, 0)]  # once per pass


def test_vsync_diffing_waits_only_for_emitted_spans(display, bus):
    display.open()
    display.fill(0)
    bus.row_blit_calls.clear()
    bus.pin_read_script = [False, True]  # exactly one fresh blanking window
    display.fill_rect(0, 0, 1, 1, 0xF800, vsync=True)
    assert bus.row_blit_calls == [(0, 0, 0)]
    assert bus.pin_read_script == []  # the TE pulse was consumed
    bus.pin_read_script = [False, True]
    display.fill_rect(0, 0, 1, 1, 0xF800, vsync=True)  # unchanged now
    assert bus.pin_read_script == [False, True]  # ...so no TE wait at all


def test_rotation_keeps_the_shadow_valid(display, bus):
    display.open()
    display.fill(0)
    bus.row_blit_calls.clear()
    display.rotation = 90
    display.fill(0)  # same controller-space content, rotated view
    assert bus.row_blit_calls == []


def test_exception_mid_blit_invalidates_the_shadow():
    bus = ExplodingBus()
    display = Display(backend=bus)
    display.open()
    display.fill(0)  # commit a full shadow
    bus.row_blit_calls.clear()
    bus.explode_at = bus._calls + 1  # the next row_blit raises
    with pytest.raises(RuntimeError):
        display.fill_rect(0, 0, 100, 10, 0xF800)
    bus.explode_at = None
    display.fill_rect(0, 0, 100, 10, 0xF800)  # shadow was dropped
    assert bus.row_blit_calls == [(0, 99, y) for y in range(10)]


def test_raw_blit_invalidates_the_shadow(display, bus):
    display.open()
    display.fill(0)
    bus.row_blit_calls.clear()
    display._blit(array("H", [0xF800, 0x07E0]))  # gramcheck-style raw burst
    display.fill(0)
    assert bus.row_blit_calls == [(0, 799, y) for y in range(480)]


def test_set_pixel_unchanged_emits_nothing(display, bus):
    display.open()
    display.fill(0)
    bus.row_blit_calls.clear()
    display.set_pixel(10, 10, 0)  # already black
    assert bus.row_blit_calls == []
    display.set_pixel(10, 10, 0xF800)
    assert bus.row_blit_calls == [(10, 10, 10)]


def test_never_written_pixels_are_always_dirty(display, bus):
    display.open()
    display.fill_rect(5, 5, 10, 4, 0x07E0)  # only (5,5)-(14,8) ever written
    bus.row_blit_calls.clear()
    display.set_pixel(1, 1, 0x0000)  # the panel may hold anything there
    assert bus.row_blit_calls == [(1, 1, 1)]


def test_identical_full_width_rect_emits_nothing(display, bus):
    display.open()
    display.fill(0)
    bus.row_blit_calls.clear()
    display.fill_rect(0, 0, 800, 100, 0)  # full-width window, unchanged
    assert bus.row_blit_calls == []


def test_full_width_window_over_unknown_rows_still_writes(display, bus):
    display.open()
    display.fill_rect(5, 5, 10, 4, 0x07E0)  # rows 0-3 were never written
    bus.row_blit_calls.clear()
    display.fill_rect(0, 0, 800, 4, 0x0000)  # black on unknown rows
    assert bus.row_blit_calls == [(0, 799, y) for y in range(4)]


# ----------------------------------------------------------------------
# Simulator oracle: what the panel ends up showing
# ----------------------------------------------------------------------


def test_shadow_matches_the_sim_framebuffer(sim_display, sim_bus):
    sim_display.fill(rgb565(10, 20, 30))
    sim_display.fill_rect(10, 10, 50, 20, rgb565(200, 30, 90))
    sim_display.set_pixel(0, 0, 0xFFFF)
    assert bytes(sim_display._shadow) == sim_bus.full_frame()


def test_diffed_draws_land_pixel_exact(sim_display, sim_bus):
    sim_display.fill(0)
    sim_display.image(_stripes(100, range(10)), x=400, y=300)
    assert sim_bus.pixel(200, 240) == 0  # untouched background
    assert sim_bus.pixel(400, 300) == 0xF800
    assert sim_bus.pixel(409, 300) == 0xF800
    assert sim_bus.pixel(410, 300) == 0  # beyond the span: still black
    assert bytes(sim_display._shadow) == sim_bus.full_frame()


def test_unwritten_pixels_are_written_even_when_black(sim_display, sim_bus):
    sim_display.fill_rect(5, 5, 10, 4, rgb565(0, 255, 0))
    sim_display.set_pixel(1, 1, 0x0000)  # black must land, not be diffed away
    assert sim_bus.pixel(1, 1) == 0x0000
