"""Base entities for the Blackmagic SmartView / SmartScope integration."""

from __future__ import annotations

from collections.abc import Awaitable
import logging

from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from . import SmartViewConfigEntry
from .client import SmartViewClient, SmartViewCommandError, SmartViewError
from .const import COMMON_FIELDS, DEFAULT_NAME, DOMAIN, MANUFACTURER, MODEL_FIELDS

_LOGGER = logging.getLogger(__name__)


def monitor_ids(client: SmartViewClient) -> list[str]:
    """Return the monitor letters of the device (A, B, ...)."""
    if client.state.monitors:
        return sorted(client.state.monitors)
    count = client.state.monitor_count or 1
    return [chr(ord("A") + index) for index in range(count)]


def monitor_supports(client: SmartViewClient, monitor: str, field: str) -> bool:
    """Return True if a monitor supports a field.

    A field is supported when the device reports it in its state dump, when
    every model supports it, or when the model is known to support it.
    """
    if field in client.state.monitors.get(monitor, {}):
        return True
    if field in COMMON_FIELDS:
        return True
    model = client.state.model or ""
    return any(name in model and field in fields for name, fields in MODEL_FIELDS.items())


class SmartViewEntity(Entity):
    """Base class for all SmartView entities."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: SmartViewConfigEntry, key: str) -> None:
        """Initialise the entity."""
        self._client = entry.runtime_data
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        state = self._client.state
        model = state.model
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            manufacturer=MANUFACTURER,
            model=model,
            name=state.device.get("Name") or model or DEFAULT_NAME,
            sw_version=(f"Protocol {state.protocol_version}" if state.protocol_version else None),
        )

    @property
    def available(self) -> bool:
        """Return True when the device is connected."""
        return self._client.connected

    async def async_added_to_hass(self) -> None:
        """Subscribe to client updates."""
        self.async_on_remove(self._client.add_listener(self._handle_client_update))

    @callback
    def _handle_client_update(self) -> None:
        """Write the new state."""
        self.async_write_ha_state()


class SmartViewMonitorEntity(SmartViewEntity):
    """Base class for entities that belong to one monitor (A, B)."""

    def __init__(self, entry: SmartViewConfigEntry, monitor: str, key: str) -> None:
        """Initialise the entity."""
        super().__init__(entry, f"monitor_{monitor.lower()}_{key}")
        self._monitor = monitor
        self._attr_translation_placeholders = {"monitor": monitor}

    @property
    def monitor_state(self) -> dict[str, str]:
        """Return the reported fields of this monitor."""
        return self._client.state.monitors.get(self._monitor, {})

    async def _async_set(self, fields: dict[str, str]) -> None:
        """Send settings to this monitor."""
        await async_send(self, self._client.set_monitor(self._monitor, fields), fields)


async def async_send(entity: SmartViewEntity, command: Awaitable[None], fields: object) -> None:
    """Await a client command and turn failures into a readable error in Home Assistant."""
    try:
        await command
    except SmartViewCommandError as err:
        _LOGGER.debug("Rejected by %s: %s", entity._client.host, err)
        entity.async_write_ha_state()
        raise HomeAssistantError(
            f"The SmartView rejected this setting: {fields}. "
            "Your model or firmware may not support it."
        ) from err
    except SmartViewError as err:
        _LOGGER.debug("Command to %s failed: %s", entity._client.host, err)
        entity.async_write_ha_state()
        raise HomeAssistantError(
            f"The SmartView at {entity._client.host} did not respond."
        ) from err
