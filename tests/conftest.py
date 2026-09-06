"""Shared test fixtures: a scriptable fake bus and speed-ups."""
from __future__ import annotations

import pytest

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
        """Replay the traffic the real backends generate for one row
        (window commands, burst, CS/DC framing) so the recorded byte
        stream looks exactly as it did before row_blit existed."""
        self.row_blit_calls.append((x0, x1, y))
        cs, dc = self.pins.cs, self.pins.dc
        self.pin_write(cs, False)
        self.pin_write(dc, False)
        self.write_byte(0x2A)
        self.pin_write(cs, True)
        self.pin_write(cs, False)
        self.pin_write(dc, True)
        for v in (x0 >> 8, x0 & 0xFF, x1 >> 8, x1 & 0xFF):
            self.write_byte(v)
        self.pin_write(cs, True)
        self.pin_write(cs, False)
        self.pin_write(dc, False)
        self.write_byte(0x2B)
        self.pin_write(cs, True)
        self.pin_write(cs, False)
        self.pin_write(dc, True)
        for v in (y >> 8, y & 0xFF, y >> 8, y & 0xFF):
            self.write_byte(v)
        self.pin_write(cs, True)
        self.pin_write(cs, False)
        self.pin_write(dc, False)
        self.write_byte(0x2C)
        self.pin_write(cs, True)
        self.pin_write(cs, False)
        self.pin_write(dc, True)
        self.pixel_stream(buf)
        self.pin_write(cs, True)

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
