"""API client for the No Longer Evil API."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp
from aiohttp import ClientError, ClientResponseError, ClientTimeout

from .const import (
    DEFAULT_BASE_URL,
    ENDPOINT_AWAY,
    ENDPOINT_DEVICES,
    ENDPOINT_FAN,
    ENDPOINT_MODE,
    ENDPOINT_SCHEDULE,
    ENDPOINT_STATUS,
    ENDPOINT_TEMPERATURE,
    ENDPOINT_TEMPERATURE_RANGE,
)
from .exceptions import (
    NLEAPIError,
    NLEAuthenticationError,
    NLEConnectionError,
    NLERateLimitError,
)

_LOGGER = logging.getLogger(__name__)

DEFAULT_TIMEOUT = ClientTimeout(total=30)


class NLEDevice:
    """Representation of an NLE device."""

    def __init__(self, data: dict[str, Any]) -> None:
        """Initialize the device."""
        self.id: str = data.get("id", "")
        self.serial: str = data.get("serial", "")
        self.name: str | None = data.get("name")
        self.access_type: str = data.get("accessType", "shared")

    @property
    def display_name(self) -> str:
        """Return display name for the device."""
        return self.name or f"Thermostat {self.serial[-4:]}"


class NLEDeviceStatus:
    """Representation of an NLE device status."""

    def __init__(self, data: dict[str, Any]) -> None:
        """Initialize the device status."""
        self._data = data
        self._parse_data()

    def _parse_data(self) -> None:
        """Parse the status data from the API response."""
        self.device_id: str = self._data.get("id", "")
        self.serial: str = self._data.get("serial", "")
        self.name: str | None = self._data.get("name")

        # Find the shared state data
        shared_key = f"shared.{self.serial}"
        shared_data = self._data.get(shared_key, {})

        # Find the device settings data
        device_key = f"device.{self.serial}"
        device_data = self._data.get(device_key, {})

        # Current state
        self.current_temperature: float | None = shared_data.get("current_temperature")
        self.target_temperature: float | None = shared_data.get("target_temperature")
        self.target_temperature_type: str = shared_data.get(
            "target_temperature_type", "heat"
        )
        self.target_temperature_low: float | None = shared_data.get(
            "target_temperature_low"
        )
        self.target_temperature_high: float | None = shared_data.get(
            "target_temperature_high"
        )

        # HVAC state
        self.heater_active: bool = shared_data.get("hvac_heater_state", False)
        self.ac_active: bool = shared_data.get("hvac_ac_state", False)
        self.fan_active: bool = shared_data.get("hvac_fan_state", False)

        # Fan mode
        self.fan_mode: str = shared_data.get("fan_mode", "auto")

        # Away mode (0 = home, 2 = away)
        away_value = shared_data.get("auto_away", 0)
        self.is_away: bool = away_value == 2

        # Device capabilities
        self.can_cool: bool = shared_data.get("can_cool", False)
        self.can_heat: bool = shared_data.get("can_heat", True)

        # Device settings
        self.temperature_scale: str = device_data.get("temperature_scale", "C")
        self.eco_mode_enabled: bool = device_data.get("eco_mode_enabled", False)
        self.temperature_lock_enabled: bool = device_data.get(
            "temperature_lock_enabled", False
        )

        # Humidity (if available)
        self.current_humidity: float | None = shared_data.get("current_humidity")

    @property
    def hvac_mode(self) -> str:
        """Return the current HVAC mode."""
        if self.target_temperature_type == "range":
            return "heat-cool"
        return self.target_temperature_type

    @property
    def hvac_action(self) -> str:
        """Return the current HVAC action."""
        if self.heater_active:
            return "heating"
        if self.ac_active:
            return "cooling"
        if self.fan_active:
            return "fan"
        return "idle"


class NLEApiClient:
    """API client for the No Longer Evil API."""

    def __init__(
        self,
        api_key: str,
        session: aiohttp.ClientSession | None = None,
        base_url: str = DEFAULT_BASE_URL,
    ) -> None:
        """Initialize the API client."""
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._session = session
        self._own_session = session is None

        # Rate limiting tracking
        self._rate_limit_remaining: int | None = None
        self._rate_limit_reset: str | None = None

    @property
    def _headers(self) -> dict[str, str]:
        """Return the request headers."""
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create the aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=DEFAULT_TIMEOUT)
            self._own_session = True
        return self._session

    async def close(self) -> None:
        """Close the API client session."""
        if self._own_session and self._session and not self._session.closed:
            await self._session.close()

    def _update_rate_limits(self, headers: dict[str, Any]) -> None:
        """Update rate limit tracking from response headers."""
        if "X-RateLimit-Remaining" in headers:
            self._rate_limit_remaining = int(headers["X-RateLimit-Remaining"])
        if "X-RateLimit-Reset" in headers:
            self._rate_limit_reset = headers["X-RateLimit-Reset"]

    async def _request(
        self,
        method: str,
        endpoint: str,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make an API request."""
        session = await self._get_session()
        url = f"{self._base_url}{endpoint}"

        try:
            async with session.request(
                method,
                url,
                headers=self._headers,
                json=data if data else None,
            ) as response:
                self._update_rate_limits(response.headers)

                if response.status == 401:
                    raise NLEAuthenticationError("Invalid API key")

                if response.status == 403:
                    raise NLEAuthenticationError("Access denied to resource")

                if response.status == 429:
                    retry_after = None
                    try:
                        error_data = await response.json()
                        retry_after = error_data.get("retryAfter")
                    except Exception:
                        pass
                    raise NLERateLimitError(
                        "Rate limit exceeded", retry_after=retry_after
                    )

                if response.status == 404:
                    raise NLEAPIError("Resource not found")

                if response.status >= 400:
                    try:
                        error_data = await response.json()
                        error_msg = error_data.get("error", "Unknown error")
                    except Exception:
                        error_msg = f"HTTP {response.status}"
                    raise NLEAPIError(f"API error: {error_msg}")

                return await response.json()

        except ClientResponseError as err:
            _LOGGER.error("HTTP error: %s", err)
            raise NLEAPIError(f"HTTP error: {err}") from err
        except asyncio.TimeoutError as err:
            _LOGGER.error("Request timeout")
            raise NLEConnectionError("Request timeout") from err
        except ClientError as err:
            _LOGGER.error("Connection error: %s", err)
            raise NLEConnectionError(f"Connection error: {err}") from err

    async def get_devices(self) -> list[NLEDevice]:
        """Get list of devices."""
        response = await self._request("GET", ENDPOINT_DEVICES)
        devices_data = response.get("devices", [])
        return [NLEDevice(device) for device in devices_data]

    async def get_device_status(self, device_id: str) -> NLEDeviceStatus:
        """Get device status."""
        endpoint = ENDPOINT_STATUS.format(device_id=device_id)
        response = await self._request("GET", endpoint)
        return NLEDeviceStatus(response)

    async def set_temperature(
        self,
        device_id: str,
        temperature: float,
        mode: str,
        scale: str = "C",
    ) -> dict[str, Any]:
        """Set target temperature."""
        endpoint = ENDPOINT_TEMPERATURE.format(device_id=device_id)
        data = {
            "value": temperature,
            "mode": mode,
            "scale": scale,
        }
        return await self._request("POST", endpoint, data)

    async def set_temperature_range(
        self,
        device_id: str,
        low: float,
        high: float,
        scale: str = "C",
    ) -> dict[str, Any]:
        """Set temperature range for heat-cool mode."""
        endpoint = ENDPOINT_TEMPERATURE_RANGE.format(device_id=device_id)
        data = {
            "low": low,
            "high": high,
            "scale": scale,
        }
        return await self._request("POST", endpoint, data)

    async def set_hvac_mode(self, device_id: str, mode: str) -> dict[str, Any]:
        """Set HVAC mode."""
        endpoint = ENDPOINT_MODE.format(device_id=device_id)
        data = {"mode": mode}
        return await self._request("POST", endpoint, data)

    async def set_away_mode(self, device_id: str, away: bool) -> dict[str, Any]:
        """Set away mode."""
        endpoint = ENDPOINT_AWAY.format(device_id=device_id)
        data = {"away": away}
        return await self._request("POST", endpoint, data)

    async def set_fan_mode(self, device_id: str, mode: str) -> dict[str, Any]:
        """Set fan mode."""
        endpoint = ENDPOINT_FAN.format(device_id=device_id)
        data = {"mode": mode}
        return await self._request("POST", endpoint, data)

    async def set_fan_timer(self, device_id: str, duration: int) -> dict[str, Any]:
        """Set fan timer duration in seconds."""
        endpoint = ENDPOINT_FAN.format(device_id=device_id)
        data = {"duration": duration}
        return await self._request("POST", endpoint, data)

    async def get_schedule(self, device_id: str) -> dict[str, Any]:
        """Get device schedule."""
        endpoint = ENDPOINT_SCHEDULE.format(device_id=device_id)
        return await self._request("GET", endpoint)

    async def set_schedule(
        self, device_id: str, schedule: dict[str, Any]
    ) -> dict[str, Any]:
        """Set device schedule."""
        endpoint = ENDPOINT_SCHEDULE.format(device_id=device_id)
        return await self._request("PUT", endpoint, schedule)

    async def validate_connection(self) -> bool:
        """Validate the API connection and credentials."""
        try:
            await self.get_devices()
            return True
        except NLEAuthenticationError:
            return False
        except NLEAPIError:
            return False
