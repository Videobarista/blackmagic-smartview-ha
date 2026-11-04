"""Sensors: IP address, hostname, model, protocol version, Audio Monitor delay and SDI level."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SmartViewConfigEntry
from .client import SmartViewState
from .const import (
    BLOCK_AUDIO_OUTPUT,
    BLOCK_ST2110,
    FIELD_AUDIO_DELAY_MS,
    FIELD_SDI_OUTPUT_LEVEL,
)
from .entity import SmartViewEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class SmartViewSensorDescription(SensorEntityDescription):
    """Describes a SmartView sensor."""

    value_fn: Callable[[SmartViewState], str | int | None]
    attrs_fn: Callable[[SmartViewState], dict[str, Any]] | None = None
    # Whether the device has this sensor (checked once, at setup)
    supported_fn: Callable[[SmartViewState], bool] = lambda state: True


def _network_attrs(state: SmartViewState) -> dict[str, Any]:
    net = state.network
    # SmartView says "netmask", the Audio Monitor "subnet"
    return {
        "netmask": net.get("Current netmask") or net.get("Current subnet"),
        "gateway": net.get("Current gateway"),
        "static_address": net.get("Static address"),
        "static_netmask": net.get("Static netmask") or net.get("Static subnet"),
        "static_gateway": net.get("Static gateway"),
    }


def _int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


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
        supported_fn=lambda state: not state.is_audio_monitor,
        # Contains the MAC address; off by default so it stays out of history
        entity_registry_enabled_default=False,
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
    SmartViewSensorDescription(
        key="audio_delay",
        translation_key="audio_delay",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MILLISECONDS,
        value_fn=lambda state: _int(state.block(BLOCK_AUDIO_OUTPUT).get(FIELD_AUDIO_DELAY_MS)),
        supported_fn=lambda state: FIELD_AUDIO_DELAY_MS in state.block(BLOCK_AUDIO_OUTPUT),
    ),
    SmartViewSensorDescription(
        key="sdi_output_level",
        translation_key="sdi_output_level",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state: state.block(BLOCK_ST2110).get(FIELD_SDI_OUTPUT_LEVEL),
        supported_fn=lambda state: FIELD_SDI_OUTPUT_LEVEL in state.block(BLOCK_ST2110),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SmartViewConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors."""
    state = entry.runtime_data.state
    async_add_entities(
        SmartViewSensor(entry, description)
        for description in SENSORS
        if description.supported_fn(state)
    )


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
    def native_value(self) -> str | int | None:
        """Return the value."""
        return self.entity_description.value_fn(self._client.state)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra attributes."""
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self._client.state)
