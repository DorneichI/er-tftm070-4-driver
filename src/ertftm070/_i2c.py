"""Minimal I2C master over /dev/i2c-1 — stdlib only, no smbus.

The FT5x06 touch controller (docs/COMMUNITY-RESEARCH.md §6) needs just
register-pointer + byte reads/writes; the kernel's i2c-dev character
device provides exactly that through ``ioctl(I2C_SLAVE)`` +
``read()``/``write()``.  Kept deliberately thin — everything testable
lives in :mod:`ertftm070.touch` above it.
"""
from __future__ import annotations

import fcntl
import os

from .errors import Ertftm070Error

_I2C_SLAVE = 0x0703  # linux/i2c-dev.h


class I2CError(Ertftm070Error):
    """The touch I2C bus could not be opened or answered.

    Usual causes, in order of likelihood: I2C not enabled
    (``dtparam=i2c_arm=on`` + reboot), SCL/SDA swapped, or the touch
    chip hibernating because CTP_WAKE (pin 37) is not tied to 3.3 V —
    in that state it answers at ghost addresses instead of 0x38.
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
            fcntl.ioctl(fd, _I2C_SLAVE, self.addr)
        except OSError as exc:
            os.close(fd)
            raise I2CError(
                f"no slave at 0x{self.addr:02X} on {self.path}: {exc} — "
                "check SCL/SDA wiring and that CTP_WAKE is tied to 3.3 V "
                "(a hibernating FT5x06 answers at ghost addresses, not 0x38)"
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
            raise I2CError(f"I2C write to 0x{self.addr:02X} failed: {exc}") from exc
        if written != len(payload):
            raise I2CError(
                f"I2C write to 0x{self.addr:02X} short: "
                f"{written}/{len(payload)} bytes"
            )

    def _read(self, n: int) -> bytes:
        try:
            data = os.read(self._check(), n)
        except OSError as exc:
            raise I2CError(f"I2C read from 0x{self.addr:02X} failed: {exc}") from exc
        if len(data) != n:
            raise I2CError(
                f"I2C read from 0x{self.addr:02X} short: {len(data)}/{n} bytes"
            )
        return data
