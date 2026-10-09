"""Async client for the Blackmagic SmartView Ethernet Protocol (TCP 9992).

The protocol is text based and very similar to the Videohub protocol:

* On connect the device sends a full state dump as a series of blocks.
* A block starts with a header line ending in a colon (``MONITOR A:``),
  followed by ``Key: Value`` lines, and ends with a blank line.
* A block sent by the client is answered with ``ACK`` or ``NAK``. Input the
  device cannot parse is answered with a ``# Error: ...`` line instead.

Behaviour seen on a SmartScope Duo 4K (protocol 1.3):

* Changes made by this client are acknowledged but not echoed back, so the
  client stores acknowledged values itself.
* ``PING:`` is not supported (answered with ``# Error``), so dead connections
  are detected with TCP keepalive and TCP user timeout instead. A monitor that
  is switched off is noticed after about a minute; shorter network hiccups
  are ridden out without dropping the connection.
* Every command is answered with ``ACK``. No answer means the monitor is not
  reachable, so the command is reported as failed.
* Right after a previous connection was closed, the device may close a new
  connection straight away. Connecting is therefore retried a few times.

This module has no Home Assistant dependencies.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import contextlib
from dataclasses import dataclass, field
import logging
import socket

from .const import (
    BLOCK_DEVICE,
    BLOCK_MONITOR_PREFIX,
    BLOCK_NETWORK,
    BLOCK_PREAMBLE,
    COMMAND_TIMEOUT,
    CONNECT_ATTEMPTS,
    CONNECT_RETRY_DELAY,
    CONNECT_TIMEOUT,
    FIELD_IDENTIFY,
    INITIAL_DUMP_TIMEOUT,
    KEEPALIVE_COUNT,
    KEEPALIVE_IDLE,
    KEEPALIVE_INTERVAL,
    RECONNECT_MAX_DELAY,
    RECONNECT_MIN_DELAY,
    TCP_USER_TIMEOUT_MS,
)

_LOGGER = logging.getLogger(__name__)

# Fields that are transient on the device and must not be stored optimistically
_TRANSIENT_FIELDS = frozenset({FIELD_IDENTIFY})


class SmartViewError(Exception):
    """Base error for the SmartView client."""


class SmartViewConnectionError(SmartViewError):
    """Raised when the device cannot be reached or the connection drops."""


class SmartViewCommandError(SmartViewError):
    """Raised when the device rejects a command (NAK or error line)."""


class _DeviceClosedError(SmartViewConnectionError):
    """The device closed the connection before sending its state."""


@dataclass
class SmartViewState:
    """Last known state of the device, as reported by the device."""

    protocol_version: str | None = None
    device: dict[str, str] = field(default_factory=dict)
    network: dict[str, str] = field(default_factory=dict)
    monitors: dict[str, dict[str, str]] = field(default_factory=dict)
    other_blocks: dict[str, dict[str, str]] = field(default_factory=dict)

    @property
    def model(self) -> str | None:
        """Return the model name reported by the device."""
        return self.device.get("Model")

    @property
    def monitor_count(self) -> int | None:
        """Return the number of monitors (LCDs) the device reports."""
        value = self.device.get("Monitors")
        if value is None:
            return None
        try:
            return int(value)
        except ValueError:
            _LOGGER.debug("Unexpected Monitors value: %s", value)
            return None

    def as_dict(self) -> dict[str, object]:
        """Return the state as a plain dict (used for diagnostics)."""
        return {
            "protocol_version": self.protocol_version,
            "device": dict(self.device),
            "network": dict(self.network),
            "monitors": {key: dict(value) for key, value in self.monitors.items()},
            "other_blocks": {key: dict(value) for key, value in self.other_blocks.items()},
        }


def _enable_keepalive(writer: asyncio.StreamWriter) -> None:
    """Turn on TCP keepalive so a switched-off monitor is noticed."""
    sock = writer.get_extra_info("socket")
    if sock is None:
        return
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        for name, value in (
            ("TCP_KEEPIDLE", KEEPALIVE_IDLE),
            ("TCP_KEEPINTVL", KEEPALIVE_INTERVAL),
            ("TCP_KEEPCNT", KEEPALIVE_COUNT),
            ("TCP_USER_TIMEOUT", TCP_USER_TIMEOUT_MS),
        ):
            option = getattr(socket, name, None)
            if option is not None:
                sock.setsockopt(socket.IPPROTO_TCP, option, value)
    except OSError as err:
        _LOGGER.debug("Could not enable TCP keepalive: %s", err)


class SmartViewClient:
    """Persistent push client for a SmartView or SmartScope monitor."""

    def __init__(self, host: str, port: int) -> None:
        """Initialise the client."""
        self.host = host
        self.port = port
        self.state = SmartViewState()

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._read_task: asyncio.Task[None] | None = None
        self._supervisor_task: asyncio.Task[None] | None = None

        self._connected = False
        self._closing = False
        self._dump_complete = asyncio.Event()
        self._command_lock = asyncio.Lock()
        self._pending: asyncio.Future[bool] | None = None

        self._listeners: list[Callable[[], None]] = []

    # ------------------------------------------------------------------ public

    @property
    def connected(self) -> bool:
        """Return True when the TCP connection is up and the dump was received."""
        return self._connected

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Register a callback for state/connection changes. Returns a remover."""
        self._listeners.append(listener)

        def _remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return _remove

    async def connect(self) -> None:
        """Connect and read the state dump, retrying if the device is busy."""
        for attempt in range(1, CONNECT_ATTEMPTS + 1):
            try:
                await self._connect_once()
            except _DeviceClosedError as err:
                if attempt == CONNECT_ATTEMPTS:
                    raise SmartViewConnectionError(
                        f"{self.host} keeps closing the connection; is another "
                        "controller using all connection slots?"
                    ) from err
                _LOGGER.debug(
                    "%s closed the connection right away (attempt %s/%s), retrying",
                    self.host,
                    attempt,
                    CONNECT_ATTEMPTS,
                )
                await asyncio.sleep(CONNECT_RETRY_DELAY)
            else:
                return

    async def start(self) -> None:
        """Start the background supervisor that keeps the connection alive."""
        self._closing = False
        if self._supervisor_task is None or self._supervisor_task.done():
            self._supervisor_task = asyncio.create_task(
                self._supervise(), name=f"smartview_supervisor_{self.host}"
            )

    async def close(self) -> None:
        """Close the connection and stop all background tasks."""
        self._closing = True
        if self._supervisor_task is not None:
            self._supervisor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._supervisor_task
            self._supervisor_task = None
        await self._teardown()

    async def set_monitor(self, monitor: str, fields: dict[str, str]) -> None:
        """Change one or more settings of a monitor (``A``, ``B``)."""
        await self.send_block(f"{BLOCK_MONITOR_PREFIX}{monitor}", fields)

    async def set_device(self, fields: dict[str, str]) -> None:
        """Change settings in the device block (e.g. the name)."""
        await self.send_block(BLOCK_DEVICE, fields)

    async def send_block(self, header: str, fields: dict[str, str]) -> None:
        """Send a block and wait for ACK. Raises on NAK or connection loss."""
        lines = [f"{header}:"] + [f"{key}: {value}" for key, value in fields.items()]
        acked = await self._send_raw("\n".join(lines) + "\n\n")
        if acked is None:
            raise SmartViewConnectionError(f"No answer from {self.host} for {header}")
        if acked is False:
            raise SmartViewCommandError(f"Device rejected {header}: {fields}")
        # The device acknowledges changes but does not echo them back, so store
        # the new values here. Anything the device reports later overrides this.
        target = self._block_target(header)
        if target is not None:
            for key, value in fields.items():
                if key not in _TRANSIENT_FIELDS:
                    target[key] = value
            self._notify()

    def preload(self, model: str | None, name: str | None, monitors: int | None) -> None:
        """Fill in the last known identity, so entities can be created while offline."""
        device: dict[str, str] = {}
        if model:
            device["Model"] = model
        if name:
            device["Name"] = name
        if monitors:
            device["Monitors"] = str(monitors)
        self.state = SmartViewState(device=device)

    @classmethod
    async def probe(cls, host: str, port: int) -> SmartViewState:
        """Connect once, read the state dump and disconnect (used by config flow)."""
        client = cls(host, port)
        try:
            await client.connect()
            return client.state
        finally:
            await client.close()

    # ---------------------------------------------------------------- internal

    async def _connect_once(self) -> None:
        """Open the connection and wait for the initial state dump."""
        await self._teardown()
        self.state = SmartViewState()
        self._dump_complete = asyncio.Event()

        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), CONNECT_TIMEOUT
            )
        except (OSError, TimeoutError) as err:
            raise SmartViewConnectionError(
                f"Cannot connect to {self.host}:{self.port}: {err}"
            ) from err
        _enable_keepalive(self._writer)

        read_task = asyncio.create_task(self._read_loop(), name=f"smartview_read_{self.host}")
        self._read_task = read_task
        dump_task = asyncio.create_task(self._dump_complete.wait())
        try:
            await asyncio.wait(
                {read_task, dump_task},
                timeout=INITIAL_DUMP_TIMEOUT,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            dump_task.cancel()

        if read_task.done():
            await self._teardown()
            raise _DeviceClosedError(f"{self.host} closed the connection during setup")

        if not self._dump_complete.is_set():
            if not self.state.device:
                await self._teardown()
                raise SmartViewConnectionError(
                    f"No SmartView state received from {self.host}:{self.port}"
                )
            _LOGGER.debug(
                "Initial dump from %s incomplete after %ss, continuing with %s",
                self.host,
                INITIAL_DUMP_TIMEOUT,
                list(self.state.monitors),
            )

        self._connected = True
        _LOGGER.debug("Connected to %s (%s)", self.host, self.state.model)
        self._notify()

    async def _send_raw(self, payload: str) -> bool | None:
        """Write a payload and wait for ACK/NAK. Returns True/False/None (no answer)."""
        async with self._command_lock:
            writer = self._writer
            if writer is None or writer.is_closing():
                raise SmartViewConnectionError(f"Not connected to {self.host}")

            loop = asyncio.get_running_loop()
            self._pending = loop.create_future()
            _LOGGER.debug("Send to %s: %r", self.host, payload)
            try:
                writer.write(payload.encode("utf-8"))
                await writer.drain()
            except OSError as err:
                self._pending = None
                raise SmartViewConnectionError(f"Write to {self.host} failed: {err}") from err

            try:
                return await asyncio.wait_for(self._pending, COMMAND_TIMEOUT)
            except TimeoutError:
                _LOGGER.debug("No ACK/NAK from %s for %r", self.host, payload)
                return None
            finally:
                self._pending = None

    def _block_target(self, header: str) -> dict[str, str] | None:
        """Return the state dict that belongs to a block header."""
        if header == BLOCK_DEVICE:
            return self.state.device
        if header == BLOCK_NETWORK:
            return self.state.network
        if header.startswith(BLOCK_MONITOR_PREFIX):
            monitor = header[len(BLOCK_MONITOR_PREFIX) :].strip()
            return self.state.monitors.setdefault(monitor, {})
        return None

    async def _read_loop(self) -> None:
        """Read lines from the device and dispatch complete blocks."""
        reader = self._reader
        if reader is None:
            return
        header: str | None = None
        body: list[str] = []
        try:
            while True:
                raw = await reader.readline()
                if not raw:
                    _LOGGER.debug("Connection to %s closed by device", self.host)
                    return
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")

                if header is None:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    if stripped in ("ACK", "NAK"):
                        _LOGGER.debug("%s from %s", stripped, self.host)
                        self._resolve_pending(stripped == "ACK")
                        continue
                    if stripped.startswith("#"):
                        # e.g. "# Error: 1.0: syntax error, unexpected identifier"
                        _LOGGER.debug("Device message from %s: %s", self.host, stripped)
                        if "error" in stripped.lower():
                            self._resolve_pending(False)
                        continue
                    if stripped.endswith(":"):
                        header = stripped[:-1].strip()
                        body = []
                        continue
                    _LOGGER.debug("Ignoring stray line from %s: %r", self.host, line)
                    continue

                if line.strip():
                    body.append(line)
                    continue

                self._handle_block(header, body)
                header = None
                body = []
        except (OSError, asyncio.IncompleteReadError, ValueError) as err:
            _LOGGER.debug("Read from %s failed: %s", self.host, err)

    def _resolve_pending(self, acked: bool) -> None:
        """Resolve the outstanding command future, if any."""
        if self._pending is not None and not self._pending.done():
            self._pending.set_result(acked)

    def _handle_block(self, header: str, body: list[str]) -> None:
        """Parse a complete block and merge it into the state."""
        fields: dict[str, str] = {}
        for line in body:
            key, sep, value = line.partition(":")
            if not sep:
                _LOGGER.debug("Ignoring line without colon in %s: %r", header, line)
                continue
            fields[key.strip()] = value.strip()

        _LOGGER.debug("Block from %s: %s %s", self.host, header, fields)

        if header == BLOCK_PREAMBLE:
            self.state.protocol_version = fields.get("Version")
        else:
            target = self._block_target(header)
            if target is None:
                self.state.other_blocks.setdefault(header, {}).update(fields)
            else:
                target.update(fields)

        if not self._dump_complete.is_set() and self._initial_dump_received():
            self._dump_complete.set()
        if self._connected:
            self._notify()

    def _initial_dump_received(self) -> bool:
        """Return True when the device block and all monitor blocks have arrived."""
        if not self.state.device:
            return False
        count = self.state.monitor_count or 1
        expected = {chr(ord("A") + index) for index in range(count)}
        return expected.issubset(self.state.monitors)

    async def _supervise(self) -> None:
        """Keep the connection alive and reconnect with back-off."""
        delay = RECONNECT_MIN_DELAY
        while not self._closing:
            read_task = self._read_task
            if self._connected and read_task is not None:
                await asyncio.wait({read_task})
                if self._closing:
                    return
                _LOGGER.info("Lost connection to SmartView at %s", self.host)
                await self._teardown()
                delay = RECONNECT_MIN_DELAY

            await asyncio.sleep(delay)
            if self._closing:
                return
            try:
                await self.connect()
            except SmartViewConnectionError as err:
                _LOGGER.debug("Reconnect to %s failed: %s", self.host, err)
                delay = min(delay * 2, RECONNECT_MAX_DELAY)
            else:
                _LOGGER.info("Reconnected to SmartView at %s", self.host)
                delay = RECONNECT_MIN_DELAY

    async def _close_transport(self) -> None:
        """Close the socket."""
        writer = self._writer
        if writer is not None:
            # Also close when the device already closed its side, to free the socket
            writer.close()
            try:
                await writer.wait_closed()
            except OSError as err:
                _LOGGER.debug("Error while closing connection to %s: %s", self.host, err)

    async def _teardown(self) -> None:
        """Close the socket and stop the read task."""
        was_connected = self._connected
        self._connected = False

        task = self._read_task
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._read_task = None

        await self._close_transport()
        self._reader = None
        self._writer = None

        if self._pending is not None and not self._pending.done():
            self._pending.set_exception(SmartViewConnectionError("Connection closed"))

        if was_connected:
            self._notify()

    def _notify(self) -> None:
        """Call all registered listeners."""
        for listener in list(self._listeners):
            listener()
