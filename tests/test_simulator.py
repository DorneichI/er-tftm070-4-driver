"""The simulated browser backend — framebuffer, touch state, selection,
and (when websockets is installed) the server itself.  Everything that
does not need a socket runs headless via ``SimulatedBus(serve=False)``.
"""
from __future__ import annotations

import importlib
import json
import os
import socket
import sys
import time
import urllib.request
from array import array

import pytest

from ertftm070 import backends
from ertftm070._i2c import I2CError
from ertftm070.colors import rgb565
from ertftm070.display import Display
from ertftm070.errors import Ertftm070Error, NotOnRaspberryPi
from ertftm070.pins import DEFAULT_PINS
from ertftm070.simulator import (
    FULL_MSG,
    FULL_SIZE,
    HEIGHT,
    ROW_HEADER,
    ROW_MSG,
    TE_PERIOD,
    WIDTH,
    SimulatedBus,
    SimulatedI2C,
    TouchState,
)
from ertftm070.touch import (
    EVENT_CONTACT,
    EVENT_DOWN,
    TD_STATUS,
    Touch,
    TouchPoint,
    decode_points,
    decode_status,
)


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
def sim_backend():
    """``backends`` reloaded under ERTFTM070_DISPLAY=sim (BACKEND is
    decided once at import, so selection tests re-decide it)."""
    old = os.environ.get("ERTFTM070_DISPLAY")
    os.environ["ERTFTM070_DISPLAY"] = "sim"
    importlib.reload(backends)
    try:
        yield backends
    finally:
        if old is None:
            os.environ.pop("ERTFTM070_DISPLAY", None)
        else:
            os.environ["ERTFTM070_DISPLAY"] = old
        importlib.reload(backends)


# ----------------------------------------------------------------------
# A. Framebuffer
# ----------------------------------------------------------------------


def test_row_blit_writes_exact_little_endian_words(sim_bus):
    words = [rgb565(255, 0, 0), rgb565(0, 255, 0), rgb565(0, 0, 255)]
    sim_bus.row_blit(5, 7, 3, array("H", words))
    assert sim_bus.pixel(5, 3) == words[0]
    assert sim_bus.pixel(6, 3) == words[1]
    assert sim_bus.pixel(7, 3) == words[2]
    assert sim_bus.pixel(4, 3) == 0  # neighbors untouched
    assert sim_bus.pixel(8, 3) == 0
    assert sim_bus.pixel(5, 2) == 0
    offset = (3 * WIDTH + 5) * 2
    assert sim_bus.full_frame()[offset : offset + 6] == array("H", words).tobytes()


def test_row_blit_accepts_bytes_and_partial_rows_compose(sim_bus):
    # bytes of LE word pairs are a valid pixel buffer, like on real backends
    left = array("H", [0x1234, 0x5678]).tobytes()
    sim_bus.row_blit(0, 1, 0, left)
    sim_bus.row_blit(2, 3, 0, array("H", [0x9ABC, 0xDEF0]))
    assert [sim_bus.pixel(x, 0) for x in range(4)] == [0x1234, 0x5678, 0x9ABC, 0xDEF0]


def test_fill_round_trip_through_display(sim_bus):
    # A whole Display against the sim bus: fill() must land in the
    # framebuffer exactly as rgb565() defines the color.
    with Display(backend=sim_bus, auto_init=False, backlight=False) as lcd:
        lcd.fill(rgb565(200, 30, 90))
    assert sim_bus.pixel(0, 0) == rgb565(200, 30, 90)
    assert sim_bus.pixel(WIDTH - 1, HEIGHT - 1) == rgb565(200, 30, 90)


# ----------------------------------------------------------------------
# B. Validation parity with the real backends
# ----------------------------------------------------------------------


def test_row_blit_validation_matches_real_backends(sim_bus):
    with pytest.raises(ValueError):
        sim_bus.row_blit(5, 4, 0, array("H", [0]))  # x0 > x1
    with pytest.raises(ValueError):
        sim_bus.row_blit(0, 1, 0, array("H", [0, 0, 0]))  # wrong word count
    with pytest.raises(ValueError):
        sim_bus.row_blit(0, 0, 0, b"\x01")  # odd byte count
    with pytest.raises(ValueError):
        sim_bus.row_blit(0, WIDTH, 0, array("H", [0] * (WIDTH + 1)))  # x1 off-panel
    with pytest.raises(ValueError):
        sim_bus.row_blit(0, 0, HEIGHT, array("H", [0]))  # y off-panel
    with pytest.raises(TypeError):
        sim_bus.row_blit(0, 0, 0, array("I", [0]))  # wider items, like _word_view


def test_closed_bus_raises_like_real_backends():
    bus = SimulatedBus(serve=False)
    with pytest.raises(RuntimeError):
        bus.row_blit(0, 0, 0, array("H", [0]))
    with pytest.raises(RuntimeError):
        bus.pin_read(0)
    with pytest.raises(RuntimeError):
        bus.pin_write(0, True)
    with pytest.raises(RuntimeError):
        bus.write_byte(0)
    with pytest.raises(RuntimeError):
        bus.read_word()


# ----------------------------------------------------------------------
# C. Pins
# ----------------------------------------------------------------------


def test_pin_write_read_levels_round_trip(sim_bus):
    sim_bus.pin_mode(4, True)
    sim_bus.pin_write(4, True)
    assert sim_bus.pin_read(4) is True
    sim_bus.pin_write(4, False)
    assert sim_bus.pin_read(4) is False
    assert sim_bus.pin_read(99) is False  # never written: low, like GPLEV0


def test_input_pin_reads_the_int_line(sim_bus):
    # Touch.open() sets its INT pin to input; the sim answers it with the
    # measured panel polarity: idle low, high while a finger is down.
    sim_bus.pin_mode(15, False)
    assert sim_bus.pin_read(15) is False
    sim_bus.touch.update(0, 400, 240, "down")
    assert sim_bus.pin_read(15) is True
    sim_bus.touch.update(0, 400, 240, "up")
    assert sim_bus.pin_read(15) is False


def test_te_pin_reads_a_square_wave(sim_bus):
    # The TE waveform must show both levels within ~2 periods (~40 ms),
    # or vsync_wait()/refresh_rate() could never see an edge.
    seen = {sim_bus.pin_read(DEFAULT_PINS.te)}
    deadline = time.monotonic() + 4 * TE_PERIOD
    while time.monotonic() < deadline and len(seen) < 2:
        time.sleep(0.0005)
        seen.add(sim_bus.pin_read(DEFAULT_PINS.te))
    assert seen == {False, True}


def test_read_word_is_zero_in_sim(sim_bus):
    assert sim_bus.read_word() == 0


# ----------------------------------------------------------------------
# D. TouchState <-> FT5x06 registers
# ----------------------------------------------------------------------


def test_touchstate_round_trips_through_the_decoders():
    state = TouchState()
    state.update(0, 0x300, 0x100, "down")
    count, records, any_down = state.sample()
    assert any_down is True
    assert decode_status(count) == 1
    assert decode_points(records, count) == [
        TouchPoint(x=0x300, y=0x100, id=0, event=EVENT_DOWN)
    ]
    state.update(0, 0x301, 0x100, "move")
    count, records, _ = state.sample()
    assert decode_points(records, count) == [
        TouchPoint(x=0x301, y=0x100, id=0, event=EVENT_CONTACT)
    ]
    state.update(0, 0, 0, "up")
    assert state.sample() == (0, b"", False)


def test_touchstate_clamps_caps_and_ignores_junk():
    state = TouchState()
    state.update(0, 5000, -5, "down")
    assert decode_points(state.sample()[1], 1)[0].x == WIDTH - 1
    assert decode_points(state.sample()[1], 1)[0].y == 0
    state.update(0, 0, 0, "up")
    for ft_id in range(5):
        state.update(ft_id, ft_id, ft_id, "down")
    state.update(1, 1, 1, "down")  # 6th finger: ignored
    assert state.sample()[0] == 5
    state.update(7, 1, 1, "down")  # id outside 0..4: ignored
    assert state.sample()[0] == 5
    state.update(0, 1, 1, "teleport")  # not a gesture: ignored
    assert state.sample()[0] == 5


# ----------------------------------------------------------------------
# E. Touch end-to-end over the simulated register file
# ----------------------------------------------------------------------


class RecordingI2C:
    """A plain fake transport: proves an explicit i2c= still wins."""

    def __init__(self):
        self.opened = False

    def open(self):
        self.opened = True

    def close(self):
        pass

    def write_reg(self, reg, data=b""):
        pass

    def read_reg(self, reg, n):
        return b"\x00" * n


@pytest.fixture
def sim_touch(sim_bus, monkeypatch):
    """A Touch wired to the sim bus, with the touch layer's sleeps
    removed (the /RST settle is real timing the sim does not need)."""
    import ertftm070.touch as touch_module

    monkeypatch.setattr(touch_module.time, "sleep", lambda seconds: None)
    touch = Touch(sim_bus)
    yield touch
    touch.close()


def test_touch_picks_simulated_i2c_and_reads_injected_points(sim_touch, sim_bus):
    assert isinstance(sim_touch._i2c, SimulatedI2C)
    sim_touch.open()  # fresh state reads sane: the phantom wait passes
    assert sim_touch.read() == []
    sim_bus.touch.update(0, 400, 240, "down")
    assert sim_touch.read() == [TouchPoint(x=400, y=240, id=0, event=EVENT_DOWN)]
    sim_bus.touch.update(0, 410, 240, "move")
    points = sim_touch.read(mapped=True)  # default calibration is identity
    assert points == [TouchPoint(x=410, y=240, id=0, event=EVENT_CONTACT)]
    assert sim_touch.wait_touch(timeout=0.5) is True  # first poll: already down
    sim_bus.touch.update(0, 0, 0, "up")
    assert sim_touch.read() == []
    assert sim_touch.wait_touch(timeout=0.05) is False  # sleep patched: spins out


def test_touch_explicit_i2c_still_wins(sim_bus, monkeypatch):
    import ertftm070.touch as touch_module

    monkeypatch.setattr(touch_module.time, "sleep", lambda seconds: None)
    rec = RecordingI2C()
    touch = Touch(sim_bus, i2c=rec)
    assert touch._i2c is rec
    touch.open()
    assert rec.opened is True


def test_simulated_i2c_closed_bus_raises():
    i2c = SimulatedI2C()
    with pytest.raises(I2CError):
        i2c.read_reg(TD_STATUS, 1)
    i2c.open()
    assert i2c.read_reg(TD_STATUS, 1) == b"\x00"  # no touches
    assert i2c.read_reg(0x03, 12) == b"\x00" * 12  # records padded
    assert i2c.read_reg(0x7F, 2) == b"\x00\x00"  # unmodeled reg: zeros


# ----------------------------------------------------------------------
# F. Selection via ERTFTM070_DISPLAY=sim
# ----------------------------------------------------------------------


def test_env_selects_the_sim_backend(sim_backend):
    assert backends.BACKEND == "sim"
    bus = backends.get_backend()
    assert isinstance(bus, SimulatedBus)
    assert bus.pins is DEFAULT_PINS
    assert bus.host == "0.0.0.0"
    assert bus.port == 8000
    bus.close()  # never opened: close must be a safe no-op


def test_explicit_backend_wins_over_the_env(sim_backend, bus):
    assert backends.get_backend(DEFAULT_PINS, backend=bus) is bus


def test_sim_host_and_port_env_vars(sim_backend, monkeypatch):
    monkeypatch.setenv("ERTFTM070_SIM_HOST", "127.0.0.1")
    monkeypatch.setenv("ERTFTM070_SIM_PORT", "8081")
    bus = backends.get_backend()
    assert bus.host == "127.0.0.1"
    assert bus.port == 8081
    bus.close()


def test_sim_port_env_must_be_a_valid_integer(sim_backend, monkeypatch):
    monkeypatch.setenv("ERTFTM070_SIM_PORT", "not-a-port")
    with pytest.raises(ValueError):
        backends.get_backend()
    monkeypatch.setenv("ERTFTM070_SIM_PORT", "70000")
    with pytest.raises(ValueError):
        backends.get_backend()


# ----------------------------------------------------------------------
# G. The server (websockets must be installed — the [sim]/[dev] extras)
# ----------------------------------------------------------------------


@pytest.fixture
def ws_connect():
    pytest.importorskip("websockets")
    from websockets.sync.client import connect

    return connect


@pytest.fixture
def serving_bus():
    bus = SimulatedBus(serve=True, host="127.0.0.1", port=0)
    bus.open()
    try:
        yield bus
    finally:
        bus.close()


def test_page_is_served_at_root(serving_bus):
    url = f"http://127.0.0.1:{serving_bus.server_port}/"
    with urllib.request.urlopen(url) as response:
        body = response.read().decode("utf-8")
    assert response.status == 200
    assert "<canvas" in body


def test_full_frame_then_rows_reach_the_browser(serving_bus, ws_connect):
    with ws_connect(f"ws://127.0.0.1:{serving_bus.server_port}/ws") as ws:
        first = ws.recv(timeout=5)
        assert isinstance(first, bytes)
        assert first[0] == FULL_MSG and len(first) == FULL_SIZE
        words = array("H", [rgb565(255, 0, 0)] * 10)
        serving_bus.row_blit(0, 9, 0, words)
        row = ws.recv(timeout=5)
        assert isinstance(row, bytes)
        assert ROW_HEADER.unpack(row[: ROW_HEADER.size]) == (ROW_MSG, 0, 9, 0, 10)
        assert row[ROW_HEADER.size :] == words.tobytes()


def test_browser_touch_events_reach_the_bus(serving_bus, ws_connect):
    with ws_connect(f"ws://127.0.0.1:{serving_bus.server_port}/ws") as ws:
        ws.recv(timeout=5)  # the FULL frame
        ws.send(json.dumps({"type": "touch", "event": "down", "x": 42, "y": 43}))
        deadline = time.monotonic() + 5
        while serving_bus.touch.sample()[0] != 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert serving_bus.touch.sample()[1] and serving_bus.touch.sample()[0] == 1
        point = decode_points(serving_bus.touch.sample()[1], 1)[0]
        assert (point.x, point.y, point.id) == (42, 43, 0)


def test_port_conflict_is_a_sim_error_not_notonraspberrypi():
    pytest.importorskip("websockets")
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    port = blocker.getsockname()[1]
    bus = SimulatedBus(serve=True, host="127.0.0.1", port=port)
    try:
        with pytest.raises(Ertftm070Error) as excinfo:
            bus.open()
        assert not isinstance(excinfo.value, NotOnRaspberryPi)
        assert "ERTFTM070_SIM_PORT" in str(excinfo.value)
        with pytest.raises(RuntimeError):
            bus.row_blit(0, 0, 0, array("H", [0]))  # open failed: still closed
    finally:
        bus.close()
        blocker.close()


def test_port_conflict_surfaces_through_display(sim_backend, monkeypatch):
    pytest.importorskip("websockets")
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    port = blocker.getsockname()[1]
    monkeypatch.setenv("ERTFTM070_SIM_HOST", "127.0.0.1")
    monkeypatch.setenv("ERTFTM070_SIM_PORT", str(port))
    try:
        with pytest.raises(Ertftm070Error) as excinfo:
            Display().open()
        assert not isinstance(excinfo.value, NotOnRaspberryPi)
    finally:
        blocker.close()


def test_missing_websockets_is_a_clear_sim_error(monkeypatch):
    # Evict every cached websockets module first: a cached submodule
    # would let `from websockets.asyncio.server import ...` succeed past
    # the None parent (the import machinery never re-checks parents once
    # the leaf is in sys.modules).
    websockets_modules = [
        n for n in sys.modules if n == "websockets" or n.startswith("websockets.")
    ]
    for name in websockets_modules:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setitem(sys.modules, "websockets", None)
    bus = SimulatedBus(serve=True, host="127.0.0.1", port=0)
    with pytest.raises(Ertftm070Error) as excinfo:
        bus.open()
    assert "ertftm070[sim]" in str(excinfo.value)
    bus.close()


def test_close_stops_the_server_and_reopen_rebinds(serving_bus):
    serving_bus.close()
    serving_bus.close()  # idempotent
    assert serving_bus.server_port is None
    serving_bus.open()
    first = serving_bus.server_port
    serving_bus.open()  # idempotent: same server, same port
    assert serving_bus.server_port == first
    serving_bus.close()
    serving_bus.open()  # a fresh server binds again
    assert serving_bus.server_port is not None
