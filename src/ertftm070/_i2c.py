"""Minimal I2C master over /dev/i2c-1 — stdlib only, no smbus.

The FT5x06 touch controller (docs/COMMUNITY-RESEARCH.md §6) needs just
register-pointer + byte reads/writes; the kernel's i2c-dev character
device provides exactly that through ``ioctl(I2C_SLAVE)`` +
``read()``/``write()``.  Kept deliberately thin — everything testable
lives in :mod:`ertftm070.touch` above it.
"""
from __future__ import annotations

import os

from .errors import Ertftm070Error

_I2C_SLAVE = 0x0703  # linux/i2c-dev.h

# Wiring hints appended to every bus-level failure: an absent or wedged
# FT5x06 can look like almost any OSError, so point at what actually
# differs from a working setup.
_WIRE_HINT = (
    "check the SCL/SDA wiring (docs/WIRING.md) and that CTP_WAKE is "
    "tied to 3.3 V — a hibernating FT5x06 answers at ghost addresses, "
    "not 0x38"
)


class I2CError(Ertftm070Error):
    """The touch I2C bus could not be opened or answered.

    Usual causes, in order of likelihood: I2C not enabled
    (``dtparam=i2c_arm=on`` + reboot), SCL/SDA swapped, or the touch
    chip hibernating because CTP_WAKE (pin 37) is not tied to 3.3 V —
    in that state it answers at ghost addresses instead of 0x38.  An
    absent slave only shows up on the first read or write, never on
    :meth:`I2C.open` — that ioctl only sets the address.
    """


class I2C:
    """One I2C slave device on ``/dev/i2c-<bus>``."""

    def __init__(self, addr: int, bus: int = 1):
        self.addr = addr
        self.path = f"/dev/i2c-{bus}"
        self._fd = None  # type: int | None

    def open(self) -> None:
        """Open the bus and address the slave.  Idempotent."""
        if self._fd is not None:
            return
        try:
            fd = os.open(self.path, os.O_RDWR)
        except OSError as exc:
            raise I2CError(
                f"cannot open {self.path}: {exc} — enable I2C with "
                "`dtparam=i2c_arm=on` and reboot (see docs/WIRING.md)"
            ) from exc
        try:
            # fcntl is POSIX-only; importing it at module top would break
            # the documented contract that importing the package works
            # off-Linux.  os.open above already failed there (no
            # /dev/i2c-N device), so this import only ever runs on Linux.
            import fcntl

            fcntl.ioctl(fd, _I2C_SLAVE, self.addr)
        except OSError as exc:
            os.close(fd)
            raise I2CError(
                f"cannot select slave 0x{self.addr:02X} on {self.path}: {exc}"
            ) from exc
        self._fd = fd

    def close(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def write_reg(self, reg: int, data: bytes | list[int] = b"") -> None:
        """Write the register pointer and, optionally, data bytes."""
        self._write(bytes([reg]) + bytes(data))

    def read_reg(self, reg: int, n: int) -> bytes:
        """Write the register pointer, then read ``n`` bytes."""
        self._write(bytes([reg]))
        return self._read(n)

    def _check(self) -> int:
        if self._fd is None:
            raise I2CError("I2C bus not open — call open() first")
        return self._fd

    def _write(self, payload: bytes) -> None:
        try:
            written = os.write(self._check(), payload)
        except OSError as exc:
            raise I2CError(
                f"I2C write to 0x{self.addr:02X} failed: {exc} — {_WIRE_HINT}"
            ) from exc
        if written != len(payload):
            raise I2CError(
                f"I2C write to 0x{self.addr:02X} short: "
                f"{written}/{len(payload)} bytes — {_WIRE_HINT}"
            )

    def _read(self, n: int) -> bytes:
        try:
            data = os.read(self._check(), n)
        except OSError as exc:
            raise I2CError(
                f"I2C read from 0x{self.addr:02X} failed: {exc} — {_WIRE_HINT}"
            ) from exc
        if len(data) != n:
            raise I2CError(
                f"I2C read from 0x{self.addr:02X} short: {len(data)}/{n} bytes "
                f"— {_WIRE_HINT}"
            )
        return data
