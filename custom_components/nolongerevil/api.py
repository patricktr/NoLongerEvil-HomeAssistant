"""API client for the No Longer Evil API."""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import urlparse

import aiohttp
from aiohttp import ClientError, ClientResponseError, ClientTimeout, InvalidURL

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
    HOST_TYPE_SELF_HOSTED,
    SH_ENDPOINT_COMMAND,
    SH_ENDPOINT_DEVICES,
    SH_ENDPOINT_SCHEDULE,
    SH_ENDPOINT_STATUS,
)
from .exceptions import (
    NLEAPIError,
    NLEAuthenticationError,
    NLEConnectionError,
    NLEError,
    NLEIncompleteStatusError,
    NLERateLimitError,
)

_LOGGER = logging.getLogger(__name__)

DEFAULT_TIMEOUT = ClientTimeout(total=30)


def normalize_base_url(url: str, *, default_scheme: str = "https") -> str:
    """Validate and normalize a base URL.

    Adds a default scheme if the user omitted it (e.g. "192.168.1.50:8082"),
    rejects anything that isn't a well-formed http(s) URL, and strips any
    trailing slash. Raises ValueError on malformed input so callers can map it
    to a friendly "invalid_url" error instead of letting aiohttp raise an
    opaque InvalidURL that surfaces as a generic "unexpected error".
    """
    url = (url or "").strip()
    if not url:
        raise ValueError("Base URL must not be empty")
    if "://" not in url:
        url = f"{default_scheme}://{url}"
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"Invalid base URL: {url}")
    return url.rstrip("/")


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

    def __init__(self, data: dict[str, Any], *, source: str = "cloud") -> None:
        """Initialize the device status.

        ``source`` selects the response shape to parse: "cloud" for the hosted
        REST API (Nest-style nested ``shared.{serial}`` / ``device.{serial}``
        objects) or "control" for the flat self-hosted Control API payload.
        Both populate the same set of attributes so downstream entities are
        agnostic to which backend produced the status.
        """
        self._data = data
        if source == "control":
            self._parse_control_data()
        else:
            self._parse_data()

    def _parse_control_data(self) -> None:
        """Parse a status payload from the self-hosted Control API.

        Shape (GET /status?serial=...): a flat object keyed by serial rather
        than the cloud's nested ``shared.{serial}`` structure.
        """
        data = self._data

        self.device_id = data.get("serial", "")
        self.serial = data.get("serial", "")
        self.name = data.get("name")

        self.current_temperature = data.get("current_temperature")
        self.target_temperature = data.get("target_temperature")
        # Control API modes are heat | cool | range | off | emergency. The
        # hvac_mode property maps range -> heat-cool and emergency -> heat.
        # Do not default an omitted mode here; the coordinator restores the
        # last known value instead.
        self.target_temperature_type: str | None = data.get("mode")
        self.target_temperature_low = data.get("target_temperature_low")
        self.target_temperature_high = data.get("target_temperature_high")

        hvac = data.get("hvac") or {}
        self.heater_active = bool(hvac.get("heater", False))
        self.ac_active = bool(hvac.get("ac", False))
        self.fan_active = bool(hvac.get("fan", False))

        # The Control API exposes a fan timer rather than a fan_mode string.
        self.fan_mode = "on" if data.get("fan_timer_active") else "auto"

        self.is_away = bool(data.get("away", False))

        caps = data.get("capabilities") or {}
        self.can_cool = bool(caps.get("can_cool", False))
        self.can_heat = bool(caps.get("can_heat", True))

        self.temperature_scale = data.get("temperature_scale", "C")
        self.eco_mode_enabled = str(data.get("eco_mode", "")).lower() in (
            "on",
            "manual",
            "auto",
            "true",
            "1",
        )
        self.temperature_lock_enabled = bool(
            data.get("temperature_lock_enabled", False)
        )

        _LOGGER.debug(
            "Self-hosted device %s: mode=%s can_heat=%s can_cool=%s",
            self.serial,
            self.target_temperature_type,
            self.can_heat,
            self.can_cool,
        )

    def _parse_data(self) -> None:
        """Parse the status data from the API response."""
        # Get device metadata
        device_info = self._data.get("device", {})
        self.device_id: str = device_info.get("id", "")
        self.serial: str = device_info.get("serial", "")
        self.name: str | None = device_info.get("name")

        # Get state data
        state = self._data.get("state", {})
        if not isinstance(state, dict):
            raise NLEIncompleteStatusError(
                f"Status response for device {self.serial} is missing shared state"
            )

        # Find the shared state data
        shared_key = f"shared.{self.serial}"
        shared_obj = state.get(shared_key, {})
        if not isinstance(shared_obj, dict):
            raise NLEIncompleteStatusError(
                f"Status response for device {self.serial} is missing shared state"
            )
        shared_data = shared_obj.get("value", {})
        if not isinstance(shared_data, dict) or not shared_data:
            raise NLEIncompleteStatusError(
                f"Status response for device {self.serial} is missing shared state"
            )

        # Find the device settings data
        device_key = f"device.{self.serial}"
        device_obj = state.get(device_key, {})
        device_data = device_obj.get("value", {})

        # Current state
        self.current_temperature: float | None = shared_data.get("current_temperature")
        self.target_temperature: float | None = shared_data.get("target_temperature")
        # Do not default an omitted mode to "heat" here. The API occasionally
        # omits this field, so the coordinator restores the last known mode.
        self.target_temperature_type: str | None = shared_data.get(
            "target_temperature_type"
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

        _LOGGER.debug(
            "Device %s capabilities: can_heat=%s, can_cool=%s (raw shared data keys: %s)",
            self.serial,
            self.can_heat,
            self.can_cool,
            list(shared_data.keys()),
        )

        # Device settings
        self.temperature_scale: str = device_data.get("temperature_scale", "C")
        self.eco_mode_enabled: bool = device_data.get("eco_mode_enabled", False)
        self.temperature_lock_enabled: bool = device_data.get(
            "temperature_lock_enabled", False
        )

    @property
    def hvac_mode(self) -> str:
        """Return the current HVAC mode."""
        if self.target_temperature_type is None:
            # Statuses normally pass through the coordinator's mode latch first.
            return "heat"
        if self.target_temperature_type == "range":
            return "heat-cool"
        if self.target_temperature_type == "emergency":
            # Emergency heat is a safety mode — treat it as heat so HA
            # correctly shows the unit as heating rather than off.
            return "heat"
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


class NLEClientBase:
    """Shared session management for the cloud and self-hosted clients.

    Subclasses implement the same public surface (get_devices,
    get_device_status, set_*). This base only owns the aiohttp session so the
    coordinator and config flow can treat either backend uniformly.
    """

    _session: aiohttp.ClientSession | None
    _own_session: bool

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


class NLEApiClient(NLEClientBase):
    """Client for the hosted No Longer Evil REST API."""

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
                    # 403 is an authorization failure on a specific resource
                    # (e.g. shared device without write permission, temperature
                    # lock, transient gateway authz hiccup). The API key itself
                    # is still valid, so this must not trigger a re-auth flow.
                    raise NLEAPIError("Access denied to resource")

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

        except InvalidURL as err:
            # A malformed base URL (e.g. missing scheme) — treat as a
            # connection problem rather than letting the raw aiohttp error
            # bubble up as a generic "unexpected error" in the config flow.
            _LOGGER.error("Invalid URL: %s", err)
            raise NLEConnectionError(f"Invalid URL: {err}") from err
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


class NLESelfHostedClient(NLEClientBase):
    """Client for a self-hosted No Longer Evil server's Control API.

    The Control API (default port 8082) is unauthenticated, addresses devices
    by serial, and funnels every control action through a single ``/command``
    verb. This client adapts that surface to the same method signatures and
    ``NLEDevice`` / ``NLEDeviceStatus`` return types as :class:`NLEApiClient`
    so the coordinator and entities are backend-agnostic.
    """

    def __init__(
        self,
        base_url: str,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        """Initialize the self-hosted client."""
        self._base_url = base_url.rstrip("/")
        self._session = session
        self._own_session = session is None

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make a request against the Control API."""
        session = await self._get_session()
        url = f"{self._base_url}{endpoint}"

        try:
            async with session.request(
                method,
                url,
                params=params,
                json=data if data is not None else None,
            ) as response:
                if response.status == 404:
                    raise NLEAPIError("Resource not found")

                if response.status >= 400:
                    try:
                        error_data = await response.json()
                        error_msg = (
                            error_data.get("message")
                            or error_data.get("error")
                            or f"HTTP {response.status}"
                        )
                    except Exception:
                        error_msg = f"HTTP {response.status}"
                    raise NLEAPIError(f"API error: {error_msg}")

                payload = await response.json()
        except NLEError:
            # Already-mapped errors raised above — re-raise unchanged.
            raise
        except InvalidURL as err:
            _LOGGER.error("Invalid URL: %s", err)
            raise NLEConnectionError(f"Invalid URL: {err}") from err
        except asyncio.TimeoutError as err:
            _LOGGER.error("Request timeout")
            raise NLEConnectionError("Request timeout") from err
        except ClientError as err:
            _LOGGER.error("Connection error: %s", err)
            raise NLEConnectionError(f"Connection error: {err}") from err

        # The /command endpoint reports failures in-band as {"success": false}.
        if isinstance(payload, dict) and payload.get("success") is False:
            raise NLEAPIError(payload.get("message", "Command failed"))

        return payload

    async def _command(
        self, serial: str, command: str, value: Any
    ) -> dict[str, Any]:
        """Send a control command for a device serial."""
        return await self._request(
            "POST",
            SH_ENDPOINT_COMMAND,
            data={"serial": serial, "command": command, "value": value},
        )

    async def get_devices(self) -> list[NLEDevice]:
        """Get list of devices."""
        response = await self._request("GET", SH_ENDPOINT_DEVICES)
        devices = []
        for device in response.get("devices", []):
            serial = device.get("serial", "")
            # The Control API keys devices by serial; map it onto the same
            # NLEDevice shape the cloud client produces (id == serial).
            devices.append(
                NLEDevice(
                    {
                        "id": serial,
                        "serial": serial,
                        "name": device.get("name"),
                        "accessType": "owner",
                    }
                )
            )
        return devices

    async def get_device_status(self, device_id: str) -> NLEDeviceStatus:
        """Get device status by serial."""
        response = await self._request(
            "GET", SH_ENDPOINT_STATUS, params={"serial": device_id}
        )
        return NLEDeviceStatus(response, source="control")

    async def set_temperature(
        self,
        device_id: str,
        temperature: float,
        mode: str,
        scale: str = "C",
    ) -> dict[str, Any]:
        """Set target temperature (Control API expects Celsius)."""
        return await self._command(device_id, "set_temperature", temperature)

    async def set_temperature_range(
        self,
        device_id: str,
        low: float,
        high: float,
        scale: str = "C",
    ) -> dict[str, Any]:
        """Set temperature range for heat-cool mode."""
        return await self._command(
            device_id, "set_temperature", {"high": high, "low": low}
        )

    async def set_hvac_mode(self, device_id: str, mode: str) -> dict[str, Any]:
        """Set HVAC mode (heat/cool/heat-cool/off pass straight through)."""
        return await self._command(device_id, "set_mode", mode)

    async def set_away_mode(self, device_id: str, away: bool) -> dict[str, Any]:
        """Set away mode."""
        return await self._command(device_id, "set_away", bool(away))

    async def set_fan_mode(self, device_id: str, mode: str) -> dict[str, Any]:
        """Set fan mode. The Control API has no explicit "off"; map it to auto."""
        value = "auto" if mode in ("auto", "off") else mode
        return await self._command(device_id, "set_fan", value)

    async def set_fan_timer(self, device_id: str, duration: int) -> dict[str, Any]:
        """Set fan timer duration in seconds."""
        return await self._command(device_id, "set_fan", duration)

    async def get_schedule(self, device_id: str) -> dict[str, Any]:
        """Get device schedule."""
        return await self._request(
            "GET", SH_ENDPOINT_SCHEDULE, params={"serial": device_id}
        )

    async def set_schedule(
        self, device_id: str, schedule: dict[str, Any]
    ) -> dict[str, Any]:
        """Set device schedule."""
        return await self._command(device_id, "set_schedule", schedule)

    async def validate_connection(self) -> bool:
        """Validate the connection to the self-hosted server."""
        try:
            await self.get_devices()
            return True
        except NLEError:
            return False


def create_client(
    host_type: str,
    api_key: str,
    base_url: str,
    session: aiohttp.ClientSession | None = None,
) -> NLEClientBase:
    """Build the appropriate API client for the configured host type."""
    if host_type == HOST_TYPE_SELF_HOSTED:
        return NLESelfHostedClient(base_url, session)
    return NLEApiClient(api_key, session, base_url)
