"""An in-process fake of the Blackmagic SmartView Ethernet Protocol.

It behaves like the SmartScope Duo 4K the integration was developed against:
a state dump on connect, ACK without echo for settings, ``# Error`` for
anything it cannot parse (such as ``PING:``), and hooks to refuse
connections, stay silent, drop connections or send oversized lines.
All addresses and identifiers are made up.
"""

from __future__ import annotations

import asyncio

DUMP_BLOCKS: dict[str, dict[str, str]] = {
    "PROTOCOL PREAMBLE": {"Version": "1.3"},
    "SMARTVIEW DEVICE": {
        "Model": "SmartScope Duo 4K",
        "Hostname": "SmartScopeDuo-7c2e0d000001",
        "Name": "SmartScope Duo",
        "Monitors": "2",
        "Inverted": "false",
    },
    "NETWORK": {
        "Dynamic IP": "true",
        "Static address": "192.0.2.2",
        "Static netmask": "255.255.255.0",
        "Static gateway": "192.0.2.1",
        "Current address": "192.0.2.10",
        "Current netmask": "255.255.255.0",
        "Current gateway": "192.0.2.1",
    },
    "MONITOR A": {
        "Brightness": "128",
        "Contrast": "128",
        "Saturation": "128",
        "Identify": "false",
        "Border": "None",
        "WidescreenSD": "on",
        "ScopeMode": "ParadeRGB",
        "AudioChannel": "0",
    },
    "MONITOR B": {
        "Brightness": "128",
        "Contrast": "128",
        "Saturation": "128",
        "Identify": "false",
        "Border": "None",
        "WidescreenSD": "on",
        "ScopeMode": "AudioDbfs",
        "AudioChannel": "0",
    },
}

SCOPE_MODES = {
    "Picture",
    "WaveformLuma",
    "Vector100",
    "Vector75",
    "ParadeRGB",
    "ParadeYUV",
    "Histogram",
    "AudioDbfs",
    "AudioDbvu",
}


def dump_text(blocks: dict[str, dict[str, str]] | None = None) -> str:
    """Return a state dump as the device sends it."""
    out = []
    for header, fields in (blocks or DUMP_BLOCKS).items():
        out.append(f"{header}:\n")
        out.extend(f"{key}: {value}\n" for key, value in fields.items())
        out.append("\n")
    return "".join(out)


class MockSmartView:
    """A local asyncio TCP server that behaves like a SmartScope Duo 4K."""

    def __init__(self) -> None:
        """Start with a normal, well-behaved device."""
        self.host = "127.0.0.1"
        self.port = 0
        self.refuse_connections = 0  # close this many new connections at once
        self.silent = False  # never answer commands
        self.oversized_line_once = False  # send one line over the client limit
        self.connection_count = 0
        self.received: list[tuple[str, dict[str, str]]] = []
        self._server: asyncio.Server | None = None
        self._writers: set[asyncio.StreamWriter] = set()
        self._tasks: set[asyncio.Task[None]] = set()

    async def start(self) -> None:
        """Listen on an ephemeral local port."""
        self._server = await asyncio.start_server(self._handle, self.host, 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        """Stop listening and close every connection."""
        if self._server is not None:
            self._server.close()
        await self.drop_all()
        if self._server is not None:
            await self._server.wait_closed()
            self._server = None

    async def drop_all(self) -> None:
        """Close all open connections, like a device restart."""
        for writer in list(self._writers):
            writer.close()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)
        self._writers.add(writer)
        self.connection_count += 1
        try:
            if self.refuse_connections > 0:
                self.refuse_connections -= 1
                return
            writer.write(dump_text().encode())
            if self.oversized_line_once:
                self.oversized_line_once = False
                writer.write(b"MONITOR A:\nBorder: " + b"x" * 20_000 + b"\n\n")
            await writer.drain()
            await self._serve(reader, writer)
        except (ConnectionError, OSError):
            pass
        finally:
            self._writers.discard(writer)
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass
            if task is not None:
                self._tasks.discard(task)

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        header: str | None = None
        fields: dict[str, str] = {}
        while True:
            raw = await reader.readline()
            if not raw:
                return
            line = raw.decode().rstrip("\r\n")
            if header is None:
                if line.endswith(":"):
                    header, fields = line[:-1], {}
                continue
            if line:
                key, _, value = line.partition(": ")
                fields[key] = value
                continue
            self.received.append((header, dict(fields)))
            if not self.silent:
                writer.write(self._answer(header, fields))
                await writer.drain()
            header = None

    def _answer(self, header: str, fields: dict[str, str]) -> bytes:
        if header.startswith("MONITOR "):
            if "ScopeMode" in fields and fields["ScopeMode"] not in SCOPE_MODES:
                return b"NAK\n\n"
            return b"ACK\n\n"
        if header == "SMARTVIEW DEVICE":
            return b"ACK\n\n"
        return b"# Error: 1.0: syntax error, unexpected identifier\n"
