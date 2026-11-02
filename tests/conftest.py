"""Shared fixtures for the Blackmagic SmartView / SmartScope tests.

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
    DOMAIN,
)
from homeassistant.const import CONF_HOST, CONF_PORT

from .mock_smartview import dump_text

HOST = "192.0.2.10"
PORT = 9992

ENTRY_DATA: dict[str, Any] = {
    CONF_HOST: HOST,
    CONF_PORT: PORT,
    CONF_CACHED_MODEL: "SmartScope Duo 4K",
    CONF_CACHED_NAME: "SmartScope Duo",
    CONF_CACHED_MONITORS: 2,
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

    Parsing, validation, limits and the optimistic state update are the real
    code; only connecting and the socket are faked.
    """

    instances: list[FakeClient] = []
    online: bool = True
    # What the device answers to a command: True = ACK, False = NAK, None = nothing
    answer: bool | None = True

    def __init__(self, host: str, port: int) -> None:
        """Create the client and remember it."""
        super().__init__(host, port)
        self.sent: list[str] = []
        self.closed = False
        FakeClient.instances.append(self)

    async def connect(self) -> None:
        """Read the sample dump, or fail like a switched-off monitor."""
        if not FakeClient.online:
            raise SmartViewConnectionError(f"Cannot connect to {self.host}:{self.port}")
        self.state = SmartViewState()
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
        return FakeClient.answer

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
