"""Shared test fixtures: a scriptable fake bus and speed-ups."""
from __future__ import annotations

import pytest

from ertftm070.backends import _row_blit_traffic
from ertftm070.display import Display
from ertftm070.pins import DEFAULT_PINS


class FakeBus:
    """A Bus implementation that records everything and touches no GPIOs.

    ``read_words`` is a queue of scripted 16-bit words returned by
    successive ``read_word()`` calls.
    """

    def __init__(self, pins=DEFAULT_PINS):
        self.pins = pins
        self.open_calls = 0
        self.closed = False
        self.pin_levels = {}  # pin -> bool
        self.pin_modes = {}  # pin -> bool (is output)
        self.bytes_written = []  # (dc_level_at_write, byte) pairs
        self.streams = []  # one list of words per pixel_stream call
        self.row_blit_calls = []  # (x0, x1, y) per row_blit call
        self.row_blit_words = []  # the word list per row_blit call
        self.read_words = []  # script queue

    # -- Bus protocol --
    def open(self):
        self.open_calls += 1

    def close(self):
        self.closed = True

    def pin_mode(self, pin, output):
        self.pin_modes[pin] = bool(output)

    def pin_write(self, pin, level):
        self.pin_levels[pin] = bool(level)

    def write_byte(self, value):
        self.bytes_written.append((self.pin_levels.get(self.pins.dc), value))

    def read_word(self):
        if not self.read_words:
            raise AssertionError("FakeBus.read_word called with an empty script")
        return self.read_words.pop(0)

    def pixel_stream(self, buf):
        self.streams.append(list(buf))

    def row_blit(self, x0, x1, y, buf):
        """Record the call (coordinates and per-row words), then emit
        this row's traffic through the same ``_row_blit_traffic``
        composer the slow backend uses — so the recorded byte stream
        matches what reaches the panel, and the test oracle and the
        real slow backend cannot drift apart."""
        self.row_blit_calls.append((x0, x1, y))
        self.row_blit_words.append(list(buf))
        _row_blit_traffic(self, x0, x1, y, buf)

    # -- helpers for assertions --
    def commands(self):
        """The command bytes only (DC low), in order."""
        return [b for dc, b in self.bytes_written if dc is False]

    def data(self):
        """The data bytes only (DC high), in order."""
        return [b for dc, b in self.bytes_written if dc is True]


class FailOpenBus(FakeBus):
    """A bus whose open() fails like a non-Pi machine."""

    def open(self):
        self.open_calls += 1
        raise OSError("No such file or directory: '/dev/gpiomem'")


@pytest.fixture
def bus():
    return FakeBus()


@pytest.fixture
def display(bus):
    """A Display wired to the FakeBus, with sleeps removed for speed."""
    import ertftm070.display as display_module

    d = Display(backend=bus)
    original_sleep = display_module.time.sleep
    display_module.time.sleep = lambda seconds: None  # noqa: ARG005
    try:
        yield d
    finally:
        display_module.time.sleep = original_sleep
