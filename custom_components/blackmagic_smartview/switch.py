"""Switch entities: tally border per monitor, mute on an Audio Monitor."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SmartViewConfigEntry
from .const import (
    BLOCK_AUDIO_OUTPUT,
    BORDER_COLORS,
    FIELD_BORDER,
    FIELD_MUTE,
    TALLY_DEFAULT_COLOR,
)
from .entity import AudioMonitorEntity, SmartViewMonitorEntity, monitor_ids, monitor_supports

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SmartViewConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up tally switches, or the mute switch of an Audio Monitor."""
    client = entry.runtime_data
    if client.state.is_audio_monitor:
        async_add_entities([AudioMonitorMuteSwitch(entry)])
        return
    async_add_entities(
        SmartViewTallySwitch(entry, monitor)
        for monitor in monitor_ids(client)
        if monitor_supports(client, monitor, FIELD_BORDER)
    )


class SmartViewTallySwitch(SmartViewMonitorEntity, SwitchEntity):
    """Soft tally border on/off.

    Turning on uses the last colour that was active on this monitor (red by
    default). The colour itself is chosen with the tally colour select.
    A hard-wired tally on the DB-9 port always overrides the soft tally; this
    switch shows the border that is actually visible.
    """

    _attr_translation_key = "tally"

    def __init__(self, entry: SmartViewConfigEntry, monitor: str) -> None:
        """Initialise the switch."""
        super().__init__(entry, monitor, "tally")
        self._last_color = TALLY_DEFAULT_COLOR
        self._remember_color()

    def _border(self) -> str | None:
        raw = self.monitor_state.get(FIELD_BORDER)
        return raw.strip().lower() if raw is not None else None

    def _remember_color(self) -> None:
        border = self._border()
        if border in BORDER_COLORS and border != "none":
            self._last_color = border

    @callback
    def _handle_client_update(self) -> None:
        self._remember_color()
        super()._handle_client_update()

    @property
    def is_on(self) -> bool | None:
        """Return True when a border colour is shown."""
        border = self._border()
        if border is None:
            return None
        return border != "none"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the colour used when switching on."""
        return {"color": self._border(), "on_color": self._last_color}

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Show the tally border."""
        await self._async_set({FIELD_BORDER: BORDER_COLORS[self._last_color]})

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Hide the tally border."""
        await self._async_set({FIELD_BORDER: BORDER_COLORS["none"]})


class AudioMonitorMuteSwitch(AudioMonitorEntity, SwitchEntity):
    """Mute of an Audio Monitor's speakers; follows the front-panel button too."""

    _attr_translation_key = "mute"

    def __init__(self, entry: SmartViewConfigEntry) -> None:
        """Initialise the switch."""
        super().__init__(entry, "mute", BLOCK_AUDIO_OUTPUT, FIELD_MUTE)

    @property
    def is_on(self) -> bool | None:
        """Return True when muted."""
        raw = self.reported
        if raw is None:
            return None
        return raw.strip().lower() == "true"

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Mute."""
        await self._async_send({FIELD_MUTE: "true"})

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Unmute."""
        await self._async_send({FIELD_MUTE: "false"})
