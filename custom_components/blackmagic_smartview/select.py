"""Select entities.

SmartView / SmartScope: scope mode, audio channels, tally colour, widescreen SD,
LUT, input. Audio Monitor: input source, meter mode, solo.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SmartViewConfigEntry
from .const import (
    AUDIO_CHANNELS,
    AUDIO_SOURCES,
    AUDIO_SOURCES_ST2110,
    BLOCK_AUDIO_INPUT,
    BLOCK_AUDIO_METER,
    BLOCK_AUDIO_OUTPUT,
    BLOCK_ST2110,
    BORDER_COLORS,
    FIELD_AUDIO_CHANNEL,
    FIELD_BORDER,
    FIELD_LUT,
    FIELD_METER_MODE,
    FIELD_MONITOR_INPUT,
    FIELD_ROUTING,
    FIELD_SCOPE_MODE,
    FIELD_SOLO,
    FIELD_WIDESCREEN_SD,
    LUTS,
    METER_MODES,
    MONITOR_INPUTS,
    ROUTING_DESTINATION,
    SCOPE_MODES,
    SOLO_MODES,
    WIDESCREEN_SD,
)
from .entity import AudioMonitorEntity, SmartViewMonitorEntity, monitor_ids, monitor_supports

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class SmartViewSelectDescription(SelectEntityDescription):
    """Describes a SmartView select entity."""

    field: str
    values: dict[str, str]
    # Options that are only offered when the device itself reports them
    report_only: frozenset[str] = frozenset()


SELECTS: tuple[SmartViewSelectDescription, ...] = (
    SmartViewSelectDescription(
        key="scope_mode",
        translation_key="scope_mode",
        field=FIELD_SCOPE_MODE,
        values=SCOPE_MODES,
    ),
    SmartViewSelectDescription(
        key="audio_channels",
        translation_key="audio_channels",
        field=FIELD_AUDIO_CHANNEL,
        values=AUDIO_CHANNELS,
    ),
    SmartViewSelectDescription(
        key="tally_color",
        translation_key="tally_color",
        field=FIELD_BORDER,
        values=BORDER_COLORS,
    ),
    SmartViewSelectDescription(
        key="widescreen_sd",
        translation_key="widescreen_sd",
        field=FIELD_WIDESCREEN_SD,
        values=WIDESCREEN_SD,
        report_only=frozenset({"auto"}),
        entity_category=EntityCategory.CONFIG,
    ),
    SmartViewSelectDescription(
        key="lut",
        translation_key="lut",
        field=FIELD_LUT,
        values=LUTS,
    ),
    SmartViewSelectDescription(
        key="monitor_input",
        translation_key="monitor_input",
        field=FIELD_MONITOR_INPUT,
        values=MONITOR_INPUTS,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SmartViewConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up select entities."""
    client = entry.runtime_data
    if client.state.is_audio_monitor:
        async_add_entities(
            [
                AudioMonitorInputSelect(entry),
                AudioMonitorSelect(
                    entry, "meter_mode", BLOCK_AUDIO_METER, FIELD_METER_MODE, METER_MODES
                ),
                AudioMonitorSelect(entry, "solo", BLOCK_AUDIO_OUTPUT, FIELD_SOLO, SOLO_MODES),
            ]
        )
        return
    async_add_entities(
        SmartViewSelect(entry, monitor, description)
        for monitor in monitor_ids(client)
        for description in SELECTS
        if monitor_supports(client, monitor, description.field)
    )


class SmartViewSelect(SmartViewMonitorEntity, SelectEntity):
    """A monitor setting with a fixed set of values."""

    entity_description: SmartViewSelectDescription

    def __init__(
        self,
        entry: SmartViewConfigEntry,
        monitor: str,
        description: SmartViewSelectDescription,
    ) -> None:
        """Initialise the select."""
        super().__init__(entry, monitor, description.key)
        self.entity_description = description

    def _option_for(self, raw: str | None) -> str | None:
        """Map a protocol value to an option key (case-insensitive)."""
        if raw is None:
            return None
        wanted = raw.strip().lower()
        for option, value in self.entity_description.values.items():
            if value.lower() == wanted:
                return option
        _LOGGER.debug(
            "Monitor %s: unknown %s value %r", self._monitor, self.entity_description.field, raw
        )
        return None

    @property
    def current_option(self) -> str | None:
        """Return the selected option."""
        return self._option_for(self.monitor_state.get(self.entity_description.field))

    @property
    def options(self) -> list[str]:
        """Return the available options."""
        current = self.current_option
        return [
            option
            for option in self.entity_description.values
            if option not in self.entity_description.report_only or option == current
        ]

    async def async_select_option(self, option: str) -> None:
        """Send the selected value to the monitor."""
        await self._async_set(
            {self.entity_description.field: self.entity_description.values[option]}
        )


def _option_for(values: dict[str, str], raw: str | None) -> str | None:
    """Map a protocol value to an option key (case-insensitive)."""
    if raw is None:
        return None
    wanted = raw.strip().lower()
    for option, value in values.items():
        if value.lower() == wanted:
            return option
    _LOGGER.debug("Unknown value %r", raw)
    return None


class AudioMonitorSelect(AudioMonitorEntity, SelectEntity):
    """An Audio Monitor setting with a fixed set of values."""

    def __init__(
        self,
        entry: SmartViewConfigEntry,
        key: str,
        block: str,
        field: str,
        values: dict[str, str],
    ) -> None:
        """Initialise the select."""
        super().__init__(entry, key, block, field)
        self._attr_translation_key = key
        self._values = values

    @property
    def values(self) -> dict[str, str]:
        """Return option key -> protocol value."""
        return self._values

    @property
    def current_option(self) -> str | None:
        """Return the selected option."""
        return _option_for(self.values, self.reported)

    @property
    def options(self) -> list[str]:
        """Return the available options."""
        return list(self.values)

    async def async_select_option(self, option: str) -> None:
        """Send the selected value."""
        await self._async_send({self._field: self.values[option]})


class AudioMonitorInputSelect(AudioMonitorSelect):
    """Input source for the speakers and headphones.

    The protocol line is ``Routing: Speaker Stereo <source>``. ST 2110 sources
    are offered only by devices that report an ST2110 block.
    """

    def __init__(self, entry: SmartViewConfigEntry) -> None:
        """Initialise the select."""
        super().__init__(entry, "audio_input", BLOCK_AUDIO_INPUT, FIELD_ROUTING, AUDIO_SOURCES)

    @property
    def values(self) -> dict[str, str]:
        """Return the sources this device offers."""
        if self._client.state.block(BLOCK_ST2110):
            return {**AUDIO_SOURCES, **AUDIO_SOURCES_ST2110}
        return AUDIO_SOURCES

    @property
    def current_option(self) -> str | None:
        """Return the selected source."""
        raw = self.reported
        if raw is None:
            return None
        source = raw.strip()
        if source.lower().startswith(ROUTING_DESTINATION.lower()):
            source = source[len(ROUTING_DESTINATION) :].strip()
        return _option_for(self.values, source)

    async def async_select_option(self, option: str) -> None:
        """Route the source to the speakers."""
        await self._async_send({FIELD_ROUTING: f"{ROUTING_DESTINATION} {self.values[option]}"})
