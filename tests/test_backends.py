"""Backend internals that don't need /dev/gpiomem."""
from __future__ import annotations

import struct
from array import array

import pytest

from ertftm070 import NotOnRaspberryPi
from ertftm070 import backends as backends_mod
from ertftm070.backends import (
    _FSEL,
    _GPCLR0,
    _GPLEV0,
    _GPSET0,
    _byte_mask,
    _check_platform,
    _MmioBus,
    _word_view,
    get_backend,
)
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


# ----------------------------------------------------------------------
# _word_view: bytes must stream as 16-bit words, not per byte
# ----------------------------------------------------------------------


def test_word_view_bytes_pair_up_low_byte_first():
    raw = b"\x01\x02\x03\x04"  # words 0x0201, 0x0403 on little-endian
    expected = array("H")
    expected.frombytes(raw)
    assert list(_word_view(raw)) == list(expected)


def test_word_view_bytearray_odd_length_rejected():
    with pytest.raises(ValueError):
        _word_view(bytearray(b"\x01\x02\x03"))  # cannot form whole words


def test_word_view_passes_word_buffers_through():
    buf = array("H", [0xF800, 0x07E0])
    assert _word_view(buf) is buf
    view = memoryview(buf)
    assert _word_view(view) is view
    assert list(_word_view(view[0:1])) == [0xF800]  # slices keep the format


def test_word_view_rejects_wider_items():
    with pytest.raises(TypeError):
        _word_view(array("I", [1, 2]))


# ----------------------------------------------------------------------
# Platform gate: BCM2835-family SoCs only
# ----------------------------------------------------------------------


def test_check_platform_accepts_supported_socs(monkeypatch):
    for soc in (
        b"brcm,bcm2835\x00brcm,bcm2709\x00",
        b"brcm,bcm2711\x00",
        b"brcm,bcm2708\x00",
    ):
        monkeypatch.setattr(backends_mod, "_read_compatible", lambda s=soc: s)
        _check_platform()  # must not raise


def test_check_platform_rejects_pi5_and_foreign_socs(monkeypatch):
    for soc in (b"brcm,bcm2712\x00", b"rockchip,rk3399\x00", b"fsl,imx8mq\x00"):
        monkeypatch.setattr(backends_mod, "_read_compatible", lambda s=soc: s)
        with pytest.raises(NotOnRaspberryPi):
            _check_platform()


def test_check_platform_without_device_tree(monkeypatch):
    def missing():
        raise OSError("No such file or directory")

    monkeypatch.setattr(backends_mod, "_read_compatible", missing)
    with pytest.raises(NotOnRaspberryPi):
        _check_platform()


# ----------------------------------------------------------------------
# Mmio register traffic against a scriptable bytearray "mmap"
# ----------------------------------------------------------------------


class _RecordingMmap:
    """A bytearray that stands in for the mmap and logs every slice write.

    The backend only uses ``mm[off:off+4] = bytes`` and 4-byte slice
    reads, so a bytearray has the exact same interface.
    """

    def __init__(self, size=0xB4):
        self.buf = bytearray(size)
        self.writes = []  # (byte offset, bytes written)

    def __setitem__(self, key, value):
        if isinstance(key, slice):
            self.writes.append((key.start, bytes(value)))
        else:
            self.writes.append((key, bytes([value])))
        self.buf[key] = value

    def __getitem__(self, key):
        return self.buf[key]

    def close(self):
        pass


def _mmio_bus():
    """An _MmioBus whose register writes land in a _RecordingMmap."""
    bus = _MmioBus(DEFAULT_PINS)
    mm = _RecordingMmap()
    bus._mm = mm
    return bus, mm


def test_mmio_write_byte_strobes_low_pins_only():
    bus, mm = _mmio_bus()
    bus.write_byte(0x5A)
    # clear low data pins, set them to 0x5A, WR low, WR high
    assert mm.writes == [
        (_GPCLR0, struct.pack("<I", bus._clr_low)),
        (_GPSET0, struct.pack("<I", bus._set_low[0x5A])),
        (_GPCLR0, struct.pack("<I", bus._wr)),
        (_GPSET0, struct.pack("<I", bus._wr)),
    ]


def test_mmio_pixel_stream_strobes_each_word_once():
    bus, mm = _mmio_bus()
    bus.pixel_stream(b"\x00\xF8\xE0\x07")  # words 0xF800, 0x07E0
    # 4 register writes per pixel (clr, set, WR low, WR high),
    # 2 pixels + 2 trailing dummy pixels
    assert [off for off, _ in mm.writes] == (
        [_GPCLR0, _GPSET0, _GPCLR0, _GPSET0] * 4
    )
    assert mm.writes[0][1] == struct.pack("<I", bus._clr_all)
    assert mm.writes[1][1] == struct.pack(
        "<I", bus._set_low[0x00] | bus._set_high[0xF8]
    )
    assert mm.writes[5][1] == struct.pack(
        "<I", bus._set_low[0xE0] | bus._set_high[0x07]
    )
    assert mm.writes[2][1] == mm.writes[3][1] == struct.pack("<I", bus._wr)
    # the 2 trailing dummy pixels repeat the last real word's data
    assert mm.writes[9][1] == mm.writes[13][1] == mm.writes[5][1]


def test_mmio_pixel_stream_accepts_word_buffers_too():
    bus, mm = _mmio_bus()
    bus.pixel_stream(array("H", [0xF800]))
    assert mm.writes[1][1] == struct.pack(
        "<I", bus._set_low[0x00] | bus._set_high[0xF8]
    )
    # 1 real word + 2 dummies
    assert len(mm.writes) == 12


def test_mmio_row_blit_windows_then_streams():
    bus, mm = _mmio_bus()
    bus.row_blit(0, 2, 0, array("H", [0xF800, 0x07E0]))
    sets = [w[1] for w in mm.writes if w[0] == _GPSET0]
    assert sets.index(struct.pack("<I", bus._set_low[0x2A])) < \
        sets.index(struct.pack("<I", bus._set_low[0x2B])) < \
        sets.index(struct.pack("<I", bus._set_low[0x2C]))
    # pixel data (0xF800 = low byte 0x00, high byte 0xF8) follows 0x2C
    pixel_set = struct.pack("<I", bus._set_low[0x00] | bus._set_high[0xF8])
    assert sets.index(pixel_set) > sets.index(struct.pack("<I", bus._set_low[0x2C]))
    # 11 window bytes + 2 real + 2 dummy pixels, one WR-low strobe each
    wr = struct.pack("<I", bus._wr)
    wr_low = [w[1] for w in mm.writes if w[0] == _GPCLR0 and w[1] == wr]
    assert len(wr_low) == 11 + 4


def test_mmio_read_word_samples_and_restores_outputs():
    bus, mm = _mmio_bus()
    # controller drives DB0 (pin 4), DB11 (pin 10), DB15 (pin 24) high
    lev = (
        (1 << DEFAULT_PINS.data_low[0])
        | (1 << DEFAULT_PINS.data_high[3])
        | (1 << DEFAULT_PINS.data_high[7])
    )
    mm.buf[_GPLEV0 : _GPLEV0 + 4] = struct.pack("<I", lev)
    assert bus.read_word() == 0x8801
    # the 16 input flips come first, then RD low + RD high
    assert mm.writes[16][1] == struct.pack("<I", bus._rd)
    assert mm.writes[17][1] == struct.pack("<I", bus._rd)
    # and every data pin is an output again afterwards (FSEL field == 1)
    for pin in DEFAULT_PINS.data:
        word = pin // 10
        shift = (pin % 10) * 3
        reg = struct.unpack("<I", mm.buf[_FSEL[word] : _FSEL[word] + 4])[0]
        assert (reg >> shift) & 7 == 1, f"pin {pin} left as input"
