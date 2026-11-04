"""Config flow for the Blackmagic SmartView, SmartScope and Audio Monitor integration."""

from __future__ import annotations

import ipaddress
import logging
import re
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .client import SmartViewClient, SmartViewConnectionError, SmartViewState
from .const import (
    CONF_CACHED_MODEL,
    CONF_CACHED_MONITORS,
    CONF_CACHED_NAME,
    CONF_DEVICE,
    CONF_DEVICE_TYPE,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DETECT_PORTS,
    DEVICE_DETECT,
    DEVICE_PORTS,
    DEVICE_TYPE_AUDIO_MONITOR,
    DEVICE_TYPE_SMARTVIEW,
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


def _user_schema(host: str = "", device: str = DEVICE_DETECT) -> vol.Schema:
    """Return the add form: an address and which device to look for (detect by default)."""
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=host): str,
            vol.Required(CONF_DEVICE, default=device): SelectSelector(
                SelectSelectorConfig(
                    options=[DEVICE_DETECT, DEVICE_TYPE_SMARTVIEW, DEVICE_TYPE_AUDIO_MONITOR],
                    translation_key=CONF_DEVICE,
                    mode=SelectSelectorMode.LIST,
                )
            ),
        }
    )


def _host_schema(host: str) -> vol.Schema:
    """Return the reconfigure form: only the address; the device type is known."""
    return vol.Schema({vol.Required(CONF_HOST, default=host): str})


async def _detect(host: str, ports: tuple[int, ...]) -> tuple[SmartViewState, int]:
    """Read the device's state on the first of the given ports it answers on."""
    error: SmartViewConnectionError | None = None
    for candidate in ports:
        try:
            return await SmartViewClient.probe(host, candidate), candidate
        except SmartViewConnectionError as err:
            _LOGGER.debug("No SmartView protocol on %s:%s: %s", host, candidate, err)
            error = err
    raise SmartViewConnectionError(f"No supported device found at {host}") from error


def _unique_id(host: str, state: SmartViewState) -> str:
    """Return the device's own unique ID where it has one, otherwise the address."""
    return state.unique_id or host.lower()


def _name(state: SmartViewState) -> str | None:
    """Return the device name, cut to the length the integration accepts."""
    return state.name[:NAME_MAX_LENGTH] if state.name else None


def entry_data(host: str, port: int, state: SmartViewState) -> dict[str, Any]:
    """Return config entry data, including what is needed to start while offline."""
    return {
        CONF_HOST: host,
        CONF_PORT: port,
        CONF_CACHED_MODEL: state.model,
        CONF_CACHED_NAME: _name(state),
        CONF_CACHED_MONITORS: state.monitor_count,
        CONF_DEVICE_TYPE: state.device_type,
    }


def _title(state: SmartViewState) -> str:
    """Return a friendly title for the config entry."""
    return _name(state) or state.model or DEFAULT_NAME


class SmartViewConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for SmartView, SmartScope and Audio Monitor devices."""

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
            return self.async_show_form(step_id="user", data_schema=_user_schema(), errors=errors)

        host = user_input[CONF_HOST].strip()
        device: str = user_input[CONF_DEVICE]
        ports = (DEVICE_PORTS[device],) if device in DEVICE_PORTS else DETECT_PORTS
        if not valid_host(host):
            errors[CONF_HOST] = "invalid_host"
        else:
            self._async_abort_entries_match({CONF_HOST: host})
            try:
                state, port = await _detect(host, ports)
            except SmartViewConnectionError as err:
                _LOGGER.debug("Cannot connect to %s: %s", host, err)
                errors["base"] = "cannot_connect"
            else:
                # A known device (by its own unique ID) at a new address: update it
                await self.async_set_unique_id(_unique_id(host, state))
                self._abort_if_unique_id_configured(updates={CONF_HOST: host, CONF_PORT: port})
                return self.async_create_entry(
                    title=_title(state), data=entry_data(host, port, state)
                )
        return self.async_show_form(
            step_id="user", data_schema=_user_schema(host, device), errors=errors
        )

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
        """Change the address of an existing entry; the device type and port stay."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        host: str = entry.data[CONF_HOST]
        port: int = entry.data[CONF_PORT]
        if user_input is None:
            return self.async_show_form(
                step_id="reconfigure", data_schema=_host_schema(host), errors=errors
            )

        new_host = user_input[CONF_HOST].strip()
        same_address = new_host == host
        if not valid_host(new_host):
            errors[CONF_HOST] = "invalid_host"
        elif any(
            other.entry_id != entry.entry_id
            and new_host.lower() in (other.unique_id, str(other.data.get(CONF_HOST, "")).lower())
            for other in self._async_current_entries(include_ignore=False)
        ):
            errors["base"] = "already_configured"
        else:
            state: SmartViewState | None = None
            # The device has few connection slots. When the address is unchanged
            # and the running integration is connected, use its state instead of
            # opening a second connection that the device may refuse.
            client = getattr(entry, "runtime_data", None)
            if same_address and isinstance(client, SmartViewClient) and client.connected:
                state = client.state
            else:
                try:
                    state, _ = await _detect(new_host, (port,))
                except SmartViewConnectionError as err:
                    _LOGGER.debug("Cannot connect to %s: %s", new_host, err)
                    errors["base"] = "cannot_connect"
            if state is not None:
                device_type = entry.data.get(CONF_DEVICE_TYPE, DEVICE_TYPE_SMARTVIEW)
                if state.device_type != device_type or (
                    state.unique_id is not None and state.unique_id != entry.unique_id
                ):
                    errors["base"] = "wrong_device"
                else:
                    return self.async_update_reload_and_abort(
                        entry,
                        unique_id=_unique_id(new_host, state),
                        data=entry_data(new_host, port, state),
                    )
        return self.async_show_form(
            step_id="reconfigure", data_schema=_host_schema(new_host), errors=errors
        )
