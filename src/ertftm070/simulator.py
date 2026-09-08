"""Simulated display backend: render into a browser, no hardware.

``ERTFTM070_DISPLAY=sim`` (read once at import, like the existing
backend choice in :mod:`ertftm070.backends`) makes
:class:`~ertftm070.display.Display` and :class:`~ertftm070.touch.Touch`
pick simulated components below the interfaces they already speak:

* :class:`SimulatedBus` implements the ``Bus`` protocol: ``row_blit``
  copies the RGB565 words into an in-memory 800x480 framebuffer and
  streams each row to connected browsers (one event per call — the same
  row-by-row update behavior the real panel gets).  Register and pin
  *writes* are no-ops; pin *levels* are tracked so the touch INT line
  and the TE waveform read like the real panel's.
* :class:`SimulatedI2C` serves the FT5x06 register file (TD_STATUS +
  point records) from a shared :class:`TouchState` the browser fills
  with mouse/touch events — ``Touch.read()`` and ``wait_touch()`` work
  unchanged.
* :class:`SimServer` is a small WebSocket server (``websockets``, the
  optional ``[sim]`` extra, imported lazily so the core install stays
  dependency-free) serving one static HTML page at ``/`` and streaming
  raw RGB565 rows over the WebSocket.

Everything is panel-native: the framebuffer is the 800x480 controller
frame the panel draws at ``rotation=0``, so what the driver draws and
what the browser shows always agree, whatever the rotation.  The
framebuffer part is fully testable headless — ``SimulatedBus(serve=False)``
starts no server.

Wire protocol (browser sets ``ws.binaryType = "arraybuffer"``; integers
little-endian):

* ROW (``0x01``): type byte, then x0, x1, y and the word count (u16
  each), then the raw RGB565 words — one message per ``row_blit`` call.
* FULL (``0x02``): type byte, then the whole 800x480x2 framebuffer —
  sent once per connection, before any rows, so (re)connects resync.

Browser → server: JSON text ``{"type": "touch", "event": "down"|"move"|
"up", "x": 0..799, "y": 0..479, "id": 0..4}``.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import struct
import sys
import threading
import time
from collections import deque
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ._i2c import I2CError
from .backends import _word_view
from .errors import Ertftm070Error
from .pins import DEFAULT_PINS, Pins
from .touch import (
    _POINT_BASES,
    EVENT_CONTACT,
    EVENT_DOWN,
    EVENT_UP,
    FT5X06_ADDR,
    TD_STATUS,
)

log = logging.getLogger("ertftm070")

# The panel frame this module renders: panel-native 800x480, row-major,
# one RGB565 word per pixel, low byte first (bit 0 = DB0, like the bus).
WIDTH, HEIGHT = 800, 480

ROW_MSG, FULL_MSG = 0x01, 0x02
ROW_HEADER = struct.Struct("<BHHHH")  # type, x0, x1, y, word count
FULL_SIZE = 1 + WIDTH * HEIGHT * 2

# The emulated TE waveform: the measured panel refreshes at ~53.7 Hz and
# pulses TE high during the ~1.6 ms vertical blanking window (~8% of the
# 18.6 ms frame).  Nominal values are enough — the driver's waits only
# care about edges, and refresh_rate() derives plausible clocks from them.
TE_PERIOD = 1.0 / 53.7
TE_BLANK_FRACTION = 0.08

# A row burst on the real panel takes ~1.3 ms (measured; see
# Display._blit_rows docs).  row_blit paces itself the same way so an
# app that redraws continuously — touch_paint's touch loop has no sleep
# of its own — drives the simulator at hardware rates instead of
# flooding the server with microseconds of memcpys.
_ROW_TIME = 0.0013

# The first point-record register.  Bound straight to touch.py's table
# so the register map cannot drift apart from decode_points (the
# round-trip test in tests/test_simulator.py additionally pins the
# layout against it).
_POINT_BASE = _POINT_BASES[0]

_MAX_FINGERS = 5

_WIRE_HINT = "set ERTFTM070_SIM_PORT to move it, or close the other sim display"


class TouchState:
    """The touch overlay as the simulated FT5x06 would report it.

    Shared by the server (browser events go in) and
    :class:`SimulatedI2C` (register reads come out).  A finger reports
    ``down`` once, then ``move`` — the same press/contact stream the
    real chip produces and :func:`ertftm070.touch.decode_points` keeps.
    Releases are never encoded; an ``up`` event removes the finger from
    TD_STATUS entirely, exactly like the chip's count does.

    When more than one browser is connected, each viewer owns the finger
    ids it pressed (the ``owner`` argument of :meth:`update`): only the
    owner may lift or move a finger it holds, and :meth:`release_owner`
    drops a departed viewer's fingers wholesale.  Without ownership a
    viewer that vanishes mid-press (tab closed, network drop — no ``up``
    ever arrives) would leave its finger stuck down forever, and two
    viewers would yank each other's fingers around the shared
    five-id register map.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._points = {}  # finger id -> (x, y, event)
        self._owners = {}  # finger id -> owner token of the current press

    def update(self, ft_id: int, x: int, y: int, event: str, owner=None) -> None:
        """Apply one touch event (``"down"``/``"move"``/``"up"``).

        ``owner`` identifies the viewer that produced the event.  A
        finger id a different owner currently holds is left alone (the
        FT5x06's five-point register map cannot hold two viewers' points
        under one id), and only the owning viewer's ``up`` releases a
        press.  ``owner=None`` — headless feeders sharing a served
        bus — is just another non-owner: it cannot take over or lift a
        finger a browser holds, only claim ids no one holds.

        Coordinates clamp to the panel span so the touch driver's
        full-scale "unpressed marker" filter (``_JUNK_COORD``) can never
        fire on real positions.  Ids outside 0..4 and a sixth finger
        are ignored, matching the FT5x06's five-point register map.
        """
        if event not in ("down", "move", "up") or not 0 <= ft_id < _MAX_FINGERS:
            return
        x = min(max(x, 0), WIDTH - 1)
        y = min(max(y, 0), HEIGHT - 1)
        with self._lock:
            holder = self._owners.get(ft_id)
            if holder is not None and holder != owner:
                return  # another viewer holds this finger id
            if event == "up":
                self._points.pop(ft_id, None)
                self._owners.pop(ft_id, None)
                return
            if ft_id not in self._points and len(self._points) >= _MAX_FINGERS:
                return
            seen = self._points.get(ft_id)
            # A finger reports DOWN once, then CONTACT per move.
            self._points[ft_id] = (
                x,
                y,
                EVENT_CONTACT if event == "move" and seen is not None else EVENT_DOWN,
            )
            self._owners[ft_id] = owner

    def release_owner(self, owner) -> None:
        """Lift every finger a viewer pressed (its connection ended)."""
        with self._lock:
            dead = [ft_id for ft_id, o in self._owners.items() if o is owner]
            for ft_id in dead:
                self._points.pop(ft_id, None)
                self._owners.pop(ft_id, None)

    def sample(self) -> tuple[int, bytes, bool]:
        """``(count, records, any_down)`` — the TD_STATUS byte, the raw
        point records as the chip would serve them from register 0x03,
        and whether any finger is down (the INT line level)."""
        with self._lock:
            records = bytearray()
            for ft_id in sorted(self._points):
                x, y, event = self._points[ft_id]
                records += bytes(
                    [
                        (event << 6) | (x >> 8),  # XH: event[7:6], X[11:8]
                        x & 0xFF,  # XL
                        (ft_id << 4) | (y >> 8),  # YH: finger id[7:4], Y[11:8]
                        y & 0xFF,  # YL
                        0,  # weight, ignored by every driver
                        0,
                    ]
                )
            return len(self._points), bytes(records), bool(self._points)


def _row_le_bytes(view) -> bytes:
    """The buffer's bytes as little-endian word pairs (the wire format).

    ``array('H').tobytes()`` is already little-endian on every CPython
    platform this package supports; the fallback exists for completeness
    on a theoretical big-endian host.
    """
    if sys.byteorder == "little":
        return view.tobytes()  # machine order = wire order
    return struct.pack(f"<{len(view)}H", *view)  # pragma: no cover


class SimulatedBus:
    """The ``Bus`` protocol backed by an in-memory framebuffer.

    Args:
        pins: Kept for interface parity (``Touch`` validates its wiring
            against ``bus.pins``); no pins are actually driven.
        serve: Start the WebSocket server on :meth:`open`.  Pass
            ``False`` for headless use — the framebuffer part then works
            with no server and no ``websockets`` package.
        host, port: Where to serve (defaults: ``ERTFTM070_SIM_HOST`` /
            ``ERTFTM070_SIM_PORT``, then ``0.0.0.0`` / ``8000`` — the
            wildcard lets a browser on another machine view a sim
            running on the Pi, or a phone reach it over the LAN).
    """

    def __init__(
        self,
        pins: Pins = DEFAULT_PINS,
        *,
        serve: bool = True,
        host: str | None = None,
        port: int | None = None,
    ) -> None:
        self.pins = pins
        self.touch = TouchState()
        self._serve = serve
        self.host = host if host is not None else os.environ.get(
            "ERTFTM070_SIM_HOST", "0.0.0.0"
        )
        if port is None:
            raw = os.environ.get("ERTFTM070_SIM_PORT", "8000")
            try:
                port = int(raw)
            except ValueError:
                raise ValueError(
                    f"ERTFTM070_SIM_PORT must be an integer, got {raw!r}"
                ) from None
            if not 0 <= port <= 65535:
                raise ValueError(f"ERTFTM070_SIM_PORT out of range: {port}")
        self.port = port
        self._fb = bytearray(WIDTH * HEIGHT * 2)
        self._lock = threading.Lock()  # guards _fb; rows in, snapshots out
        self._pin_levels = {}  # pin -> last written level
        self._inputs = set()  # pins set to input: read as INT-style levels
        self._opened = False
        self._t0 = 0.0  # TE phase anchor, set on open
        self._server = None  # type: SimServer | None

    # -- lifecycle --

    def open(self) -> None:
        """Bring the bus up and (unless ``serve=False``) start the
        server.  Idempotent.  The server binds before this returns, so a
        port conflict surfaces here as an error, not later."""
        if self._opened:
            return
        self._t0 = time.monotonic()
        self._opened = True
        if self._serve:
            server = SimServer(self)
            try:
                server.start(self.host, self.port)
            except BaseException:
                self._opened = False  # a failed open leaves no half-open bus
                raise
            self._server = server
            log.info(
                "simulated display: open http://%s:%d/ in a browser",
                self.host,
                self.server_port,
            )
            if self.host not in ("localhost", "127.0.0.1", "::1"):
                log.warning(
                    "simulator: serving on %s — anyone who can reach this "
                    "host can view the simulated display",
                    self.host,
                )
        else:
            log.info("simulated display ready (800x480, backend=sim, server off)")

    def close(self) -> None:
        """Stop the server (if any) and mark the bus closed.  Idempotent."""
        if self._server is not None:
            self._server.stop()
            self._server = None
        self._opened = False

    @property
    def server_port(self) -> int | None:
        """The port actually bound (``None`` before open / without a
        server).  Useful when ``port=0`` asked the OS to pick one."""
        return None if self._server is None else self._server.port

    def _check(self) -> None:
        if not self._opened:
            raise RuntimeError("bus not open — call open() first")

    # -- Bus protocol: pins --

    def pin_mode(self, pin: int, output: bool) -> None:
        self._check()
        if output:
            self._inputs.discard(pin)
        else:
            self._inputs.add(pin)

    def pin_write(self, pin: int, level: bool) -> None:
        self._check()
        self._pin_levels[pin] = bool(level)

    def pin_read(self, pin: int) -> bool:
        """Sample a pin.

        The TE pin answers the emulated blanking waveform (~53.7 Hz, see
        ``TE_PERIOD``) so vsync_wait/refresh_rate behave in sim.  Every
        other pin set to *input* answers the touch INT line — idle low,
        high while any finger is down, the polarity measured on the real
        panel — which makes ``Touch.wait_touch``'s poll work no matter
        which GPIO the INT pin is wired to.  Other pins return their
        last written level.
        """
        self._check()
        if pin == self.pins.te:
            phase = (time.monotonic() - self._t0) % TE_PERIOD
            return phase < TE_PERIOD * TE_BLANK_FRACTION
        if pin in self._inputs:
            return self.touch.sample()[2]
        return self._pin_levels.get(pin, False)

    # -- Bus protocol: registers and pixels --

    def write_byte(self, value: int) -> None:
        """Register traffic is not simulated — a no-op."""
        self._check()
        log.debug("sim: write_byte 0x%02X (ignored)", value & 0xFF)

    def read_word(self) -> int:
        """Register read-back is not simulated; always 0.  Diagnostics
        that need it (selftest, gramcheck) report failure in sim."""
        self._check()
        return 0

    def pixel_stream(self, buf: Any) -> None:
        """A raw burst without window framing is not simulated — no-op
        (rows arrive via ``row_blit``)."""
        self._check()
        log.debug("sim: pixel_stream of %d words (ignored)", len(_word_view(buf)))

    def row_blit(self, x0: int, x1: int, y: int, buf: Any) -> None:
        """One row of a blit: validate like the real backends, copy the
        words into the framebuffer, stream the row to the browser.

        Unlike the real backends, the window is also bounds-checked —
        the framebuffer is a fixed array, and a controller-space window
        outside 800x480 would corrupt it instead of wrapping in GRAM.
        """
        self._check()
        if x0 > x1:
            raise ValueError("row_blit: x0 must not exceed x1")
        view = _word_view(buf)  # also rejects odd-length byte buffers
        if len(view) != x1 - x0 + 1:
            raise ValueError(
                "row_blit: buffer must hold one 16-bit word per window "
                f"column (x1 - x0 + 1 == {x1 - x0 + 1} words), got {len(view)}"
            )
        if x0 < 0 or x1 >= WIDTH or y < 0 or y >= HEIGHT:
            raise ValueError(
                f"row_blit: window x0={x0} x1={x1} y={y} outside the "
                f"{WIDTH}x{HEIGHT} panel"
            )
        row_bytes = _row_le_bytes(view)
        with self._lock:
            offset = (y * WIDTH + x0) * 2
            self._fb[offset : offset + len(row_bytes)] = row_bytes
        if self._server is not None:
            # The wire payload is only needed when browsers are being
            # fed — a headless ``serve=False`` sim never pays the pack.
            payload = ROW_HEADER.pack(ROW_MSG, x0, x1, y, len(view)) + row_bytes
            self._server.push_row(payload)
        time.sleep(_ROW_TIME)  # the panel's ~1.3 ms row burst

    # -- framebuffer access --

    def pixel(self, x: int, y: int) -> int:
        """The RGB565 word currently at panel coordinate (x, y)."""
        if not 0 <= x < WIDTH or not 0 <= y < HEIGHT:
            raise ValueError(
                f"pixel: ({x}, {y}) outside the {WIDTH}x{HEIGHT} panel"
            )
        with self._lock:
            offset = (y * WIDTH + x) * 2
            return self._fb[offset] | (self._fb[offset + 1] << 8)

    def full_frame(self) -> bytes:
        """A consistent snapshot of the framebuffer (for browser connects)."""
        with self._lock:
            return bytes(self._fb)


class SimulatedI2C:
    """The FT5x06 register file served from a :class:`TouchState`.

    Mirrors :class:`ertftm070._i2c.I2C`'s method set (open/close/
    write_reg/read_reg, ``I2CError`` when closed) so ``Touch`` drives it
    unchanged: TD_STATUS (0x02) answers the touch count, the point
    records at 0x03 come from ``state.sample()``, everything else reads
    zero — enough for ``Touch.open``'s phantom wait (a fresh state reads
    count 0, i.e. sane) and for ``read()``/``wait_touch()``.
    """

    def __init__(
        self,
        addr: int = FT5X06_ADDR,
        bus: int = 1,
        state: TouchState | None = None,
    ):
        self.addr = addr
        self.path = f"/dev/i2c-{bus}"  # parity with I2C; never opened
        self._state = state if state is not None else TouchState()
        self._opened = False

    def open(self) -> None:
        self._opened = True

    def close(self) -> None:
        self._opened = False

    def _check(self) -> None:
        if not self._opened:
            raise I2CError("I2C bus not open — call open() first")

    def write_reg(self, reg: int, data: bytes | list[int] = b"") -> None:
        self._check()
        log.debug("sim: i2c write reg 0x%02X: %s (ignored)", reg, bytes(data))

    def read_reg(self, reg: int, n: int) -> bytes:
        self._check()
        if reg == TD_STATUS:
            return bytes([self._state.sample()[0]])
        if reg == _POINT_BASE:
            records = self._state.sample()[1]
            if len(records) < n:
                # The reader may already have applied an ``up`` between
                # this status read and this records read.  Serve the
                # shrunken claim as release-marked records — a real chip
                # holds EVENT_UP bytes in the slot for one frame — never
                # zero bytes, which would decode as a ghost press at
                # (0,0).  Padding is a whole number of 6-byte records
                # (n == count * 6, count <= 5).
                release = bytes(
                    [EVENT_UP << 6, 0, 0, 0, 0, 0]  # (0,0) up; id 0
                )
                records += release * ((n - len(records) + 5) // 6)
            return records[:n]
        log.debug("sim: i2c read of unmodeled reg 0x%02X -> zeros", reg)
        return b"\x00" * n


class _ConnState:
    """One connection's outbound buffer: one pending payload per address
    (x0, x1, y), latest content wins, bounded depth, FIFO send order.

    A display only ever needs the newest content: an address written
    again before its row was sent replaces the pending one, and a burst
    deeper than the bound drops the oldest addresses.  An app that
    re-blits a region every loop can therefore never starve the
    connection — the browser always converges to the latest frame — and
    the FULL frame sent on connect makes any drop self-healing.  Only
    the loop thread touches a state; ``push`` wakes the sender via an
    asyncio.Event.
    """

    def __init__(self, maxlen: int = 2048) -> None:
        self._maxlen = maxlen
        self._order = deque()  # keys in send order
        self._present = set()  # keys with a pending payload
        self._rows = {}  # key -> payload (latest content)
        self.wake = asyncio.Event()
        self.closed = False

    def push(self, payload: bytes | None) -> None:
        if self.closed:
            return
        if payload is None:  # shutdown sentinel
            self.closed = True
            self.wake.set()
            return
        key = payload[:7]  # type byte + (x0, x1, y): rows and the FULL
        # frame address different bytes, so neither can replace the other
        if key in self._present:
            self._rows[key] = payload  # latest content wins
            return
        if len(self._order) >= self._maxlen:
            dropped = self._order.popleft()  # drop the oldest address
            self._present.discard(dropped)
            del self._rows[dropped]
        self._order.append(key)
        self._present.add(key)
        self._rows[key] = payload
        self.wake.set()

    def next(self) -> bytes | None:
        if not self._order:
            return None
        key = self._order.popleft()
        self._present.discard(key)
        return self._rows.pop(key)


class SimServer:
    """The WebSocket server: the sim's only I/O to the outside world.

    Two threads touch sim state: the app thread (bus/touch/I2C) and one
    daemon asyncio thread here.  ``push_row`` crosses over via
    ``call_soon_threadsafe``; each connection owns a :class:`_ConnState`
    of immutable bytes, so the loop thread never touches the app's row
    buffers.  See :class:`_ConnState` for why a flooding app cannot
    starve the browser.

    Lifecycle: ``start`` binds before returning (a port conflict raises
    here as :class:`~ertftm070.errors.Ertftm070Error`, never an
    ``OSError`` that ``Display.open`` would wrap as NotOnRaspberryPi);
    ``stop`` is idempotent and bounded.
    """

    def __init__(self, bus: SimulatedBus) -> None:
        self._bus = bus
        self._loop = None  # type: asyncio.AbstractEventLoop | None
        self._thread = None  # type: threading.Thread | None
        self._server = None
        self._conns = {}  # connection -> _ConnState
        self._port = None  # type: int | None
        self._page = None  # type: str | None

    @property
    def port(self) -> int | None:
        return self._port

    # -- lifecycle (app thread) --

    def start(self, host: str, port: int) -> None:
        if self._loop is not None:
            return
        try:
            # The new asyncio implementation (websockets >= 13, the
            # default API since 14) — identical subset across versions,
            # so a plain ImportError means "missing", which is the case
            # we diagnose.
            from websockets.asyncio.server import serve
        except ImportError as exc:
            raise Ertftm070Error(
                "the simulator needs the websockets package: "
                "pip install 'ertftm070[sim]'"
            ) from exc
        try:
            self._page = Path(__file__).with_name("sim.html").read_text(
                encoding="utf-8"
            )
        except OSError as exc:
            raise Ertftm070Error(
                "the simulator page (sim.html) is missing from the package"
            ) from exc
        ready = threading.Event()
        outcome = {}

        def _run() -> None:
            loop = asyncio.new_event_loop()
            self._loop = loop
            asyncio.set_event_loop(loop)
            try:
                server = loop.run_until_complete(self._bind(serve, host, port))
            except BaseException as exc:  # bind failure etc.: surface on app thread
                outcome["error"] = exc
                ready.set()
                loop.close()  # nothing bound: no transports to lose
                return
            self._server = server
            self._port = server.sockets[0].getsockname()[1]
            outcome["ok"] = True
            ready.set()
            loop.run_forever()
            loop.close()

        self._thread = threading.Thread(target=_run, name="ertftm070-sim", daemon=True)
        self._thread.start()
        if not ready.wait(timeout=10):
            # The bind never completed (a hostname whose DNS lookup
            # hangs, a firewall that drops the listen socket).  Stop the
            # server thread before reporting — otherwise it stays alive
            # and eventually binds, and the *next* open() then fails
            # with a self-inflicted port conflict against the leaked
            # thread that no retry can clear.  run_until_complete
            # unwinds (the loop stopped before the bind completed), and
            # the error branch inside _run closes the loop.
            loop = self._loop
            if loop is not None:
                try:
                    loop.call_soon_threadsafe(loop.stop)
                except RuntimeError:
                    pass
            self._thread.join(timeout=1.0)
            self._loop = self._thread = None
            raise Ertftm070Error(
                "simulator: server did not start within 10 s"
            )
        if "error" in outcome:
            self._loop = None
            self._thread = None
            raise Ertftm070Error(
                f"simulator: cannot bind {host}:{port} ({outcome['error']}) — "
                f"{_WIRE_HINT}"
            )

    def stop(self) -> None:
        loop, thread = self._loop, self._thread
        self._loop = self._thread = self._port = None
        if loop is None or thread is None:
            return

        async def _shutdown() -> None:
            # Abort in-flight sends first — a client that stopped
            # reading must not stall the close handshake — then wake the
            # handlers parked on wake.wait() with the shutdown sentinel
            # so they exit instead of being destroyed mid-task, then
            # close the server.  All the per-connection waits run
            # concurrently (each capped individually), so any number of
            # stuck connections cost one bounded budget, not one budget
            # per connection, and the whole close stays well inside the
            # app thread's 2 s deadline below.  The shutdown sentinel
            # also covers the handlers the reader-side sentinel
            # (see _reader) already woke.
            close_tasks = [
                asyncio.create_task(
                    asyncio.wait_for(connection.close(), timeout=0.5)
                )
                for connection in list(self._conns)
            ]
            if close_tasks:
                _, pending = await asyncio.wait(close_tasks, timeout=1.5)
                for task in pending:
                    task.cancel()  # client never answered: drop the close
            for state in self._conns.values():
                state.push(None)
            server = self._server
            if server is not None:
                closed = server.close()
                if inspect.isawaitable(closed):  # sync in 15+, awaitable in 13/14
                    closed = await closed
                try:
                    await asyncio.wait_for(server.wait_closed(), timeout=0.5)
                except Exception:
                    pass

        try:
            # A stalled client, a wedged loop or a fresh bind failure all
            # land here — never on the app thread, which keeps its close
            # budget no matter how many connections are stuck.
            asyncio.run_coroutine_threadsafe(_shutdown(), loop).result(timeout=2.0)
        except Exception:
            pass  # best-effort: the thread is a daemon, never a process blocker
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=2.0)
        self._server = None
        self._conns.clear()

    # -- app-thread entry point --

    def push_row(self, payload: bytes) -> None:
        loop = self._loop
        if loop is None or not loop.is_running():
            return
        try:
            loop.call_soon_threadsafe(self._feed, payload)
        except RuntimeError:
            pass  # the loop shut down between the check and the call

    # -- loop thread --

    async def _bind(self, serve, host: str, port: int):
        """Bind inside the running loop.  ``serve``'s calling convention
        changed across websockets releases: a coroutine function in
        13/14, a plain function requiring a running loop in 15+ — await
        it if it is awaitable, call it straight otherwise."""
        server = serve(
            self._handle, host, port, process_request=self._process_request
        )
        if inspect.isawaitable(server):
            server = await server
        return server

    def _feed(self, payload: bytes) -> None:
        if not self._conns:
            return
        for state in self._conns.values():
            state.push(payload)

    async def _handle(self, connection) -> None:
        state = _ConnState()
        # Register + snapshot atomically (no await in between): rows that
        # arrive after this land behind the FULL frame, never before it.
        state.push(bytes([FULL_MSG]) + self._bus.full_frame())
        self._conns[connection] = state
        reader = asyncio.create_task(self._reader(connection))
        try:
            while True:
                await state.wake.wait()
                state.wake.clear()
                while True:
                    payload = state.next()
                    if payload is None:
                        break
                    await connection.send(payload)
                if state.closed:
                    break
        finally:
            reader.cancel()
            self._conns.pop(connection, None)
            try:
                # Tell the browser this connection is over so its
                # onclose fires and the reconnect logic self-heals —
                # whatever ended the handler.
                await connection.close()
            except Exception:
                pass

    async def _reader(self, connection) -> None:
        """Consume one connection's touch messages into the shared state.

        Runs until the connection ends for any reason.  A poisoned
        message (``x: 1e309`` overflows ``int()`` — an ``OverflowError``
        that the per-message ``ValueError`` guard would not catch) must
        not kill the read loop and leave a live but deaf connection, so
        it is caught here too.

        The ``finally`` releases the connection's side of the shutdown
        protocol:

        * the fingers this viewer pressed are lifted — the viewer is
          gone, so no ``up`` for them will ever arrive, and without this
          a browser tab closed mid-press would leave its touches stuck
          down forever on the shared state (and would keep blocking the
          id it pressed against other viewers);
        * the handler is woken with the shutdown sentinel, so a peer
          that disconnected while the handler was parked on
          ``wake.wait()`` does not leave a zombie handler holding the
          connection's slot and its queued rows forever.
        """
        try:
            async for message in connection:
                if not isinstance(message, str):
                    continue
                try:
                    data = json.loads(message)
                except ValueError:
                    log.debug("simulator: ignoring malformed client message")
                    continue
                if not isinstance(data, dict):
                    # Any other JSON shape ([1,2,3], null, "text", 5) has
                    # no .get — without this check one stray message
                    # would AttributeError past the per-message guards
                    # and end the whole read loop.
                    continue
                if data.get("type") != "touch":
                    continue
                event = data.get("event")
                if event not in ("down", "move", "up"):
                    continue
                try:
                    x, y = int(data["x"]), int(data["y"])
                    ft_id = int(data.get("id", 0))
                except (KeyError, TypeError, ValueError, OverflowError):
                    # NaN / overflowing json numbers ("1e309") raise
                    # OverflowError from int(); skip just this message.
                    continue
                self._bus.touch.update(ft_id, x, y, event, owner=connection)
        except Exception:
            pass  # ConnectionClosed and friends end the reader
        finally:
            self._bus.touch.release_owner(connection)
            state = self._conns.get(connection)
            if state is not None:
                state.push(None)  # shutdown sentinel: wake the handler

    async def _process_request(self, connection, request):
        """Same-origin policy, then serve the page at plain GET /;
        everything else (the /ws upgrade) proceeds with the WebSocket
        handshake.

        The server hands every connection the full framebuffer and
        accepts touches, so it must refuse requests a browser would only
        send cross-origin: a malicious page open on the operator's
        machine (or anywhere on the LAN) could otherwise reach a sim
        bound to 0.0.0.0, read the whole display and inject touches with
        no interaction at all.  Browsers attach an ``Origin`` header to
        WebSocket upgrades (never to a plain top-level page load), and
        the served page talks to ``ws://<same host>`` — so an Origin
        whose host:port matches the request's ``Host`` header is exactly
        the intended client.  Requests without an Origin header (raw
        non-browser clients, tests) are admitted.  ``None`` lets the
        handshake proceed; a ``respond`` return completes the HTTP
        exchange and drops the connection."""
        origin = request.headers.get("Origin")
        if origin is not None:
            host = request.headers.get("Host")
            if host is None or urlsplit(origin).netloc.lower() != host.lower():
                log.warning("simulator: refusing cross-origin %s", origin)
                return connection.respond(
                    HTTPStatus.FORBIDDEN, "cross-origin request refused"
                )
        if request.path == "/":
            response = connection.respond(HTTPStatus.OK, self._page)
            response.headers["Content-Type"] = "text/html; charset=utf-8"
            return response
        return None
