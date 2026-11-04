"""Text entity: the device name shown on the network."""

from __future__ import annotations

import logging
import time

from homeassistant.components.text import TextEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SmartViewConfigEntry
from .client import SmartViewCommandError, SmartViewValueError
from .const import NAME_MAX_LENGTH, NAME_PATTERN, RENAME_COOLDOWN
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
    """Device name (``Name`` on a SmartView, ``Label`` on an Audio Monitor).

    Renaming a SmartView makes it restart its network: the connection drops for
    a moment and the integration reconnects by itself, and a new rename is
    refused while it restarts. Only printable characters are accepted, so a
    name can never add lines to the command.
    """

    _attr_translation_key = "device_name"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min = 1
    _attr_native_max = NAME_MAX_LENGTH
    _attr_pattern = NAME_PATTERN

    def __init__(self, entry: SmartViewConfigEntry) -> None:
        """Initialise the text entity."""
        super().__init__(entry, "device_name")
        self._last_rename: float | None = None

    @property
    def native_value(self) -> str | None:
        """Return the device name, within the limits Home Assistant accepts."""
        name = self._client.state.name
        if not name:
            return None
        return name[:NAME_MAX_LENGTH]

    async def async_set_value(self, value: str) -> None:
        """Rename the device."""
        name = value.strip()
        if not name:
            raise ServiceValidationError("The device name cannot be empty.")
        restarts_network = not self._client.state.is_audio_monitor
        if (
            restarts_network
            and self._last_rename is not None
            and (remaining := RENAME_COOLDOWN - (time.monotonic() - self._last_rename)) > 0
        ):
            raise ServiceValidationError(
                "The SmartView is still restarting its network after the last rename. "
                f"Try again in {int(remaining) + 1} seconds."
            )
        fields = {self._client.state.name_field: name}
        try:
            await async_send(self, self._client.set_name(name), fields)
        except HomeAssistantError as err:
            # A rename restarts the device's network, which can drop the
            # connection before the ACK arrives. That is expected, not a failure.
            if not restarts_network or isinstance(
                err.__cause__, SmartViewCommandError | SmartViewValueError
            ):
                raise
            _LOGGER.debug("Connection dropped after rename, as expected: %s", err)
        self._last_rename = time.monotonic()
