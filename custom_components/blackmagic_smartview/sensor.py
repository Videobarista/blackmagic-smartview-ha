"""Diagnostic sensors: IP address, hostname and protocol version."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SmartViewConfigEntry
from .client import SmartViewState
from .entity import SmartViewEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class SmartViewSensorDescription(SensorEntityDescription):
    """Describes a SmartView sensor."""

    value_fn: Callable[[SmartViewState], str | None]
    attrs_fn: Callable[[SmartViewState], dict[str, Any]] | None = None


def _network_attrs(state: SmartViewState) -> dict[str, Any]:
    net = state.network
    return {
        "netmask": net.get("Current netmask"),
        "gateway": net.get("Current gateway"),
        "static_address": net.get("Static address"),
        "static_netmask": net.get("Static netmask"),
        "static_gateway": net.get("Static gateway"),
    }


SENSORS: tuple[SmartViewSensorDescription, ...] = (
    SmartViewSensorDescription(
        key="ip_address",
        translation_key="ip_address",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state: state.network.get("Current address"),
        attrs_fn=_network_attrs,
    ),
    SmartViewSensorDescription(
        key="hostname",
        translation_key="hostname",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state: state.device.get("Hostname"),
    ),
    SmartViewSensorDescription(
        key="protocol_version",
        translation_key="protocol_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state: state.protocol_version,
    ),
    SmartViewSensorDescription(
        key="model",
        translation_key="model",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state: state.model,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SmartViewConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors."""
    async_add_entities(SmartViewSensor(entry, description) for description in SENSORS)


class SmartViewSensor(SmartViewEntity, SensorEntity):
    """A read-only value from the device or network block."""

    entity_description: SmartViewSensorDescription

    def __init__(
        self, entry: SmartViewConfigEntry, description: SmartViewSensorDescription
    ) -> None:
        """Initialise the sensor."""
        super().__init__(entry, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> str | None:
        """Return the value."""
        return self.entity_description.value_fn(self._client.state)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra attributes."""
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self._client.state)
