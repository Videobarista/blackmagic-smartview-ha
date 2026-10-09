"""The Blackmagic SmartView / SmartScope integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .client import SmartViewClient, SmartViewConnectionError
from .const import CONF_CACHED_MODEL, CONF_CACHED_MONITORS, CONF_CACHED_NAME

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TEXT,
]

type SmartViewConfigEntry = ConfigEntry[SmartViewClient]


async def async_setup_entry(hass: HomeAssistant, entry: SmartViewConfigEntry) -> bool:
    """Set up a SmartView / SmartScope from a config entry.

    When the monitor is switched off, setup still succeeds as long as the model
    is known from an earlier connection: the entities show as unavailable, the
    Connection sensor shows disconnected, and the client keeps retrying quietly.
    """
    client = SmartViewClient(entry.data[CONF_HOST], entry.data[CONF_PORT])
    try:
        await client.connect()
    except SmartViewConnectionError as err:
        if not entry.data.get(CONF_CACHED_MODEL):
            raise ConfigEntryNotReady(str(err)) from err
        _LOGGER.debug("Monitor offline at startup, starting with cached model: %s", err)
        client.preload(
            entry.data.get(CONF_CACHED_MODEL),
            entry.data.get(CONF_CACHED_NAME),
            entry.data.get(CONF_CACHED_MONITORS),
        )
    else:
        _async_update_cache(hass, entry, client)

    await client.start()
    entry.runtime_data = client

    async def _async_stop(_event: Event) -> None:
        await client.close()

    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_stop))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


def _async_update_cache(
    hass: HomeAssistant, entry: SmartViewConfigEntry, client: SmartViewClient
) -> None:
    """Remember model, name and monitor count for the next start while offline."""
    state = client.state
    cache = {
        CONF_CACHED_MODEL: state.model,
        CONF_CACHED_NAME: state.device.get("Name"),
        CONF_CACHED_MONITORS: state.monitor_count,
    }
    if any(entry.data.get(key) != value for key, value in cache.items()):
        hass.config_entries.async_update_entry(entry, data={**entry.data, **cache})


async def async_unload_entry(hass: HomeAssistant, entry: SmartViewConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.close()
    return unloaded
