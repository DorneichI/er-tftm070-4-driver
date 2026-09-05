"""Backend internals that don't need /dev/gpiomem."""
from __future__ import annotations

from ertftm070.backends import _byte_mask, _MmioBus, get_backend
from ertftm070.pins import DEFAULT_PINS
from tests.conftest import FakeBus


def test_byte_mask_maps_bit_i_to_pin_i():
    mask = _byte_mask(0x81, DEFAULT_PINS.data_low)  # bits 0 and 7
    assert mask == (1 << DEFAULT_PINS.data_low[0]) | (1 << DEFAULT_PINS.data_low[7])


def test_precomputed_tables_cover_all_low_pins():
    bus = _MmioBus(DEFAULT_PINS)
    assert bus._set_low[0xFF] == bus._clr_low  # all 8 low pins set
    assert bus._set_high[0xFF] == bus._clr_high
    assert bus._clr_all == bus._clr_low | bus._clr_high


def test_precomputed_tables_are_disjoint():
    bus = _MmioBus(DEFAULT_PINS)
    assert bus._clr_low & bus._clr_high == 0


def test_get_backend_passes_explicit_backend_through():
    fake = FakeBus()
    assert get_backend(DEFAULT_PINS, backend=fake) is fake
