"""Data update coordinator for No Longer Evil integration."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import NLEApiClient, NLEDevice, NLEDeviceStatus
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN
from .exceptions import NLEAuthenticationError, NLEConnectionError, NLEError

_LOGGER = logging.getLogger(__name__)


class NLEDataUpdateCoordinator(DataUpdateCoordinator[dict[str, NLEDeviceStatus]]):
    """Class to manage fetching data from the API."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        client: NLEApiClient,
        devices: list[NLEDevice],
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the coordinator."""
        self.client = client
        self.devices = {device.id: device for device in devices}

        scan_interval = config_entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
        )

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
            config_entry=config_entry,
        )

    async def _async_update_data(self) -> dict[str, NLEDeviceStatus]:
        """Fetch data from API for all devices."""
        data: dict[str, NLEDeviceStatus] = {}

        try:
            for device_id in self.devices:
                try:
                    status = await self.client.get_device_status(device_id)
                    data[device_id] = status
                except NLEError as err:
                    _LOGGER.warning(
                        "Failed to get status for device %s: %s", device_id, err
                    )
                    # Continue with other devices

        except NLEAuthenticationError as err:
            raise ConfigEntryAuthFailed(
                "Authentication failed. Please reconfigure the integration."
            ) from err
        except NLEConnectionError as err:
            raise UpdateFailed(f"Connection error: {err}") from err
        except NLEError as err:
            raise UpdateFailed(f"Error communicating with API: {err}") from err

        if not data:
            raise UpdateFailed("Failed to get status for any device")

        return data

    def get_device(self, device_id: str) -> NLEDevice | None:
        """Get device info by ID."""
        return self.devices.get(device_id)

    def get_device_status(self, device_id: str) -> NLEDeviceStatus | None:
        """Get device status by ID."""
        if self.data is None:
            return None
        return self.data.get(device_id)

    async def async_set_temperature(
        self,
        device_id: str,
        temperature: float,
        mode: str,
    ) -> None:
        """Set temperature for a device."""
        await self.client.set_temperature(device_id, temperature, mode, "C")
        await self.async_request_refresh()

    async def async_set_temperature_range(
        self,
        device_id: str,
        low: float,
        high: float,
    ) -> None:
        """Set temperature range for a device."""
        await self.client.set_temperature_range(device_id, low, high, "C")
        await self.async_request_refresh()

    async def async_set_hvac_mode(self, device_id: str, mode: str) -> None:
        """Set HVAC mode for a device."""
        await self.client.set_hvac_mode(device_id, mode)
        await self.async_request_refresh()

    async def async_set_away_mode(self, device_id: str, away: bool) -> None:
        """Set away mode for a device."""
        await self.client.set_away_mode(device_id, away)
        await self.async_request_refresh()

    async def async_set_fan_mode(self, device_id: str, mode: str) -> None:
        """Set fan mode for a device."""
        await self.client.set_fan_mode(device_id, mode)
        await self.async_request_refresh()
