"""Shared fixtures for the Blackmagic SmartView, SmartScope and Audio Monitor tests.

All addresses are documentation addresses (RFC 5737) and all identifiers are
made up.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.blackmagic_smartview.client import (
    SmartViewClient,
    SmartViewConnectionError,
    SmartViewState,
)
from custom_components.blackmagic_smartview.const import (
    CONF_CACHED_MODEL,
    CONF_CACHED_MONITORS,
    CONF_CACHED_NAME,
    CONF_DEVICE_TYPE,
    DOMAIN,
)
from homeassistant.const import CONF_HOST, CONF_PORT

from .mock_smartview import (
    AUDIO_MONITOR_UNIQUE_ID,
    AudioMonitorEmulator,
    audio_monitor_dump_text,
    dump_text,
    parse_block,
)

HOST = "192.0.2.10"
PORT = 9992

ENTRY_DATA: dict[str, Any] = {
    CONF_HOST: HOST,
    CONF_PORT: PORT,
    CONF_CACHED_MODEL: "SmartScope Duo 4K",
    CONF_CACHED_NAME: "SmartScope Duo",
    CONF_CACHED_MONITORS: 2,
    CONF_DEVICE_TYPE: "smartview",
}

AUDIO_HOST = "192.0.2.30"
AUDIO_PORT = 9996
AUDIO_UNIQUE_ID = AUDIO_MONITOR_UNIQUE_ID.lower()

AUDIO_ENTRY_DATA: dict[str, Any] = {
    CONF_HOST: AUDIO_HOST,
    CONF_PORT: AUDIO_PORT,
    CONF_CACHED_MODEL: "Blackmagic Audio Monitor 12G",
    CONF_CACHED_NAME: "Studio Audio Monitor",
    CONF_CACHED_MONITORS: None,
    CONF_DEVICE_TYPE: "audio_monitor",
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let Home Assistant load the integration from custom_components."""


def parsed_state(text: str | None = None) -> SmartViewState:
    """Return the state the real parser makes of a dump."""
    client = SmartViewClient(HOST, PORT)
    client.feed(text or dump_text())
    return client.state


class FakeClient(SmartViewClient):
    """The real client with the network replaced.

    Parsing, validation, limits and state handling are the real code; only
    connecting and the socket are faked. With ``kind = "audio_monitor"`` the
    fake answers like an Audio Monitor, including the blocks it sends back.
    """

    instances: list[FakeClient] = []
    online: bool = True
    kind: str = "smartview"
    # What the device answers to a command: True = ACK, False = NAK, None = nothing
    answer: bool | None = True

    def __init__(self, host: str, port: int) -> None:
        """Create the client and remember it."""
        super().__init__(host, port)
        self.sent: list[str] = []
        self.closed = False
        self.audio = AudioMonitorEmulator()
        FakeClient.instances.append(self)

    async def connect(self) -> None:
        """Read the sample dump, or fail like a switched-off monitor."""
        if not FakeClient.online:
            raise SmartViewConnectionError(f"Cannot connect to {self.host}:{self.port}")
        self.state = SmartViewState()
        if FakeClient.kind == "audio_monitor":
            self.feed(audio_monitor_dump_text(self.audio.blocks))
        else:
            self.feed(dump_text())
        self._connected = True
        self._notify(immediate=True)

    async def start(self) -> None:
        """No background reconnects in tests."""

    async def close(self) -> None:
        """Record the close, as the real client stops everything here."""
        self.closed = True
        self._cancel_flush()
        self._connected = False

    async def _send_raw(self, payload: str) -> bool | None:
        """Record what would go over the wire and answer like the device."""
        self.sent.append(payload)
        if FakeClient.answer is not True or not self.state.is_audio_monitor:
            return FakeClient.answer
        acked, after = self.audio.handle(*parse_block(payload))
        if after:
            self.push(after)
        return acked

    def push(self, text: str) -> None:
        """Let the device push blocks and pass them on at once."""
        self.feed(text)
        self._cancel_flush()
        self._fire_listeners()

    def go_offline(self) -> None:
        """Lose the connection, as when the monitor is switched off."""
        self._connected = False
        self._fire_listeners()


@pytest.fixture
def fake_client() -> Generator[type[FakeClient]]:
    """Replace the protocol client used by the integration."""
    FakeClient.instances = []
    FakeClient.online = True
    FakeClient.answer = True
    FakeClient.kind = "smartview"
    with patch("custom_components.blackmagic_smartview.SmartViewClient", FakeClient):
        yield FakeClient


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Return a config entry as the config flow creates it."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="SmartScope Duo",
        unique_id=HOST,
        data=dict(ENTRY_DATA),
    )


@pytest.fixture
def audio_entry() -> MockConfigEntry:
    """Return an Audio Monitor config entry as the config flow creates it."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Studio Audio Monitor",
        unique_id=AUDIO_UNIQUE_ID,
        data=dict(AUDIO_ENTRY_DATA),
    )
