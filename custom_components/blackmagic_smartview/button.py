"""Button entities: identify (15 s white border) per monitor."""

from __future__ import annotations

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SmartViewConfigEntry
from .const import FIELD_IDENTIFY
from .entity import SmartViewMonitorEntity, monitor_ids, monitor_supports

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SmartViewConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up identify buttons."""
    client = entry.runtime_data
    async_add_entities(
        SmartViewIdentifyButton(entry, monitor)
        for monitor in monitor_ids(client)
        if monitor_supports(client, monitor, FIELD_IDENTIFY)
    )


class SmartViewIdentifyButton(SmartViewMonitorEntity, ButtonEntity):
    """Show a white border around the picture for 15 seconds."""

    _attr_translation_key = "identify"
    _attr_device_class = ButtonDeviceClass.IDENTIFY
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: SmartViewConfigEntry, monitor: str) -> None:
        """Initialise the button."""
        super().__init__(entry, monitor, "identify")

    async def async_press(self) -> None:
        """Start identify on this monitor."""
        await self._async_set({FIELD_IDENTIFY: "true"})
