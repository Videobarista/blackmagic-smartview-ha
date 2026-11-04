"""Diagnostics for the Blackmagic SmartView, SmartScope and Audio Monitor integration.

Diagnostics are often attached to public GitHub issues, so addresses, the
hostname and the Unique ID (both contain the MAC address) are redacted. MAC addresses that
appear anywhere else are masked as well.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from . import SmartViewConfigEntry
from .client import mask_mac

TO_REDACT = {
    CONF_HOST,
    "Hostname",
    "Unique ID",
    "Static subnet",
    "Current subnet",
    "Static address",
    "Static netmask",
    "Static gateway",
    "Current address",
    "Current netmask",
    "Current gateway",
}


def _mask(value: Any) -> Any:
    """Mask MAC addresses in all strings of a nested structure."""
    if isinstance(value, str):
        return mask_mac(value)
    if isinstance(value, dict):
        return {key: _mask(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_mask(item) for item in value]
    return value


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SmartViewConfigEntry
) -> dict[str, Any]:
    """Return the device state dump without addresses or MAC addresses."""
    client = entry.runtime_data
    return _mask(
        async_redact_data(
            {
                "entry": dict(entry.data),
                "connected": client.connected,
                "state": client.state.as_dict(),
            },
            TO_REDACT,
        )
    )
