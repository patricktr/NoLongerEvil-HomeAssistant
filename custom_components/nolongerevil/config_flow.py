"""Config flow for No Longer Evil integration."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import NLEApiClient
from .const import (
    CONF_API_KEY,
    CONF_BASE_URL,
    DEFAULT_BASE_URL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .exceptions import NLEAuthenticationError, NLEConnectionError, NLEError

_LOGGER = logging.getLogger(__name__)


class NLEConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for No Longer Evil."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._api_key: str | None = None
        self._base_url: str = DEFAULT_BASE_URL

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> NLEOptionsFlow:
        """Get the options flow for this handler."""
        return NLEOptionsFlow(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            api_key = user_input[CONF_API_KEY]
            base_url = user_input.get(CONF_BASE_URL, DEFAULT_BASE_URL)

            # Test the connection
            session = async_get_clientsession(self.hass)
            client = NLEApiClient(api_key, session, base_url)

            try:
                devices = await client.get_devices()

                if not devices:
                    errors["base"] = "no_devices"
                else:
                    # Use the API key as unique ID (first 8 chars for privacy)
                    await self.async_set_unique_id(f"nle_{api_key[:8]}")
                    self._abort_if_unique_id_configured()

                    return self.async_create_entry(
                        title="No Longer Evil",
                        data={
                            CONF_API_KEY: api_key,
                            CONF_BASE_URL: base_url,
                        },
                        options={
                            CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
                        },
                    )

            except NLEAuthenticationError:
                errors["base"] = "invalid_auth"
            except NLEConnectionError:
                errors["base"] = "cannot_connect"
            except NLEError as err:
                _LOGGER.error("Unexpected error: %s", err)
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_API_KEY): str,
                    vol.Optional(CONF_BASE_URL, default=DEFAULT_BASE_URL): str,
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> FlowResult:
        """Handle reauthorization."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle reauthorization confirmation."""
        errors: dict[str, str] = {}

        if user_input is not None:
            api_key = user_input[CONF_API_KEY]

            # Get the existing entry
            existing_entry = self.hass.config_entries.async_get_entry(
                self.context["entry_id"]
            )
            if existing_entry is None:
                return self.async_abort(reason="reauth_failed")

            base_url = existing_entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL)

            # Test the new credentials
            session = async_get_clientsession(self.hass)
            client = NLEApiClient(api_key, session, base_url)

            try:
                await client.get_devices()

                # Update the config entry with new credentials
                self.hass.config_entries.async_update_entry(
                    existing_entry,
                    data={
                        **existing_entry.data,
                        CONF_API_KEY: api_key,
                    },
                )
                await self.hass.config_entries.async_reload(existing_entry.entry_id)
                return self.async_abort(reason="reauth_successful")

            except NLEAuthenticationError:
                errors["base"] = "invalid_auth"
            except NLEConnectionError:
                errors["base"] = "cannot_connect"
            except NLEError as err:
                _LOGGER.error("Unexpected error during reauth: %s", err)
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_API_KEY): str,
                }
            ),
            errors=errors,
        )


class NLEOptionsFlow(OptionsFlow):
    """Handle options flow for No Longer Evil."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_SCAN_INTERVAL,
                        default=self.config_entry.options.get(
                            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                        ),
                    ): vol.All(vol.Coerce(int), vol.Range(min=10, max=300)),
                }
            ),
        )
