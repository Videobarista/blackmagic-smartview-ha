"""Tests for the Blackmagic SmartView / SmartScope config flow."""

from __future__ import annotations

from collections.abc import Generator
from ipaddress import ip_address
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.blackmagic_smartview.client import (
    SmartViewClient,
    SmartViewConnectionError,
)
from custom_components.blackmagic_smartview.const import (
    CONF_CACHED_MODEL,
    CONF_CACHED_NAME,
    DOMAIN,
)
from homeassistant.config_entries import SOURCE_USER, SOURCE_ZEROCONF
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .conftest import (
    AUDIO_ENTRY_DATA,
    AUDIO_HOST,
    AUDIO_PORT,
    AUDIO_UNIQUE_ID,
    ENTRY_DATA,
    HOST,
    PORT,
    FakeClient,
    parsed_state,
)
from .mock_smartview import (
    AUDIO_MONITOR_BLOCKS,
    DUMP_BLOCKS,
    audio_monitor_dump_text,
    dump_text,
)

OTHER_HOST = "192.0.2.20"

DISCOVERY = ZeroconfServiceInfo(
    ip_address=ip_address(HOST),
    ip_addresses=[ip_address(HOST)],
    hostname="SmartScopeDuo.local.",
    name="SmartScope Duo._blackmagic._tcp.local.",
    port=PORT,
    type="_blackmagic._tcp.local.",
    properties={"class": "SmartView"},
)


@pytest.fixture(autouse=True)
def probe() -> Generator[AsyncMock]:
    """Replace the one-shot connection the config flow makes."""
    with patch.object(
        SmartViewClient, "probe", new=AsyncMock(return_value=parsed_state())
    ) as mock_probe:
        yield mock_probe


@pytest.fixture(autouse=True)
def no_setup() -> Generator[AsyncMock]:
    """Keep the flow tests from setting up the integration itself."""
    with patch(
        "custom_components.blackmagic_smartview.async_setup_entry", return_value=True
    ) as mock_setup:
        yield mock_setup


async def _start_user_flow(hass: HomeAssistant, host: str, port: int | None = PORT):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    user_input = {CONF_HOST: host} if port is None else {CONF_HOST: host, CONF_PORT: port}
    return await hass.config_entries.flow.async_configure(result["flow_id"], user_input)


def _audio_state(unique_id: str | None = None):
    """Return a parsed Audio Monitor dump, optionally with another Unique ID."""
    blocks = {header: dict(fields) for header, fields in AUDIO_MONITOR_BLOCKS.items()}
    if unique_id is not None:
        blocks["AUDIOMONITOR DEVICE"]["Unique ID"] = unique_id
    return parsed_state(audio_monitor_dump_text(blocks))


def _audio_monitor_only(state):
    """A probe that finds only an Audio Monitor (port 9996), like the real device."""

    async def probe(host: str, port: int):
        if port != AUDIO_PORT:
            raise SmartViewConnectionError(f"Connection refused on {port}")
        return state

    return probe


async def test_user_flow_creates_entry(hass: HomeAssistant, probe: AsyncMock) -> None:
    """A reachable monitor is added, with its identity stored for offline starts."""
    result = await _start_user_flow(hass, f" {HOST} ")

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "SmartScope Duo"
    assert result["data"] == ENTRY_DATA
    assert result["result"].unique_id == HOST
    probe.assert_awaited_once_with(HOST, PORT)


async def test_user_flow_accepts_hostname(hass: HomeAssistant, probe: AsyncMock) -> None:
    """A plain hostname is fine too."""
    result = await _start_user_flow(hass, "smartscope.studio.lan")
    assert result["type"] is FlowResultType.CREATE_ENTRY
    probe.assert_awaited_once_with("smartscope.studio.lan", PORT)


@pytest.mark.parametrize(
    "host",
    [
        "",
        "http://192.0.2.10",
        "192.0.2.10:9992",
        "192.0.2.10/path",
        "bad host",
        "999.1.1.1",
        "-smartscope.lan",
        "a" * 254,
    ],
)
async def test_user_flow_rejects_invalid_host(
    hass: HomeAssistant, probe: AsyncMock, host: str
) -> None:
    """Anything but an IP address or plain hostname is refused before connecting."""
    result = await _start_user_flow(hass, host)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_HOST: "invalid_host"}
    probe.assert_not_awaited()


async def test_user_flow_cannot_connect_then_recovers(
    hass: HomeAssistant, probe: AsyncMock
) -> None:
    """An unreachable monitor shows an error and the user can try again."""
    probe.side_effect = SmartViewConnectionError("unreachable")
    result = await _start_user_flow(hass, HOST)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}

    probe.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: HOST, CONF_PORT: PORT}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_already_configured(
    hass: HomeAssistant, config_entry: MockConfigEntry, probe: AsyncMock
) -> None:
    """The same address cannot be added twice."""
    config_entry.add_to_hass(hass)
    result = await _start_user_flow(hass, HOST)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    probe.assert_not_awaited()


async def test_user_flow_without_name_uses_model(hass: HomeAssistant, probe: AsyncMock) -> None:
    """A monitor without a name is titled after its model."""
    blocks = {header: dict(fields) for header, fields in DUMP_BLOCKS.items()}
    del blocks["SMARTVIEW DEVICE"]["Name"]
    probe.return_value = parsed_state(dump_text(blocks))

    result = await _start_user_flow(hass, HOST)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "SmartScope Duo 4K"
    assert result["data"][CONF_CACHED_NAME] is None


async def test_zeroconf_flow_creates_entry(hass: HomeAssistant, probe: AsyncMock) -> None:
    """A discovered monitor is confirmed and added on the SmartView port."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=DISCOVERY
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "zeroconf_confirm"
    assert result["description_placeholders"] == {
        "name": "SmartScope Duo",
        "model": "SmartScope Duo 4K",
        "host": HOST,
    }

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == ENTRY_DATA
    assert result["result"].unique_id == HOST
    probe.assert_awaited_once_with(HOST, PORT)


async def test_zeroconf_unreachable_aborts(hass: HomeAssistant, probe: AsyncMock) -> None:
    """A discovered device that does not answer on the SmartView port is skipped."""
    probe.side_effect = SmartViewConnectionError("unreachable")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=DISCOVERY
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


async def test_zeroconf_already_configured_aborts(
    hass: HomeAssistant, config_entry: MockConfigEntry, probe: AsyncMock
) -> None:
    """A monitor that is already set up is not offered again."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=DISCOVERY
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    probe.assert_not_awaited()


async def test_reconfigure_changes_address(
    hass: HomeAssistant, config_entry: MockConfigEntry, probe: AsyncMock
) -> None:
    """A new address is stored once the monitor answers on it."""
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: OTHER_HOST, CONF_PORT: PORT}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert config_entry.data[CONF_HOST] == OTHER_HOST
    assert config_entry.data[CONF_CACHED_MODEL] == "SmartScope Duo 4K"
    assert config_entry.unique_id == OTHER_HOST
    probe.assert_awaited_once_with(OTHER_HOST, PORT)


async def test_reconfigure_cannot_connect_keeps_old_address(
    hass: HomeAssistant, config_entry: MockConfigEntry, probe: AsyncMock
) -> None:
    """An unreachable address never replaces a working one."""
    config_entry.add_to_hass(hass)
    probe.side_effect = SmartViewConnectionError("unreachable")
    result = await config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: OTHER_HOST, CONF_PORT: PORT}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    assert config_entry.data[CONF_HOST] == HOST


async def test_reconfigure_rejects_invalid_host(
    hass: HomeAssistant, config_entry: MockConfigEntry, probe: AsyncMock
) -> None:
    """A malformed address is refused before connecting."""
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "http://192.0.2.20", CONF_PORT: PORT}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_HOST: "invalid_host"}
    probe.assert_not_awaited()


async def test_reconfigure_rejects_address_of_other_monitor(
    hass: HomeAssistant, config_entry: MockConfigEntry, probe: AsyncMock
) -> None:
    """Two entries can never point at the same monitor."""
    config_entry.add_to_hass(hass)
    MockConfigEntry(
        domain=DOMAIN,
        title="Other SmartView",
        unique_id=OTHER_HOST,
        data={**ENTRY_DATA, CONF_HOST: OTHER_HOST},
    ).add_to_hass(hass)

    result = await config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: OTHER_HOST, CONF_PORT: PORT}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "already_configured"}
    assert config_entry.data[CONF_HOST] == HOST
    probe.assert_not_awaited()


async def test_reconfigure_same_address_reuses_running_connection(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    probe: AsyncMock,
    fake_client: type[FakeClient],
) -> None:
    """The monitor has few connection slots, so no second connection is opened."""
    config_entry.add_to_hass(hass)
    running = fake_client(HOST, PORT)
    await running.connect()
    config_entry.runtime_data = running

    result = await config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: HOST, CONF_PORT: PORT}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert config_entry.data == ENTRY_DATA
    probe.assert_not_awaited()


# ------------------------------------------------------------- Audio Monitor


async def test_user_flow_detects_audio_monitor(hass: HomeAssistant, probe: AsyncMock) -> None:
    """With the port left empty, 9992 is tried first and the Audio Monitor found on 9996."""
    probe.side_effect = _audio_monitor_only(_audio_state())

    result = await _start_user_flow(hass, AUDIO_HOST, port=None)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Studio Audio Monitor"
    assert result["data"] == AUDIO_ENTRY_DATA
    assert result["result"].unique_id == AUDIO_UNIQUE_ID
    assert [call.args for call in probe.await_args_list] == [
        (AUDIO_HOST, PORT),
        (AUDIO_HOST, AUDIO_PORT),
    ]


async def test_user_flow_detection_finds_nothing(hass: HomeAssistant, probe: AsyncMock) -> None:
    """When neither port answers, the user gets the connection error."""
    probe.side_effect = SmartViewConnectionError("unreachable")

    result = await _start_user_flow(hass, AUDIO_HOST, port=None)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    assert probe.await_count == 2


async def test_audio_monitor_at_new_address_updates_entry(
    hass: HomeAssistant, audio_entry: MockConfigEntry, probe: AsyncMock
) -> None:
    """Adding a known Audio Monitor at a new IP address updates the existing entry."""
    audio_entry.add_to_hass(hass)
    probe.return_value = _audio_state()

    result = await _start_user_flow(hass, "192.0.2.31", port=AUDIO_PORT)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert audio_entry.data[CONF_HOST] == "192.0.2.31"
    assert audio_entry.data[CONF_PORT] == AUDIO_PORT


async def test_reconfigure_audio_monitor_to_new_address(
    hass: HomeAssistant, audio_entry: MockConfigEntry, probe: AsyncMock
) -> None:
    """The same Audio Monitor at a new address is accepted; its unique ID stays."""
    audio_entry.add_to_hass(hass)
    probe.return_value = _audio_state()

    result = await audio_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "192.0.2.31", CONF_PORT: AUDIO_PORT}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert audio_entry.data[CONF_HOST] == "192.0.2.31"
    assert audio_entry.unique_id == AUDIO_UNIQUE_ID


async def test_reconfigure_audio_monitor_rejects_other_device(
    hass: HomeAssistant, audio_entry: MockConfigEntry, probe: AsyncMock
) -> None:
    """An address that answers with another Unique ID is a different device."""
    audio_entry.add_to_hass(hass)
    probe.return_value = _audio_state(unique_id="7C2E0D0000FF")

    result = await audio_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "192.0.2.31", CONF_PORT: AUDIO_PORT}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "wrong_device"}
    assert audio_entry.data[CONF_HOST] == AUDIO_HOST


async def test_reconfigure_smartview_to_audio_monitor_is_refused(
    hass: HomeAssistant, config_entry: MockConfigEntry, probe: AsyncMock
) -> None:
    """A SmartView entry cannot be pointed at an Audio Monitor."""
    config_entry.add_to_hass(hass)
    probe.side_effect = _audio_monitor_only(_audio_state())

    result = await config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "192.0.2.31"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "wrong_device"}
    assert config_entry.data[CONF_HOST] == HOST
