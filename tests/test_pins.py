"""Pin configuration sanity."""
from __future__ import annotations

import pytest

from ertftm070.pins import DEFAULT_PINS, Pins


def test_default_pins_shape():
    assert len(DEFAULT_PINS.data_low) == 8
    assert len(DEFAULT_PINS.data_high) == 8
    assert len(DEFAULT_PINS.data) == 16


def test_default_pins_unique_and_in_range():
    pins = list(DEFAULT_PINS.data) + [
        DEFAULT_PINS.cs,
        DEFAULT_PINS.dc,
        DEFAULT_PINS.wr,
        DEFAULT_PINS.rd,
        DEFAULT_PINS.reset,
        DEFAULT_PINS.backlight,
    ]
    assert len(set(pins)) == len(pins)
    assert all(0 <= p <= 53 for p in pins)


def test_duplicate_pins_rejected():
    with pytest.raises(ValueError):
        Pins(data_low=(4, 4, 27, 22, 5, 6, 13, 19))


def test_wrong_data_pin_count_rejected():
    with pytest.raises(ValueError):
        Pins(data_low=(4, 17, 27, 22, 5, 6, 13))  # 7 pins


def test_out_of_range_pin_rejected():
    with pytest.raises(ValueError):
        Pins(backlight=54)


def test_custom_pins_accepted():
    p = Pins(
        data_low=(0, 1, 2, 3, 4, 5, 6, 7),
        data_high=(8, 9, 10, 11, 12, 13, 14, 15),
        cs=16, dc=17, wr=18, rd=19, reset=20, backlight=21,
    )
    assert p.cs == 16
    assert p.data[15] == 15
