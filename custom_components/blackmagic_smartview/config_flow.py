"""Config flow for the Blackmagic SmartView / SmartScope integration."""

from __future__ import annotations

import ipaddress
import logging
import re
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .client import SmartViewClient, SmartViewConnectionError, SmartViewState
from .const import (
    CONF_CACHED_MODEL,
    CONF_CACHED_MONITORS,
    CONF_CACHED_NAME,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DOMAIN,
    NAME_MAX_LENGTH,
)

_LOGGER = logging.getLogger(__name__)

_HOSTNAME_LABEL = re.compile(r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)$")


def valid_host(host: str) -> bool:
    """Return True for an IP address or a plain hostname (no URL, path or port)."""
    if not host or len(host) > 253:
        return False
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return True
    labels = host.removesuffix(".").split(".")
    if all(label.isdigit() for label in labels):
        return False  # looks like an IPv4 address, but is not a valid one
    return all(_HOSTNAME_LABEL.match(label) for label in labels)


def _schema(host: str = "", port: int = DEFAULT_PORT) -> vol.Schema:
    """Return the host/port form schema."""
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=host): str,
            vol.Required(CONF_PORT, default=port): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=65535)
            ),
        }
    )


def entry_data(host: str, port: int, state: SmartViewState) -> dict[str, Any]:
    """Return config entry data, including what is needed to start while offline."""
    name = state.device.get("Name")
    return {
        CONF_HOST: host,
        CONF_PORT: port,
        CONF_CACHED_MODEL: state.model,
        CONF_CACHED_NAME: name[:NAME_MAX_LENGTH] if name else None,
        CONF_CACHED_MONITORS: state.monitor_count,
    }


def _title(state: SmartViewState) -> str:
    """Return a friendly title for the config entry."""
    name = state.device.get("Name")
    return (name[:NAME_MAX_LENGTH] if name else None) or state.model or DEFAULT_NAME


class SmartViewConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for SmartView / SmartScope monitors."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise the flow."""
        # Filled in by the zeroconf step, which always runs before the confirm step
        self._host = ""
        self._port: int = DEFAULT_PORT
        self._state = SmartViewState()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle a flow started by the user."""
        errors: dict[str, str] = {}
        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=_schema(), errors=errors)

        host = user_input[CONF_HOST].strip()
        port = user_input[CONF_PORT]
        if not valid_host(host):
            errors[CONF_HOST] = "invalid_host"
        else:
            await self.async_set_unique_id(host.lower())
            self._abort_if_unique_id_configured()
            try:
                state = await SmartViewClient.probe(host, port)
            except SmartViewConnectionError as err:
                _LOGGER.debug("Cannot connect to %s:%s: %s", host, port, err)
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(
                    title=_title(state), data=entry_data(host, port, state)
                )
        return self.async_show_form(step_id="user", data_schema=_schema(host, port), errors=errors)

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> ConfigFlowResult:
        """Handle a SmartView found via zeroconf (_blackmagic._tcp)."""
        host = discovery_info.host  # always an IP address from zeroconf
        await self.async_set_unique_id(host.lower())
        self._abort_if_unique_id_configured()

        try:
            state = await SmartViewClient.probe(host, DEFAULT_PORT)
        except SmartViewConnectionError as err:
            _LOGGER.debug("Discovered %s is not reachable on %s: %s", host, DEFAULT_PORT, err)
            return self.async_abort(reason="cannot_connect")

        self._host = host
        self._port = DEFAULT_PORT
        self._state = state
        self.context["title_placeholders"] = {"name": _title(state)}
        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm adding a discovered device."""
        if user_input is not None:
            return self.async_create_entry(
                title=_title(self._state),
                data=entry_data(self._host, self._port, self._state),
            )
        self._set_confirm_only()
        return self.async_show_form(
            step_id="zeroconf_confirm",
            description_placeholders={
                "name": _title(self._state),
                "model": self._state.model or DEFAULT_NAME,
                "host": self._host,
            },
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change host or port of an existing entry."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        host = entry.data[CONF_HOST]
        port = entry.data[CONF_PORT]
        if user_input is None:
            return self.async_show_form(
                step_id="reconfigure", data_schema=_schema(host, port), errors=errors
            )

        same_address = user_input[CONF_HOST].strip() == host and user_input[CONF_PORT] == port
        host = user_input[CONF_HOST].strip()
        port = user_input[CONF_PORT]
        if not valid_host(host):
            errors[CONF_HOST] = "invalid_host"
        elif any(
            other.entry_id != entry.entry_id and other.unique_id == host.lower()
            for other in self._async_current_entries(include_ignore=False)
        ):
            errors["base"] = "already_configured"
        else:
            # The monitor has few connection slots. When the address is unchanged
            # and the running integration is connected, use its state instead of
            # opening a second connection that the monitor may refuse.
            client = getattr(entry, "runtime_data", None)
            if same_address and isinstance(client, SmartViewClient) and client.connected:
                state = client.state
            else:
                try:
                    state = await SmartViewClient.probe(host, port)
                except SmartViewConnectionError as err:
                    _LOGGER.debug("Cannot connect to %s:%s: %s", host, port, err)
                    errors["base"] = "cannot_connect"
                    state = None
            if state is not None:
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=host.lower(),
                    data=entry_data(host, port, state),
                )
        return self.async_show_form(
            step_id="reconfigure", data_schema=_schema(host, port), errors=errors
        )
