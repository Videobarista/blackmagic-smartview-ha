"""Async client for the Blackmagic SmartView and Audio Monitor Ethernet Protocols.

SmartView / SmartScope monitors listen on TCP 9992, the Audio Monitor 12G on
TCP 9996. Both speak the same text protocol, very similar to the Videohub one:

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

Behaviour seen on an Audio Monitor 12G (protocol 1.0):

* The state dump ends with an ``END PRELUDE:`` block.
* Changes, including those made on the front panel, are pushed as blocks with
  only the changed fields. ``ACK`` does not always mean applied (an invalid
  value is acknowledged and then reported unchanged), so nothing is stored
  optimistically: after an ACK the client asks for the block again by sending
  it empty, and the device answers with its real state.
* ``Gain: Speaker Stereo 114`` and ``Gain: Headphone Stereo 0`` share one key;
  they are stored as ``Gain Speaker Stereo`` and ``Gain Headphone Stereo``.

The device type is detected from the device block it sends.

Security model. The protocol has no authentication or encryption, so the
client cannot verify who it talks to. What it can do:

* Outgoing values never contain line breaks or other control characters, so a
  value can never add lines or blocks to a command (no command injection).
* Data from the device is untrusted: lines, blocks, keys, values, the number
  of monitors and the number of unknown blocks are all limited, so a faulty or
  malicious device cannot make memory grow or flood Home Assistant.
* State changes are passed on at most every ``NOTIFY_INTERVAL`` seconds, and
  an error in one listener never breaks the connection.
* MAC addresses are masked in debug logs.

This module has no Home Assistant dependencies.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
import contextlib
from dataclasses import dataclass, field
import logging
import re
import socket

from .const import (
    AUDIO_MONITOR_BLOCKS,
    BLOCK_AUDIO_OUTPUT,
    BLOCK_AUDIOMONITOR_DEVICE,
    BLOCK_DEVICE,
    BLOCK_END_PRELUDE,
    BLOCK_MONITOR_PREFIX,
    BLOCK_NETWORK,
    BLOCK_PREAMBLE,
    COMMAND_TIMEOUT,
    CONNECT_ATTEMPTS,
    CONNECT_RETRY_DELAY,
    CONNECT_TIMEOUT,
    DEVICE_TYPE_AUDIO_MONITOR,
    DEVICE_TYPE_SMARTVIEW,
    FIELD_GAIN,
    FIELD_IDENTIFY,
    INITIAL_DUMP_TIMEOUT,
    KEEPALIVE_COUNT,
    KEEPALIVE_IDLE,
    KEEPALIVE_INTERVAL,
    MAX_KEY_LENGTH,
    MAX_LINES_PER_BLOCK,
    MAX_MONITORS,
    MAX_OTHER_BLOCKS,
    MAX_VALUE_LENGTH,
    NOTIFY_INTERVAL,
    READ_LINE_LIMIT,
    RECONNECT_MAX_DELAY,
    RECONNECT_MIN_DELAY,
    TCP_USER_TIMEOUT_MS,
)

_LOGGER = logging.getLogger(__name__)

# Fields that are transient on the device and must not be stored optimistically
_TRANSIENT_FIELDS = frozenset({FIELD_IDENTIFY})
# Monitor letters the client accepts from the device
MONITOR_IDS: tuple[str, ...] = tuple(chr(ord("A") + index) for index in range(MAX_MONITORS))
# A MAC address, with or without separators (Blackmagic hostnames end in one)
_MAC_RE = re.compile(
    r"(?i)(?<![0-9a-f])(?:(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}|[0-9a-f]{12})(?![0-9a-f])"
)
_MAC_MASK = "xxxxxxxxxxxx"
# "Speaker Stereo 114" -> ("Speaker Stereo", "114")
_GAIN_RE = re.compile(r"^(?P<name>\S.*?)\s+(?P<value>-?\d+)$")


def mask_mac(text: str) -> str:
    """Replace MAC addresses in a text, e.g. in ``SmartScopeDuo-7c2e0d000001``."""
    return _MAC_RE.sub(_MAC_MASK, text)


def _has_control_chars(text: str) -> bool:
    """Return True when a text contains a line break or other control character."""
    return any(ord(char) < 32 or ord(char) == 127 for char in text)


class SmartViewError(Exception):
    """Base error for the SmartView client."""


class SmartViewConnectionError(SmartViewError):
    """Raised when the device cannot be reached or the connection drops."""


class SmartViewCommandError(SmartViewError):
    """Raised when the device rejects a command (NAK or error line)."""


class SmartViewValueError(SmartViewError, ValueError):
    """Raised before sending when a value is not allowed in a command."""


class _DeviceClosedError(SmartViewConnectionError):
    """The device closed the connection before sending its state."""


@dataclass
class SmartViewState:
    """Last known state of the device, as reported by the device."""

    protocol_version: str | None = None
    device_type: str = DEVICE_TYPE_SMARTVIEW
    device: dict[str, str] = field(default_factory=dict)
    network: dict[str, str] = field(default_factory=dict)
    monitors: dict[str, dict[str, str]] = field(default_factory=dict)
    # Audio Monitor settings and (limited) unknown blocks, by header
    other_blocks: dict[str, dict[str, str]] = field(default_factory=dict)
    prelude_done: bool = False

    @property
    def model(self) -> str | None:
        """Return the model name reported by the device."""
        return self.device.get("Model")

    @property
    def is_audio_monitor(self) -> bool:
        """Return True for an Audio Monitor."""
        return self.device_type == DEVICE_TYPE_AUDIO_MONITOR

    @property
    def name_field(self) -> str:
        """Return the field that holds the user-set device name."""
        return "Label" if self.is_audio_monitor else "Name"

    @property
    def name(self) -> str | None:
        """Return the user-set device name."""
        return self.device.get(self.name_field) or None

    @property
    def unique_id(self) -> str | None:
        """Return the device's own unique ID (Audio Monitor only), lower case."""
        value = (self.device.get("Unique ID") or "").strip().lower()
        return value or None

    def block(self, header: str) -> dict[str, str]:
        """Return the fields of an Audio Monitor (or other) block."""
        return self.other_blocks.get(header, {})

    @property
    def monitor_count(self) -> int | None:
        """Return the number of monitors (LCDs), limited to ``MAX_MONITORS``."""
        value = self.device.get("Monitors")
        if value is None:
            return None
        try:
            count = int(value)
        except ValueError:
            _LOGGER.debug("Unexpected Monitors value: %r", value)
            return None
        if count < 1:
            _LOGGER.debug("Unexpected Monitors value: %r", value)
            return None
        return min(count, MAX_MONITORS)

    def as_dict(self) -> dict[str, object]:
        """Return the state as a plain dict (used for diagnostics)."""
        return {
            "protocol_version": self.protocol_version,
            "device_type": self.device_type,
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


def _validate_outgoing(header: str, fields: Mapping[str, str]) -> None:
    """Refuse anything that could add lines or blocks to a command."""
    if not header or _has_control_chars(header):
        raise SmartViewValueError(f"Invalid block header: {header!r}")
    if not fields:
        raise SmartViewValueError("A command needs at least one field")
    for key, value in fields.items():
        if not key or ":" in key or _has_control_chars(key) or len(key) > MAX_KEY_LENGTH:
            raise SmartViewValueError(f"Invalid field name: {key!r}")
        if _has_control_chars(value) or len(value) > MAX_VALUE_LENGTH:
            raise SmartViewValueError(f"Invalid value for {key}: {value!r}")


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

        # Block parser state
        self._header: str | None = None
        self._body: list[str] = []
        self._overflow = False

        self._listeners: list[Callable[[], None]] = []
        self._flush_handle: asyncio.TimerHandle | None = None

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
        if monitor not in MONITOR_IDS:
            raise SmartViewValueError(f"Unknown monitor: {monitor!r}")
        await self.send_block(f"{BLOCK_MONITOR_PREFIX}{monitor}", fields)

    async def set_device(self, fields: dict[str, str]) -> None:
        """Change settings in the device block (e.g. the name)."""
        header = BLOCK_AUDIOMONITOR_DEVICE if self.state.is_audio_monitor else BLOCK_DEVICE
        await self.send_block(header, fields)

    async def set_name(self, name: str) -> None:
        """Rename the device (``Name`` on a SmartView, ``Label`` on an Audio Monitor)."""
        await self.set_device({self.state.name_field: name})

    async def send_block(self, header: str, fields: dict[str, str]) -> None:
        """Send a block and wait for ACK. Raises on NAK, no answer or a bad value."""
        _validate_outgoing(header, fields)
        lines = [f"{header}:"] + [f"{key}: {value}" for key, value in fields.items()]
        acked = await self._send_raw("\n".join(lines) + "\n\n")
        if acked is None:
            raise SmartViewConnectionError(f"No answer from {self.host} for {header}")
        if acked is False:
            raise SmartViewCommandError(f"Device rejected {header}: {fields}")
        if self.state.is_audio_monitor:
            # Never assume: ask the device for the block, it answers with its state
            await self._request_block(header)
            return
        # A SmartView acknowledges changes but does not echo them back, so store
        # the new values here. Anything the device reports later overrides this.
        target = self._block_target(header)
        if target is not None:
            for key, value in fields.items():
                if key not in _TRANSIENT_FIELDS:
                    target[key] = value
            self._notify(immediate=True)

    def preload(
        self,
        model: str | None,
        name: str | None,
        monitors: int | None,
        device_type: str = DEVICE_TYPE_SMARTVIEW,
    ) -> None:
        """Fill in the last known identity, so entities can be created while offline."""
        state = SmartViewState(
            device_type=(
                DEVICE_TYPE_AUDIO_MONITOR
                if device_type == DEVICE_TYPE_AUDIO_MONITOR
                else DEVICE_TYPE_SMARTVIEW
            )
        )
        if model:
            state.device["Model"] = model[:MAX_VALUE_LENGTH]
        if name:
            state.device[state.name_field] = name[:MAX_VALUE_LENGTH]
        if monitors and not state.is_audio_monitor:
            state.device["Monitors"] = str(max(1, min(int(monitors), MAX_MONITORS)))
        self.state = state

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
        self._reset_parser()

        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port, limit=READ_LINE_LIMIT),
                CONNECT_TIMEOUT,
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
            with contextlib.suppress(asyncio.CancelledError):
                await dump_task

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
        self._notify(immediate=True)

    async def _request_block(self, header: str) -> None:
        """Ask an Audio Monitor for a block; it replies with ACK and the block."""
        try:
            await self._send_raw(f"{header}:\n\n")
        except SmartViewConnectionError as err:
            # The command itself succeeded; the state follows after a reconnect
            _LOGGER.debug("Could not refresh %s from %s: %s", header, self.host, err)

    async def _send_raw(self, payload: str) -> bool | None:
        """Write a payload and wait for ACK/NAK. Returns True/False/None (no answer)."""
        async with self._command_lock:
            writer = self._writer
            if writer is None or writer.is_closing():
                raise SmartViewConnectionError(f"Not connected to {self.host}")

            loop = asyncio.get_running_loop()
            self._pending = loop.create_future()
            _LOGGER.debug("Send to %s: %r", self.host, mask_mac(payload))
            try:
                writer.write(payload.encode("utf-8"))
                await writer.drain()
            except OSError as err:
                self._pending = None
                raise SmartViewConnectionError(f"Write to {self.host} failed: {err}") from err

            try:
                return await asyncio.wait_for(self._pending, COMMAND_TIMEOUT)
            except TimeoutError:
                _LOGGER.debug("No ACK/NAK from %s for %r", self.host, mask_mac(payload))
                return None
            finally:
                self._pending = None

    def _block_target(self, header: str) -> dict[str, str] | None:
        """Return the state dict that belongs to a block header, if it is a known one."""
        if header in (BLOCK_DEVICE, BLOCK_AUDIOMONITOR_DEVICE):
            return self.state.device
        if header == BLOCK_NETWORK:
            return self.state.network
        if header.startswith(BLOCK_MONITOR_PREFIX):
            monitor = header[len(BLOCK_MONITOR_PREFIX) :].strip()
            if monitor not in MONITOR_IDS:
                return None
            return self.state.monitors.setdefault(monitor, {})
        return None

    async def _read_loop(self) -> None:
        """Read lines from the device and feed them to the parser."""
        reader = self._reader
        if reader is None:
            return
        try:
            while True:
                raw = await reader.readline()
                if not raw:
                    _LOGGER.debug("Connection to %s closed by device", self.host)
                    return
                self._process_line(raw.decode("utf-8", errors="replace").rstrip("\r\n"))
        except (OSError, asyncio.IncompleteReadError, ValueError) as err:
            # ValueError: a line longer than READ_LINE_LIMIT
            _LOGGER.debug("Read from %s failed: %s", self.host, err)

    def feed(self, text: str) -> None:
        """Feed text as received from the device to the parser, line by line."""
        for line in text.split("\n"):
            self._process_line(line.rstrip("\r"))

    def _reset_parser(self) -> None:
        """Forget any half-received block."""
        self._header = None
        self._body = []
        self._overflow = False

    def _process_line(self, line: str) -> None:
        """Handle one line from the device."""
        if self._header is None:
            stripped = line.strip()
            if not stripped:
                return
            if stripped in ("ACK", "NAK"):
                _LOGGER.debug("%s from %s", stripped, self.host)
                self._resolve_pending(stripped == "ACK")
                return
            if stripped.startswith("#"):
                # e.g. "# Error: 1.0: syntax error, unexpected identifier"
                _LOGGER.debug("Device message from %s: %s", self.host, mask_mac(stripped))
                if "error" in stripped.lower():
                    self._resolve_pending(False)
                return
            if stripped.endswith(":"):
                self._header = stripped[:-1].strip()
                self._body = []
                self._overflow = False
                return
            _LOGGER.debug("Ignoring stray line from %s: %r", self.host, mask_mac(line))
            return

        if line.strip():
            if len(self._body) < MAX_LINES_PER_BLOCK:
                self._body.append(line)
            else:
                self._overflow = True
            return

        header, body, overflow = self._header, self._body, self._overflow
        self._reset_parser()
        if overflow:
            _LOGGER.debug(
                "Dropped block %r from %s: more than %s lines",
                header[:MAX_KEY_LENGTH],
                self.host,
                MAX_LINES_PER_BLOCK,
            )
            return
        self._handle_block(header, body)

    def _resolve_pending(self, acked: bool) -> None:
        """Resolve the outstanding command future, if any."""
        if self._pending is not None and not self._pending.done():
            self._pending.set_result(acked)

    def _handle_block(self, header: str, body: list[str]) -> None:
        """Parse a complete block and merge it into the state."""
        fields: dict[str, str] = {}
        for line in body:
            key, sep, value = line.partition(":")
            key = key.strip()
            if not sep or not key or len(key) > MAX_KEY_LENGTH:
                _LOGGER.debug("Ignoring line in %s: %r", header[:MAX_KEY_LENGTH], mask_mac(line))
                continue
            value = value.strip()[:MAX_VALUE_LENGTH]
            if header == BLOCK_AUDIO_OUTPUT and key == FIELD_GAIN:
                match = _GAIN_RE.match(value)
                if match is None:
                    _LOGGER.debug("Ignoring unexpected gain line: %r", line)
                    continue
                key, value = f"{FIELD_GAIN} {match['name']}"[:MAX_KEY_LENGTH], match["value"]
            fields[key] = value

        _LOGGER.debug(
            "Block from %s: %s %s",
            self.host,
            header[:MAX_KEY_LENGTH],
            {key: mask_mac(value) for key, value in fields.items()},
        )

        if header == BLOCK_PREAMBLE:
            self.state.protocol_version = fields.get("Version")
        elif header == BLOCK_END_PRELUDE:
            self.state.prelude_done = True
        else:
            if header == BLOCK_AUDIOMONITOR_DEVICE:
                self.state.device_type = DEVICE_TYPE_AUDIO_MONITOR
            target = self._block_target(header)
            if target is None:
                target = self._other_block(header)
            if target is not None:
                for key, value in fields.items():
                    # Existing keys are always updated; new keys only up to a limit
                    if key in target or len(target) < MAX_LINES_PER_BLOCK:
                        target[key] = value

        if not self._dump_complete.is_set() and self._initial_dump_received():
            self._dump_complete.set()
        if self._connected:
            self._notify()

    def _other_block(self, header: str) -> dict[str, str] | None:
        """Return storage for a block the integration does not use, within limits."""
        if header.startswith(BLOCK_MONITOR_PREFIX):
            _LOGGER.debug("Ignoring unsupported monitor block %r", header[:MAX_KEY_LENGTH])
            return None
        if len(header) > MAX_KEY_LENGTH:
            return None
        blocks = self.state.other_blocks
        if header in blocks:
            return blocks[header]
        if header in AUDIO_MONITOR_BLOCKS:
            blocks[header] = {}
            return blocks[header]
        unknown = sum(1 for name in blocks if name not in AUDIO_MONITOR_BLOCKS)
        if unknown >= MAX_OTHER_BLOCKS:
            _LOGGER.debug("Ignoring block %r: too many unknown blocks", header)
            return None
        blocks[header] = {}
        return blocks[header]

    def _initial_dump_received(self) -> bool:
        """Return True when the whole state dump has arrived.

        An Audio Monitor ends its dump with END PRELUDE; a SmartView does not,
        so for a SmartView the dump is complete with the device block and all
        monitor blocks.
        """
        if self.state.prelude_done:
            return True
        if not self.state.device or self.state.is_audio_monitor:
            return False
        count = self.state.monitor_count or 1
        return set(MONITOR_IDS[:count]).issubset(self.state.monitors)

    async def _supervise(self) -> None:
        """Keep the connection alive and reconnect with back-off."""
        delay = RECONNECT_MIN_DELAY
        while not self._closing:
            read_task = self._read_task
            if self._connected and read_task is not None:
                await asyncio.wait({read_task})
                if self._closing:
                    return
                _LOGGER.info("Lost connection to the device at %s", self.host)
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
                _LOGGER.info("Reconnected to the device at %s", self.host)
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
        self._cancel_flush()

        task = self._read_task
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._read_task = None

        await self._close_transport()
        self._reader = None
        self._writer = None
        self._reset_parser()

        if self._pending is not None and not self._pending.done():
            self._pending.set_exception(SmartViewConnectionError("Connection closed"))

        if was_connected:
            self._notify(immediate=True)

    def _notify(self, immediate: bool = False) -> None:
        """Tell listeners about a change, at most once per ``NOTIFY_INTERVAL``.

        Connection changes and acknowledged commands are passed on at once;
        changes pushed by the device are bundled, so a flood of updates from the
        device cannot flood Home Assistant.
        """
        if immediate:
            self._cancel_flush()
            self._fire_listeners()
            return
        if self._flush_handle is None:
            self._flush_handle = asyncio.get_running_loop().call_later(NOTIFY_INTERVAL, self._flush)

    def _flush(self) -> None:
        """Pass on bundled changes."""
        self._flush_handle = None
        self._fire_listeners()

    def _cancel_flush(self) -> None:
        """Drop a pending bundled notification."""
        if self._flush_handle is not None:
            self._flush_handle.cancel()
            self._flush_handle = None

    def _fire_listeners(self) -> None:
        """Call all listeners; an error in one never breaks the connection."""
        for listener in list(self._listeners):
            try:
                listener()
            except Exception:
                _LOGGER.exception("Error while updating a SmartView entity")
