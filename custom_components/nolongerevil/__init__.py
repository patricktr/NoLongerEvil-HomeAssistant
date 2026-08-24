"""The No Longer Evil integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import create_client
from .const import (
    CONF_API_KEY,
    CONF_BASE_URL,
    CONF_HOST_TYPE,
    DEFAULT_BASE_URL,
    DOMAIN,
    HOST_TYPE_CLOUD,
)
from .coordinator import NLEDataUpdateCoordinator
from .exceptions import NLEAuthenticationError, NLEConnectionError

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.CLIMATE,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SWITCH,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up No Longer Evil from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    host_type = entry.data.get(CONF_HOST_TYPE, HOST_TYPE_CLOUD)
    api_key = entry.data.get(CONF_API_KEY, "")
    base_url = entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL)

    session = async_get_clientsession(hass)
    client = create_client(host_type, api_key, base_url, session)

    try:
        devices = await client.get_devices()
    except NLEAuthenticationError as err:
        _LOGGER.error("Authentication failed: %s", err)
        return False
    except NLEConnectionError as err:
        _LOGGER.error("Connection failed: %s", err)
        return False

    if not devices:
        _LOGGER.warning("No devices found for this account")
        return False

    coordinator = NLEDataUpdateCoordinator(hass, client, devices, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(async_update_options))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        coordinator: NLEDataUpdateCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.client.close()

    return unload_ok


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its configuration changes.

    This listener fires for every entry update, including the coordinator
    persisting its runtime caches into entry data. Those writes must not
    reload the integration — a reload briefly flips every entity to
    unavailable, which is not acceptable for a routine HVAC state change.
    """
    coordinator: NLEDataUpdateCoordinator | None = hass.data.get(DOMAIN, {}).get(
        entry.entry_id
    )
    if coordinator is not None and not coordinator.config_requires_reload():
        _LOGGER.debug("Entry update only touched runtime caches; skipping reload")
        return
    await hass.config_entries.async_reload(entry.entry_id)
