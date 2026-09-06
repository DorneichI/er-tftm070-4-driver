"""Pin configuration for the 16-bit 8080 bus.

The ER-TFTM070-4V2.1 board is strapped for **16-bit 8080**: one WR strobe
latches one 16-bit pixel, with DB0-7 carrying the low byte and DB8-15 the
high byte.  (R3/R4 on the board only select 8080-vs-6800, *not* the bus
width.)  Register access always uses DB0-7 regardless of bus width.

``DEFAULT_PINS`` is the wiring verified on hardware (Raspberry Pi Zero W,
September 2026).  The full wiring table lives in ``docs/WIRING.md``.
"""
from __future__ import annotations

from dataclasses import dataclass

GPIO_MAX = 27  # the 40-pin header exposes BCM GPIO 0..27 only


@dataclass(frozen=True)
class Pins:
    """BCM GPIO numbers used by the display bus.

    Attributes:
        data_low: DB0..DB7 (low byte of every 16-bit write)
        data_high: DB8..DB15 (high byte) — NOT optional on this board:
            it is strapped for 16-bit 8080, so both bytes must be wired.
        cs: chip select (active low)
        dc: data/command select (low = command, high = data)
        wr: write strobe (active low)
        rd: read strobe (active low; only used by the self-test and
            GRAM read-back diagnostics — the driver is write-only for
            everything else)
        reset: master reset (active low)
        backlight: backlight enable (high = on)
        te: tearing-effect output from the panel (connector pin 8) —
            an *input* the driver watches for vsync and refresh
            measurement; wired to GPIO14 (phys 8)
    """

    data_low: tuple[int, ...] = (4, 17, 27, 22, 5, 6, 13, 19)
    data_high: tuple[int, ...] = (7, 8, 9, 10, 11, 18, 23, 24)
    cs: int = 26
    dc: int = 20
    wr: int = 21
    rd: int = 16
    reset: int = 12
    backlight: int = 25
    te: int = 14

    def __post_init__(self) -> None:
        pins = (
            list(self.data_low)
            + list(self.data_high)
            + [self.cs, self.dc, self.wr, self.rd, self.reset, self.backlight, self.te]
        )
        if len(self.data_low) != 8 or len(self.data_high) != 8:
            raise ValueError("the 16-bit bus needs exactly 8 low + 8 high data pins")
        if len(set(pins)) != len(pins):
            raise ValueError("pin numbers must be unique")
        bad = [p for p in pins if not 0 <= p <= GPIO_MAX]
        if bad:
            raise ValueError(
                f"GPIO numbers out of range 0..{GPIO_MAX} (40-pin header): {bad}"
            )

    @property
    def data(self) -> tuple[int, ...]:
        """All 16 data pins: DB0..DB7 followed by DB8..DB15."""
        return self.data_low + self.data_high


#: Wiring verified against the ER-TFTM070-4V2.1 datasheet, 40-pin connector.
DEFAULT_PINS = Pins()
