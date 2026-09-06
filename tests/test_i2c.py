"""The real I2C transport — ioctl traffic and error paths, with the
os/fcntl calls faked so no /dev/i2c-N device is required."""
from __future__ import annotations

import fcntl
import os

import pytest

from ertftm070._i2c import _I2C_SLAVE, _I2C_TIMEOUT, I2C, I2CError


class _FakeOS:
    """Stands in for os inside ertftm070._i2c."""

    O_RDWR = os.O_RDWR

    def __init__(self):
        self.open_calls = []
        self.closed = []
        self.open_error = None
        self.next_fd = 7

    def open(self, path, flags):
        if self.open_error is not None:
            raise self.open_error
        self.open_calls.append((path, flags))
        self.next_fd += 1
        return self.next_fd

    def close(self, fd):
        self.closed.append(fd)


def _patched_i2c(monkeypatch, behaviors=()):
    """An I2C(0x38) whose open() records ioctl calls; behaviors is one
    entry per ioctl call: None = succeed, or an exception to raise."""
    calls = []
    queue = list(behaviors)

    def fake_ioctl(fd, cmd, arg):
        calls.append((fd, cmd, arg))
        if queue:
            outcome = queue.pop(0)
            if outcome is not None:
                raise outcome
        return 0

    monkeypatch.setattr(fcntl, "ioctl", fake_ioctl)
    fake_os = _FakeOS()
    monkeypatch.setattr("ertftm070._i2c.os", fake_os)
    return I2C(0x38), fake_os, calls


def test_i2c_open_addresses_slave_then_sets_transfer_timeout(monkeypatch):
    i2c, fake_os, calls = _patched_i2c(monkeypatch)
    i2c.open()
    assert i2c._fd == 8
    assert fake_os.open_calls == [("/dev/i2c-1", os.O_RDWR)]
    assert calls == [(8, _I2C_SLAVE, 0x38), (8, _I2C_TIMEOUT, 100)]


def test_i2c_open_is_idempotent(monkeypatch):
    i2c, fake_os, calls = _patched_i2c(monkeypatch)
    i2c.open()
    i2c.open()
    assert len(fake_os.open_calls) == 1
    assert len(calls) == 2


def test_i2c_open_ignores_timeout_ioctl_failure(monkeypatch):
    # the bound is best-effort hardening: a refused ioctl must not break
    # an otherwise working bus, and must not close the fd
    i2c, fake_os, calls = _patched_i2c(monkeypatch, [None, OSError("bad ioctl")])
    i2c.open()
    assert i2c._fd == 8
    assert calls[1][1] == _I2C_TIMEOUT
    assert fake_os.closed == []


def test_i2c_open_slave_ioctl_failure_raises_and_closes_fd(monkeypatch):
    i2c, fake_os, _ = _patched_i2c(monkeypatch, [OSError("remote I/O error")])
    with pytest.raises(I2CError):
        i2c.open()
    assert fake_os.closed == [8]  # no fd leak on the hard failure path
    assert i2c._fd is None


def test_i2c_open_missing_device_raises_wiring_hint(monkeypatch):
    i2c, fake_os, _ = _patched_i2c(monkeypatch)
    fake_os.open_error = OSError(2, "No such file or directory")
    with pytest.raises(I2CError, match="enable I2C"):
        i2c.open()
