"""Number entities: monitor picture settings and Audio Monitor volumes."""

from __future__ import annotations

from dataclasses import dataclass
import logging

from homeassistant.components.number import NumberEntity, NumberEntityDescription, NumberMode
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SmartViewConfigEntry
from .const import (
    BLOCK_AUDIO_OUTPUT,
    FIELD_BRIGHTNESS,
    FIELD_CONTRAST,
    FIELD_GAIN,
    FIELD_SATURATION,
    GAIN_HEADPHONE,
    GAIN_SPEAKER,
)
from .entity import AudioMonitorEntity, SmartViewMonitorEntity, monitor_ids, monitor_supports

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class SmartViewNumberDescription(NumberEntityDescription):
    """Describes a SmartView number entity."""

    field: str


NUMBERS: tuple[SmartViewNumberDescription, ...] = (
    SmartViewNumberDescription(
        key="brightness",
        translation_key="brightness",
        field=FIELD_BRIGHTNESS,
    ),
    SmartViewNumberDescription(
        key="contrast",
        translation_key="contrast",
        field=FIELD_CONTRAST,
    ),
    SmartViewNumberDescription(
        key="saturation",
        translation_key="saturation",
        field=FIELD_SATURATION,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SmartViewConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up number entities."""
    client = entry.runtime_data
    if client.state.is_audio_monitor:
        async_add_entities(
            [
                AudioMonitorVolume(entry, "speaker_volume", GAIN_SPEAKER),
                AudioMonitorVolume(entry, "headphone_volume", GAIN_HEADPHONE),
            ]
        )
        return
    async_add_entities(
        SmartViewNumber(entry, monitor, description)
        for monitor in monitor_ids(client)
        for description in NUMBERS
        if monitor_supports(client, monitor, description.field)
    )


class SmartViewNumber(SmartViewMonitorEntity, NumberEntity):
    """A 0-255 picture setting (127 is neutral for contrast and saturation)."""

    entity_description: SmartViewNumberDescription
    _attr_native_min_value = 0
    _attr_native_max_value = 255
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    def __init__(
        self,
        entry: SmartViewConfigEntry,
        monitor: str,
        description: SmartViewNumberDescription,
    ) -> None:
        """Initialise the number."""
        super().__init__(entry, monitor, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | None:
        """Return the current value."""
        raw = self.monitor_state.get(self.entity_description.field)
        if raw is None:
            return None
        try:
            return int(raw)
        except ValueError:
            _LOGGER.debug(
                "Monitor %s: unexpected %s value %r",
                self._monitor,
                self.entity_description.field,
                raw,
            )
            return None

    async def async_set_native_value(self, value: float) -> None:
        """Send the new value to the monitor."""
        await self._async_set({self.entity_description.field: str(round(value))})


class AudioMonitorVolume(AudioMonitorEntity, NumberEntity):
    """Speaker or headphone volume of an Audio Monitor (0-255, as the device uses)."""

    _attr_native_min_value = 0
    _attr_native_max_value = 255
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    def __init__(self, entry: SmartViewConfigEntry, key: str, output: str) -> None:
        """Initialise the volume."""
        super().__init__(entry, key, BLOCK_AUDIO_OUTPUT, f"{FIELD_GAIN} {output}")
        self._attr_translation_key = key
        self._output = output
        if output == GAIN_HEADPHONE:
            # Seen on an Audio Monitor 12G (protocol 1.0) without headphones
            # connected: a headphone volume command changed the speaker volume.
            # Off by default, so nobody is surprised by loud speakers.
            self._attr_entity_registry_enabled_default = False

    @property
    def native_value(self) -> float | None:
        """Return the reported volume."""
        raw = self.reported
        if raw is None:
            return None
        try:
            return int(raw)
        except ValueError:
            _LOGGER.debug("Unexpected %s value %r", self._field, raw)
            return None

    async def async_set_native_value(self, value: float) -> None:
        """Send the new volume, e.g. ``Gain: Speaker Stereo 120``."""
        await self._async_send({FIELD_GAIN: f"{self._output} {round(value)}"})
