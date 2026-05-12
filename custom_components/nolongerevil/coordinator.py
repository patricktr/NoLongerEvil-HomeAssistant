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

# Number of consecutive polls in which the API-key probe must return 401
# before we tear down the integration with ConfigEntryAuthFailed. Defending
# against the case where the upstream service simultaneously returns 401 on
# both the per-device status endpoint and the list-devices probe — e.g. a
# brief auth-service flap during a deploy — while still surfacing a real
# revoked-key state within ~3 polls.
_AUTH_PROBE_FAILURE_THRESHOLD = 3


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

        # Latch device capabilities: once can_cool/can_heat is seen as True,
        # keep it True. The Nest API can temporarily report False even when
        # the wiring hasn't changed. Pre-populate from persisted config entry
        # data so the latch survives HA restarts and integration reloads.
        persisted_caps: dict[str, dict[str, bool]] = config_entry.data.get(
            "capability_cache", {}
        )
        self._capability_cache: dict[str, dict[str, bool]] = {
            device.id: {
                "can_cool": persisted_caps.get(device.id, {}).get("can_cool", False),
                "can_heat": persisted_caps.get(device.id, {}).get("can_heat", False),
            }
            for device in devices
        }

        self._consecutive_auth_probe_failures = 0

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
        # Probe outcome cached per-poll so we run at most one list-devices
        # call per cycle no matter how many devices return 401.
        # True  = probe succeeded (key valid)
        # False = probe returned 401 (key looks invalid)
        # None  = not probed yet, or probe was inconclusive (other error)
        auth_probe_result: bool | None = None

        try:
            for device_id in self.devices:
                try:
                    status = await self.client.get_device_status(device_id)
                    self._apply_capability_latch(device_id, status)
                    data[device_id] = status
                except NLEAuthenticationError as err:
                    if auth_probe_result is None:
                        auth_probe_result = await self._probe_api_key()
                    _LOGGER.debug(
                        "Device %s returned auth error (probe result: %s): %s",
                        device_id,
                        auth_probe_result,
                        err,
                    )
                except NLEError as err:
                    # Transient per-device errors (e.g. occasional HTTP 502 from
                    # the upstream gateway) — keep noise out of the log and let
                    # the next poll retry. If every device fails this poll we
                    # still raise UpdateFailed below.
                    _LOGGER.debug(
                        "Failed to get status for device %s: %s", device_id, err
                    )

            # Reconcile the consecutive-failure counter once per poll.
            if data or auth_probe_result is True:
                # Any evidence the key works this poll resets the counter:
                # either a device fetch succeeded, or the explicit probe
                # confirmed the key is valid.
                self._consecutive_auth_probe_failures = 0
            elif auth_probe_result is False:
                self._consecutive_auth_probe_failures += 1
                if (
                    self._consecutive_auth_probe_failures
                    >= _AUTH_PROBE_FAILURE_THRESHOLD
                ):
                    raise NLEAuthenticationError(
                        "API key probe returned 401 on "
                        f"{self._consecutive_auth_probe_failures} consecutive polls"
                    )
                _LOGGER.warning(
                    "API key probe returned 401 (%d/%d consecutive); deferring "
                    "re-auth in case this is a transient upstream flap",
                    self._consecutive_auth_probe_failures,
                    _AUTH_PROBE_FAILURE_THRESHOLD,
                )
            # auth_probe_result is None and no device succeeded: probe wasn't
            # run or was inconclusive — leave the counter alone and fall
            # through to the UpdateFailed below.

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

    async def _probe_api_key(self) -> bool | None:
        """Probe the API to check whether the configured key is still valid.

        Returns True if a list-devices call succeeds (key is definitively
        valid), False if it fails with NLEAuthenticationError (key looks
        invalid), or None if the probe was inconclusive (other error such as
        a 502 from the upstream gateway) — in which case the caller should
        not change the consecutive-failure counter.
        """
        try:
            await self.client.get_devices()
        except NLEAuthenticationError:
            return False
        except NLEError:
            return None
        return True

    def _apply_capability_latch(
        self, device_id: str, status: NLEDeviceStatus
    ) -> None:
        """Latch device capabilities so they don't regress to False.

        The Nest API can temporarily report can_cool=False even when the
        thermostat has cooling wires connected. Once we see a capability
        as True, we keep it True for the lifetime of this coordinator.
        """
        if device_id not in self._capability_cache:
            persisted = self.config_entry.data.get("capability_cache", {})
            self._capability_cache[device_id] = {
                "can_cool": persisted.get(device_id, {}).get("can_cool", False),
                "can_heat": persisted.get(device_id, {}).get("can_heat", False),
            }

        cache = self._capability_cache[device_id]

        # Latch to True — never downgrade back to False.
        # Persist to config entry data whenever a new True is observed so the
        # latch survives HA restarts and integration reloads.
        needs_persist = False
        if status.can_cool and not cache["can_cool"]:
            cache["can_cool"] = True
            needs_persist = True
        if status.can_heat and not cache["can_heat"]:
            cache["can_heat"] = True
            needs_persist = True
        if needs_persist:
            self._persist_capability_cache()

        # Apply latched values back to the status object
        if cache["can_cool"] and not status.can_cool:
            _LOGGER.debug(
                "Device %s: API reported can_cool=False but previously "
                "reported True — keeping can_cool=True",
                device_id,
            )
            status.can_cool = True
        if cache["can_heat"] and not status.can_heat:
            _LOGGER.debug(
                "Device %s: API reported can_heat=False but previously "
                "reported True — keeping can_heat=True",
                device_id,
            )
            status.can_heat = True

    def _persist_capability_cache(self) -> None:
        """Persist the capability cache to config entry data.

        This ensures the latch survives HA restarts and integration reloads.
        """
        new_data = {**self.config_entry.data, "capability_cache": self._capability_cache}
        self.hass.config_entries.async_update_entry(self.config_entry, data=new_data)
        _LOGGER.debug("Persisted capability cache: %s", self._capability_cache)

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
