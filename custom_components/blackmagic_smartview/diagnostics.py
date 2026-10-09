"""Diagnostics for the Blackmagic SmartView / SmartScope integration."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from . import SmartViewConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SmartViewConfigEntry
) -> dict[str, Any]:
    """Return the full device state dump; useful to report unknown fields."""
    client = entry.runtime_data
    return {
        "entry": dict(entry.data),
        "connected": client.connected,
        "state": client.state.as_dict(),
    }
