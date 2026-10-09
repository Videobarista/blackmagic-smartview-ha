"""Text entity: the device name shown on the network."""

from __future__ import annotations

import logging

from homeassistant.components.text import TextEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SmartViewConfigEntry
from .client import SmartViewCommandError
from .entity import SmartViewEntity, async_send

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SmartViewConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the device name entity."""
    async_add_entities([SmartViewNameText(entry)])


class SmartViewNameText(SmartViewEntity, TextEntity):
    """Device name. Changing it makes the device restart its network.

    The connection drops for a moment and the integration reconnects by itself.
    """

    _attr_translation_key = "device_name"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min = 1
    _attr_native_max = 64

    def __init__(self, entry: SmartViewConfigEntry) -> None:
        """Initialise the text entity."""
        super().__init__(entry, "device_name")

    @property
    def native_value(self) -> str | None:
        """Return the device name."""
        return self._client.state.device.get("Name")

    async def async_set_value(self, value: str) -> None:
        """Rename the device."""
        fields = {"Name": value.strip()}
        try:
            await async_send(self, self._client.set_device(fields), fields)
        except HomeAssistantError as err:
            # A rename restarts the device's network, which can drop the
            # connection before the ACK arrives. That is expected, not a failure.
            if isinstance(err.__cause__, SmartViewCommandError):
                raise
            _LOGGER.debug("Connection dropped after rename, as expected: %s", err)
