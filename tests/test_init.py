"""Tests for setup, entities and diagnostics of the SmartView integration."""

from __future__ import annotations

import json

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.blackmagic_smartview.const import (
    CONF_CACHED_MODEL,
    CONF_CACHED_MONITORS,
    CONF_CACHED_NAME,
    DOMAIN,
)
from custom_components.blackmagic_smartview.diagnostics import (
    async_get_config_entry_diagnostics,
)
from homeassistant.components.diagnostics import REDACTED
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    CONF_HOST,
    CONF_PORT,
    EVENT_HOMEASSISTANT_STOP,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .conftest import HOST, PORT, FakeClient

ENTITY_COUNT = 26  # 9 per monitor (A and B) plus 8 for the device


def _entity_id(hass: HomeAssistant, domain: str, entry: MockConfigEntry, key: str) -> str:
    """Look an entity up by its unique ID, independent of its display name."""
    entity_id = er.async_get(hass).async_get_entity_id(domain, DOMAIN, f"{entry.entry_id}_{key}")
    assert entity_id is not None
    return entity_id


def _state(hass: HomeAssistant, domain: str, entry: MockConfigEntry, key: str) -> str:
    state = hass.states.get(_entity_id(hass, domain, entry, key))
    assert state is not None
    return state.state


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _call(hass: HomeAssistant, domain: str, service: str, entity_id: str, **data) -> None:
    await hass.services.async_call(domain, service, {"entity_id": entity_id, **data}, blocking=True)


# -------------------------------------------------------------------- setup


async def test_setup_while_monitor_is_on(
    hass: HomeAssistant, config_entry: MockConfigEntry, fake_client: type[FakeClient]
) -> None:
    """A reachable monitor shows its live state on every entity."""
    await _setup(hass, config_entry)

    assert config_entry.state is ConfigEntryState.LOADED
    registry = er.async_get(hass)
    assert len(er.async_entries_for_config_entry(registry, config_entry.entry_id)) == ENTITY_COUNT

    assert _state(hass, "binary_sensor", config_entry, "connection") == STATE_ON
    assert _state(hass, "select", config_entry, "monitor_a_scope_mode") == "parade_rgb"
    assert _state(hass, "select", config_entry, "monitor_b_scope_mode") == "audio_dbfs"
    assert _state(hass, "select", config_entry, "monitor_a_tally_color") == "none"
    assert _state(hass, "switch", config_entry, "monitor_a_tally") == STATE_OFF
    assert float(_state(hass, "number", config_entry, "monitor_b_brightness")) == 128
    assert _state(hass, "sensor", config_entry, "protocol_version") == "1.3"
    assert _state(hass, "text", config_entry, "device_name") == "SmartScope Duo"

    widescreen = hass.states.get(
        _entity_id(hass, "select", config_entry, "monitor_a_widescreen_sd")
    )
    assert widescreen is not None
    assert widescreen.state == "on"
    assert widescreen.attributes["options"] == ["on", "off"]

    device = dr.async_entries_for_config_entry(dr.async_get(hass), config_entry.entry_id)[0]
    assert device.name == "SmartScope Duo"
    assert device.model == "SmartScope Duo 4K"
    assert device.sw_version == "Protocol 1.3"


async def test_hostname_sensor_is_disabled_by_default(
    hass: HomeAssistant, config_entry: MockConfigEntry, fake_client: type[FakeClient]
) -> None:
    """The hostname contains the MAC address, so it stays out of history by default."""
    await _setup(hass, config_entry)
    entry = er.async_get(hass).async_get(_entity_id(hass, "sensor", config_entry, "hostname"))
    assert entry is not None
    assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION


async def test_identity_is_remembered_for_offline_starts(
    hass: HomeAssistant, fake_client: type[FakeClient]
) -> None:
    """An entry without cached identity gets it on the first successful start."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="SmartScope Duo",
        unique_id=HOST,
        data={CONF_HOST: HOST, CONF_PORT: PORT},
    )
    await _setup(hass, entry)

    assert entry.data[CONF_CACHED_MODEL] == "SmartScope Duo 4K"
    assert entry.data[CONF_CACHED_NAME] == "SmartScope Duo"
    assert entry.data[CONF_CACHED_MONITORS] == 2


async def test_setup_while_monitor_is_off(
    hass: HomeAssistant, config_entry: MockConfigEntry, fake_client: type[FakeClient]
) -> None:
    """A switched-off monitor sets up fine and shows as disconnected."""
    fake_client.online = False
    await _setup(hass, config_entry)

    assert config_entry.state is ConfigEntryState.LOADED
    registry = er.async_get(hass)
    assert len(er.async_entries_for_config_entry(registry, config_entry.entry_id)) == ENTITY_COUNT
    assert _state(hass, "binary_sensor", config_entry, "connection") == STATE_OFF
    assert _state(hass, "select", config_entry, "monitor_a_scope_mode") == STATE_UNAVAILABLE

    device = dr.async_entries_for_config_entry(dr.async_get(hass), config_entry.entry_id)[0]
    assert device.name == "SmartScope Duo"
    assert device.model == "SmartScope Duo 4K"


async def test_setup_retries_when_off_and_never_seen(
    hass: HomeAssistant, fake_client: type[FakeClient]
) -> None:
    """Without a remembered model there is nothing to build entities from yet."""
    fake_client.online = False
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="SmartScope Duo",
        unique_id=HOST,
        data={CONF_HOST: HOST, CONF_PORT: PORT},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_monitor_going_offline_and_back(
    hass: HomeAssistant, config_entry: MockConfigEntry, fake_client: type[FakeClient]
) -> None:
    """Losing the connection marks entities unavailable; the Connection sensor stays."""
    await _setup(hass, config_entry)
    client = fake_client.instances[0]

    client.go_offline()
    await hass.async_block_till_done()
    assert _state(hass, "binary_sensor", config_entry, "connection") == STATE_OFF
    assert _state(hass, "select", config_entry, "monitor_a_scope_mode") == STATE_UNAVAILABLE

    await client.connect()
    await hass.async_block_till_done()
    assert _state(hass, "binary_sensor", config_entry, "connection") == STATE_ON
    assert _state(hass, "select", config_entry, "monitor_a_scope_mode") == "parade_rgb"


async def test_unload_closes_connection(
    hass: HomeAssistant, config_entry: MockConfigEntry, fake_client: type[FakeClient]
) -> None:
    """Unloading closes the connection to the monitor."""
    await _setup(hass, config_entry)

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.NOT_LOADED
    assert fake_client.instances[0].closed


async def test_stopping_home_assistant_closes_connection(
    hass: HomeAssistant, config_entry: MockConfigEntry, fake_client: type[FakeClient]
) -> None:
    """The connection is closed when Home Assistant stops."""
    await _setup(hass, config_entry)

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()

    assert fake_client.instances[0].closed


# ------------------------------------------------------------------ actions


async def test_select_sends_scope_mode(
    hass: HomeAssistant, config_entry: MockConfigEntry, fake_client: type[FakeClient]
) -> None:
    """Choosing a scope sends the protocol value and shows it straight away."""
    await _setup(hass, config_entry)
    scope = _entity_id(hass, "select", config_entry, "monitor_a_scope_mode")

    await _call(hass, "select", "select_option", scope, option="waveform_luma")

    assert fake_client.instances[0].sent == ["MONITOR A:\nScopeMode: WaveformLuma\n\n"]
    assert _state(hass, "select", config_entry, "monitor_a_scope_mode") == "waveform_luma"


async def test_number_sends_brightness(
    hass: HomeAssistant, config_entry: MockConfigEntry, fake_client: type[FakeClient]
) -> None:
    """Brightness is sent as a whole number."""
    await _setup(hass, config_entry)
    brightness = _entity_id(hass, "number", config_entry, "monitor_a_brightness")

    await _call(hass, "number", "set_value", brightness, value=200)

    assert fake_client.instances[0].sent == ["MONITOR A:\nBrightness: 200\n\n"]
    assert float(_state(hass, "number", config_entry, "monitor_a_brightness")) == 200


async def test_tally_switch_remembers_colour(
    hass: HomeAssistant, config_entry: MockConfigEntry, fake_client: type[FakeClient]
) -> None:
    """The tally switch uses red by default, and the last colour after that."""
    await _setup(hass, config_entry)
    client = fake_client.instances[0]
    tally = _entity_id(hass, "switch", config_entry, "monitor_a_tally")
    colour = _entity_id(hass, "select", config_entry, "monitor_a_tally_color")

    await _call(hass, "switch", "turn_on", tally)
    assert client.sent[-1] == "MONITOR A:\nBorder: red\n\n"
    assert _state(hass, "switch", config_entry, "monitor_a_tally") == STATE_ON

    await _call(hass, "select", "select_option", colour, option="green")
    await _call(hass, "switch", "turn_off", tally)
    assert client.sent[-1] == "MONITOR A:\nBorder: none\n\n"
    assert _state(hass, "switch", config_entry, "monitor_a_tally") == STATE_OFF

    await _call(hass, "switch", "turn_on", tally)
    assert client.sent[-1] == "MONITOR A:\nBorder: green\n\n"


async def test_identify_button(
    hass: HomeAssistant, config_entry: MockConfigEntry, fake_client: type[FakeClient]
) -> None:
    """Identify shows the white border on the chosen monitor."""
    await _setup(hass, config_entry)
    identify = _entity_id(hass, "button", config_entry, "monitor_b_identify")

    await _call(hass, "button", "press", identify)

    assert fake_client.instances[0].sent == ["MONITOR B:\nIdentify: true\n\n"]


async def test_rename_and_cooldown(
    hass: HomeAssistant, config_entry: MockConfigEntry, fake_client: type[FakeClient]
) -> None:
    """A rename is sent once; a second rename waits for the network restart."""
    await _setup(hass, config_entry)
    name = _entity_id(hass, "text", config_entry, "device_name")

    await _call(hass, "text", "set_value", name, value="Studio Scope")
    assert fake_client.instances[0].sent == ["SMARTVIEW DEVICE:\nName: Studio Scope\n\n"]

    with pytest.raises(ServiceValidationError):
        await _call(hass, "text", "set_value", name, value="Other Scope")
    assert len(fake_client.instances[0].sent) == 1


async def test_rename_with_line_break_is_refused(
    hass: HomeAssistant, config_entry: MockConfigEntry, fake_client: type[FakeClient]
) -> None:
    """A name can never smuggle extra settings (such as a static IP) into the command."""
    await _setup(hass, config_entry)
    name = _entity_id(hass, "text", config_entry, "device_name")

    # Home Assistant refuses the value against the entity's pattern before the
    # integration sees it: a ValueError in current releases, a
    # ServiceValidationError (a HomeAssistantError) in newer ones.
    with pytest.raises((ValueError, HomeAssistantError)):
        await _call(hass, "text", "set_value", name, value="Studio\nDynamic IP: false")

    assert fake_client.instances[0].sent == []


@pytest.mark.parametrize("answer", [False, None])
async def test_failed_command_raises_and_keeps_state(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    fake_client: type[FakeClient],
    answer: bool | None,
) -> None:
    """A NAK or no answer surfaces as an error and the entity does not move."""
    await _setup(hass, config_entry)
    fake_client.answer = answer
    scope = _entity_id(hass, "select", config_entry, "monitor_a_scope_mode")

    with pytest.raises(HomeAssistantError):
        await _call(hass, "select", "select_option", scope, option="histogram")

    assert _state(hass, "select", config_entry, "monitor_a_scope_mode") == "parade_rgb"


# ------------------------------------------------------- data from the device


async def test_changes_pushed_by_the_device(
    hass: HomeAssistant, config_entry: MockConfigEntry, fake_client: type[FakeClient]
) -> None:
    """Changes made elsewhere (front panel, other software) are shown."""
    await _setup(hass, config_entry)

    fake_client.instances[0].push("MONITOR B:\nScopeMode: Histogram\nBorder: Green\n\n")
    await hass.async_block_till_done()

    assert _state(hass, "select", config_entry, "monitor_b_scope_mode") == "histogram"
    assert _state(hass, "select", config_entry, "monitor_b_tally_color") == "green"
    assert _state(hass, "switch", config_entry, "monitor_b_tally") == STATE_ON


@pytest.mark.parametrize(("pushed", "expected"), [("", STATE_UNKNOWN), ("N" * 5000, "N" * 64)])
async def test_odd_device_name_does_not_break_anything(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    fake_client: type[FakeClient],
    pushed: str,
    expected: str,
) -> None:
    """An empty or very long name from the device is shown safely."""
    await _setup(hass, config_entry)
    client = fake_client.instances[0]

    client.push(f"SMARTVIEW DEVICE:\nName: {pushed}\n\nMONITOR A:\nScopeMode: Vector75\n\n")
    await hass.async_block_till_done()

    assert _state(hass, "text", config_entry, "device_name") == expected
    assert _state(hass, "select", config_entry, "monitor_a_scope_mode") == "vector_75"
    assert client.connected


async def test_unknown_values_show_as_unknown(
    hass: HomeAssistant, config_entry: MockConfigEntry, fake_client: type[FakeClient]
) -> None:
    """A value the integration does not know is shown as unknown, not as an error."""
    await _setup(hass, config_entry)

    fake_client.instances[0].push("MONITOR A:\nScopeMode: FutureScope\nBrightness: bright\n\n")
    await hass.async_block_till_done()

    assert _state(hass, "select", config_entry, "monitor_a_scope_mode") == STATE_UNKNOWN
    assert _state(hass, "number", config_entry, "monitor_a_brightness") == STATE_UNKNOWN


# -------------------------------------------------------------- diagnostics


async def test_diagnostics_hide_addresses_and_mac(
    hass: HomeAssistant, config_entry: MockConfigEntry, fake_client: type[FakeClient]
) -> None:
    """Diagnostics can be shared publicly: no IP addresses, hostname or MAC address."""
    await _setup(hass, config_entry)

    result = await async_get_config_entry_diagnostics(hass, config_entry)

    assert result["connected"] is True
    assert result["entry"][CONF_HOST] == REDACTED
    assert result["state"]["device"]["Hostname"] == REDACTED
    assert result["state"]["device"]["Model"] == "SmartScope Duo 4K"
    assert result["state"]["network"]["Current address"] == REDACTED
    assert result["state"]["network"]["Static gateway"] == REDACTED
    text = json.dumps(result)
    assert "7c2e0d000001" not in text
    assert "192.0.2." not in text
