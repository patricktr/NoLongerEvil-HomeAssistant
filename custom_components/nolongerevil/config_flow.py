"""Config flow for No Longer Evil integration."""

from __future__ import annotations

import logging
from typing import Any

from urllib.parse import urlparse

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    OptionsFlow,
)
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import NLEApiClient, NLESelfHostedClient, normalize_base_url
from .const import (
    CONF_API_KEY,
    CONF_BASE_URL,
    CONF_HOST_TYPE,
    CONF_UNAVAILABLE_AFTER,
    DEFAULT_BASE_URL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SELFHOSTED_URL,
    DEFAULT_UNAVAILABLE_AFTER,
    DOMAIN,
    HOST_TYPE_CLOUD,
    HOST_TYPE_SELF_HOSTED,
    MAX_UNAVAILABLE_AFTER,
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
        return NLEOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Let the user choose between the hosted cloud and a self-hosted server."""
        return self.async_show_menu(
            step_id="user",
            menu_options=[HOST_TYPE_CLOUD, HOST_TYPE_SELF_HOSTED],
        )

    async def async_step_cloud(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle setup against the hosted No Longer Evil cloud API."""
        errors: dict[str, str] = {}

        if user_input is not None:
            api_key = user_input[CONF_API_KEY]
            raw_url = user_input.get(CONF_BASE_URL, DEFAULT_BASE_URL)

            try:
                base_url = normalize_base_url(raw_url, default_scheme="https")
            except ValueError:
                errors["base"] = "invalid_url"
            else:
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
                                CONF_HOST_TYPE: HOST_TYPE_CLOUD,
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
            step_id="cloud",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_API_KEY): str,
                    vol.Optional(CONF_BASE_URL, default=DEFAULT_BASE_URL): str,
                }
            ),
            errors=errors,
        )

    async def async_step_self_hosted(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle setup against a self-hosted NLE server's Control API."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                base_url = normalize_base_url(
                    user_input[CONF_BASE_URL], default_scheme="http"
                )
            except ValueError:
                errors["base"] = "invalid_url"
            else:
                session = async_get_clientsession(self.hass)
                client = NLESelfHostedClient(base_url, session)

                try:
                    devices = await client.get_devices()

                    if not devices:
                        errors["base"] = "no_devices"
                    else:
                        host = urlparse(base_url).netloc
                        await self.async_set_unique_id(f"nle_selfhosted_{host}")
                        self._abort_if_unique_id_configured()

                        return self.async_create_entry(
                            title=f"No Longer Evil ({host})",
                            data={
                                CONF_HOST_TYPE: HOST_TYPE_SELF_HOSTED,
                                CONF_BASE_URL: base_url,
                                # No API key on the self-hosted Control API.
                                CONF_API_KEY: "",
                            },
                            options={
                                CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
                            },
                        )

                except NLEConnectionError:
                    errors["base"] = "cannot_connect"
                except NLEError as err:
                    _LOGGER.error("Unexpected error: %s", err)
                    errors["base"] = "unknown"

        return self.async_show_form(
            step_id="self_hosted",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_BASE_URL, default=DEFAULT_SELFHOSTED_URL
                    ): str,
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

                # Update the config entry with new credentials. On a loaded
                # entry the update listener reloads for us; reload explicitly
                # only when no listener can have fired — the entry never
                # finished setup (so the listener was never registered) or the
                # key is unchanged (so no update event was emitted). Exactly
                # one reload runs either way.
                changed = self.hass.config_entries.async_update_entry(
                    existing_entry,
                    data={
                        **existing_entry.data,
                        CONF_API_KEY: api_key,
                    },
                )
                if not changed or existing_entry.state is not ConfigEntryState.LOADED:
                    await self.hass.config_entries.async_reload(
                        existing_entry.entry_id
                    )
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
                    vol.Optional(
                        CONF_UNAVAILABLE_AFTER,
                        default=self.config_entry.options.get(
                            CONF_UNAVAILABLE_AFTER, DEFAULT_UNAVAILABLE_AFTER
                        ),
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(min=0, max=MAX_UNAVAILABLE_AFTER),
                    ),
                }
            ),
        )
