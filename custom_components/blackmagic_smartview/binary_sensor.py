"""Binary sensors: connection, DHCP and inverted mounting."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SmartViewConfigEntry
from .client import SmartViewClient
from .entity import SmartViewEntity

PARALLEL_UPDATES = 0


def _flag(value: str | None) -> bool | None:
    if value is None:
        return None
    return value.strip().lower() in ("true", "on", "yes", "1")


@dataclass(frozen=True, kw_only=True)
class SmartViewBinaryDescription(BinarySensorEntityDescription):
    """Describes a SmartView binary sensor."""

    value_fn: Callable[[SmartViewClient], bool | None]
    always_available: bool = False


BINARY_SENSORS: tuple[SmartViewBinaryDescription, ...] = (
    SmartViewBinaryDescription(
        key="connection",
        translation_key="connection",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda client: client.connected,
        always_available=True,
    ),
    SmartViewBinaryDescription(
        key="dhcp",
        translation_key="dhcp",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda client: _flag(client.state.network.get("Dynamic IP")),
    ),
    SmartViewBinaryDescription(
        key="inverted",
        translation_key="inverted",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda client: _flag(client.state.device.get("Inverted")),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SmartViewConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up binary sensors."""
    async_add_entities(SmartViewBinarySensor(entry, description) for description in BINARY_SENSORS)


class SmartViewBinarySensor(SmartViewEntity, BinarySensorEntity):
    """A boolean value from the device."""

    entity_description: SmartViewBinaryDescription

    def __init__(
        self, entry: SmartViewConfigEntry, description: SmartViewBinaryDescription
    ) -> None:
        """Initialise the binary sensor."""
        super().__init__(entry, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        """The connection sensor stays available to show the device is offline."""
        if self.entity_description.always_available:
            return True
        return super().available

    @property
    def is_on(self) -> bool | None:
        """Return the value."""
        return self.entity_description.value_fn(self._client)
