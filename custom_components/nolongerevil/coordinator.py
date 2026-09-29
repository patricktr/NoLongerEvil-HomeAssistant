"""Data update coordinator for No Longer Evil integration."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from time import monotonic
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import NLEClientBase, NLEDevice, NLEDeviceStatus
from .const import (
    CONF_UNAVAILABLE_AFTER,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_UNAVAILABLE_AFTER,
    DOMAIN,
    RUNTIME_CACHE_KEYS,
)
from .exceptions import NLEAuthenticationError, NLEConnectionError, NLEError
from .exceptions import NLEIncompleteStatusError, NLEServerError

_LOGGER = logging.getLogger(__name__)

# Number of consecutive polls in which the API-key probe must return 401
# before we tear down the integration with ConfigEntryAuthFailed. Defending
# against the case where the upstream service simultaneously returns 401 on
# both the per-device status endpoint and the list-devices probe — e.g. a
# brief auth-service flap during a deploy — while still surfacing a real
# revoked-key state within ~3 polls.
_AUTH_PROBE_FAILURE_THRESHOLD = 3

# A status fetch that fails with a connection error or a 5xx is retried once
# within the same poll after this delay. Most such failures are single blips
# (a dropped packet, a gateway restarting), and a quick retry clears them
# without serving stale data at all. Auth, rate-limit and incomplete-status
# failures are not retried: repeating the request cannot fix them.
_RETRY_DELAY_SECONDS = 2


def _reload_relevant_config(
    config_entry: ConfigEntry,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the parts of the entry config that require a reload to apply.

    The runtime caches the coordinator itself persists into entry data are
    excluded, so writing them never looks like a configuration change.
    """
    data = {
        key: value
        for key, value in config_entry.data.items()
        if key not in RUNTIME_CACHE_KEYS
    }
    return data, dict(config_entry.options)


class NLEDataUpdateCoordinator(DataUpdateCoordinator[dict[str, NLEDeviceStatus]]):
    """Class to manage fetching data from the API."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        client: NLEClientBase,
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

        # The API occasionally omits a thermostat's mode. Restore the cache
        # across restarts and reloads so an omission does not become "heat".
        persisted_modes: dict[str, str] = config_entry.data.get("mode_cache", {})
        self._mode_cache: dict[str, str] = {
            device.id: persisted_modes[device.id]
            for device in devices
            if device.id in persisted_modes
        }

        # Staleness bookkeeping for failed polls: when each device last
        # returned a complete status (monotonic for the grace-window check,
        # wall clock for the diagnostic sensor), its consecutive failed
        # polls, and which devices have been dropped from the data set until
        # a complete status arrives.
        self._last_success_monotonic: dict[str, float] = {}
        self._last_success: dict[str, datetime] = {}
        self._failure_streak: dict[str, int] = {}
        self._stale_unavailable: set[str] = set()

        self._consecutive_auth_probe_failures = 0

        scan_interval = config_entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
        )
        # How long a device keeps serving its last complete snapshot while
        # its updates fail, before its entities go unavailable.
        self._unavailable_after = 60 * config_entry.options.get(
            CONF_UNAVAILABLE_AFTER, DEFAULT_UNAVAILABLE_AFTER
        )

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
            config_entry=config_entry,
        )

        # Snapshot of the reload-relevant config this coordinator was built
        # from, compared by config_requires_reload when the entry changes.
        self._setup_config = _reload_relevant_config(config_entry)

    def config_requires_reload(self) -> bool:
        """Return True when the entry config changed in a way that needs a reload.

        Returns False for entry updates that only touched the runtime caches
        this coordinator persists (see RUNTIME_CACHE_KEYS), since the running
        setup already reflects those values.
        """
        return _reload_relevant_config(self.config_entry) != self._setup_config

    async def _async_update_data(self) -> dict[str, NLEDeviceStatus]:
        """Fetch data from API for all devices."""
        data: dict[str, NLEDeviceStatus] = {}
        # Probe outcome cached per-poll so we run at most one list-devices
        # call per cycle no matter how many devices return 401.
        # True  = probe succeeded (key valid)
        # False = probe returned 401 (key looks invalid)
        # None  = not probed yet, or probe was inconclusive (other error)
        auth_probe_result: bool | None = None
        # Whether any device got an authenticated response this poll.
        # Retained snapshots also land in ``data``, so ``data`` alone is no
        # evidence that the API key works.
        api_responded = False

        try:
            for device_id in self.devices:
                try:
                    status = await self._fetch_status(device_id)
                    self._apply_capability_latch(device_id, status)
                    self._apply_mode_latch(device_id, status)
                    data[device_id] = status
                    api_responded = True
                    self._note_complete_status(device_id)
                    continue
                except NLEAuthenticationError as err:
                    if auth_probe_result is None:
                        auth_probe_result = await self._probe_api_key()
                    _LOGGER.debug(
                        "Device %s returned auth error (probe result: %s): %s",
                        device_id,
                        auth_probe_result,
                        err,
                    )
                except NLEIncompleteStatusError as err:
                    api_responded = True
                    _LOGGER.debug(
                        "Device %s returned an incomplete status: %s",
                        device_id,
                        err,
                    )
                except NLEError as err:
                    # Transient per-device errors (e.g. occasional HTTP 502 from
                    # the upstream gateway) — keep noise out of the log and let
                    # the grace window below decide what the entities show.
                    _LOGGER.debug(
                        "Failed to get status for device %s: %s", device_id, err
                    )

                # Every failure above rides out the grace window on the last
                # complete snapshot. If no device has fresh or retained data
                # this poll we still raise UpdateFailed below.
                retained = self._handle_failed_poll(device_id)
                if retained is not None:
                    data[device_id] = retained

            # Reconcile the consecutive-failure counter once per poll.
            if api_responded or auth_probe_result is True:
                # Any evidence the key works this poll resets the counter:
                # either a device fetch got a response, or the explicit probe
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

    async def _fetch_status(self, device_id: str) -> NLEDeviceStatus:
        """Fetch a device status, retrying once on a transient failure."""
        try:
            return await self.client.get_device_status(device_id)
        except (NLEConnectionError, NLEServerError) as err:
            _LOGGER.debug(
                "Status fetch for device %s failed (%s); retrying in %d s",
                device_id,
                err,
                _RETRY_DELAY_SECONDS,
            )
        await asyncio.sleep(_RETRY_DELAY_SECONDS)
        return await self.client.get_device_status(device_id)

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

    def _apply_mode_latch(self, device_id: str, status: NLEDeviceStatus) -> None:
        """Remember a reported HVAC mode or restore the last known value.

        Only explicit API values replace the cache; a missing value reuses it.
        """
        api_mode = status.target_temperature_type
        if api_mode is None:
            status.target_temperature_type = self._mode_cache.get(device_id, "heat")
            return
        self._remember_mode(device_id, api_mode)

    def _remember_mode(self, device_id: str, mode: str) -> None:
        """Cache and persist a device's last known HVAC mode when it changes."""
        if self._mode_cache.get(device_id) != mode:
            self._mode_cache[device_id] = mode
            self._persist_mode_cache()

    def _persist_mode_cache(self) -> None:
        """Persist modes so the latch survives restarts and reloads."""
        new_data = {**self.config_entry.data, "mode_cache": self._mode_cache}
        self.hass.config_entries.async_update_entry(self.config_entry, data=new_data)
        _LOGGER.debug("Persisted mode cache: %s", self._mode_cache)

    def _handle_failed_poll(self, device_id: str) -> NLEDeviceStatus | None:
        """Serve the last complete snapshot through a bounded outage.

        Returns the snapshot to keep publishing, or None once the device has
        gone the configured unavailable-after window without a complete
        status — the device then drops out of the data set and its entities
        go unavailable until a complete status arrives. A window of 0 drops
        the device on its first failed poll.
        """
        streak = self._failure_streak.get(device_id, 0) + 1
        self._failure_streak[device_id] = streak
        last_success = self._last_success_monotonic.get(device_id)
        previous_status = self.get_device_status(device_id)

        if last_success is None or previous_status is None:
            _LOGGER.debug(
                "Device %s has no snapshot to retain (%d consecutive failed "
                "polls)",
                device_id,
                streak,
            )
            return None

        age = monotonic() - last_success
        if age < self._unavailable_after:
            cached_mode = self._mode_cache.get(device_id)
            if cached_mode is not None:
                previous_status.target_temperature_type = cached_mode
            _LOGGER.debug(
                "Device %s update failed; retaining last known status from "
                "%.0f s ago (%d consecutive failed polls)",
                device_id,
                age,
                streak,
            )
            return previous_status

        if device_id not in self._stale_unavailable:
            self._stale_unavailable.add(device_id)
            _LOGGER.warning(
                "Device %s has had no successful update for %.0f s (%d "
                "consecutive failed polls); its entities are unavailable "
                "until it recovers",
                device_id,
                age,
                streak,
            )
        else:
            _LOGGER.debug(
                "Device %s still failing to update (%d consecutive)",
                device_id,
                streak,
            )
        return None

    def _note_complete_status(self, device_id: str) -> None:
        """Record a successful update; log when a dropped device recovers."""
        self._last_success_monotonic[device_id] = monotonic()
        self._last_success[device_id] = dt_util.utcnow()
        streak = self._failure_streak.pop(device_id, 0)
        if device_id in self._stale_unavailable:
            self._stale_unavailable.discard(device_id)
            _LOGGER.info(
                "Device %s updated successfully after %d consecutive failed "
                "polls",
                device_id,
                streak,
            )

    def get_last_success(self, device_id: str) -> datetime | None:
        """Return when the device last returned a complete status."""
        return self._last_success.get(device_id)

    def get_capabilities(self, device_id: str) -> dict[str, bool]:
        """Return stable heat/cool capabilities rather than one poll's values."""
        return self._capability_cache.get(
            device_id, {"can_cool": False, "can_heat": False}
        )

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
        # Cache after a successful write but before refreshing: the immediate
        # status response may omit the mode and must restore the new value.
        self._remember_mode(
            device_id,
            "range" if mode == "heat-cool" else mode,
        )
        await self.async_request_refresh()

    async def async_set_away_mode(self, device_id: str, away: bool) -> None:
        """Set away mode for a device."""
        await self.client.set_away_mode(device_id, away)
        await self.async_request_refresh()

    async def async_set_fan_mode(self, device_id: str, mode: str) -> None:
        """Set fan mode for a device."""
        await self.client.set_fan_mode(device_id, mode)
        await self.async_request_refresh()
