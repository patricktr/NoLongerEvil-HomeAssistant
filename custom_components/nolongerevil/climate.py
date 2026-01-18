"""Climate platform for No Longer Evil integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    FAN_MODE_AUTO,
    FAN_MODE_OFF,
    FAN_MODE_ON,
    HVAC_MODE_MAP,
    HVAC_MODE_REVERSE_MAP,
)
from .coordinator import NLEDataUpdateCoordinator
from .entity import NLEEntity
from .exceptions import NLEError

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the No Longer Evil climate platform."""
    coordinator: NLEDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = [
        NLEClimate(coordinator, device)
        for device in coordinator.devices.values()
    ]

    async_add_entities(entities)


class NLEClimate(NLEEntity, ClimateEntity):
    """Representation of a No Longer Evil thermostat."""

    _attr_name = None  # Use device name
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_precision = 0.5
    _attr_target_temperature_step = 0.5
    _attr_min_temp = 9.0  # 48F
    _attr_max_temp = 32.0  # 90F

    def __init__(
        self,
        coordinator: NLEDataUpdateCoordinator,
        device,
    ) -> None:
        """Initialize the climate entity."""
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.id}_climate"

        # Determine supported features based on device capabilities
        self._attr_supported_features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.FAN_MODE
            | ClimateEntityFeature.PRESET_MODE
            | ClimateEntityFeature.TURN_OFF
            | ClimateEntityFeature.TURN_ON
        )

        # Initialize hvac modes (will be updated in _handle_coordinator_update)
        self._attr_hvac_modes = [HVACMode.OFF]
        self._attr_fan_modes = [FAN_MODE_AUTO, FAN_MODE_ON, FAN_MODE_OFF]

        # Add target temp range support for heat-cool mode
        self._attr_supported_features |= ClimateEntityFeature.TARGET_TEMPERATURE_RANGE

    @property
    def hvac_modes(self) -> list[HVACMode]:
        """Return the list of available HVAC modes."""
        status = self.device_status
        if status is None:
            return [HVACMode.OFF]

        modes = [HVACMode.OFF]
        if status.can_heat:
            modes.append(HVACMode.HEAT)
        if status.can_cool:
            modes.append(HVACMode.COOL)
        if status.can_heat and status.can_cool:
            modes.append(HVACMode.HEAT_COOL)

        return modes

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Return the current HVAC mode."""
        status = self.device_status
        if status is None:
            return None

        api_mode = status.hvac_mode
        if api_mode == "heat":
            return HVACMode.HEAT
        elif api_mode == "cool":
            return HVACMode.COOL
        elif api_mode == "heat-cool":
            return HVACMode.HEAT_COOL
        elif api_mode == "off":
            return HVACMode.OFF
        return HVACMode.OFF

    @property
    def hvac_action(self) -> HVACAction | None:
        """Return the current HVAC action."""
        status = self.device_status
        if status is None:
            return None

        action = status.hvac_action
        if action == "heating":
            return HVACAction.HEATING
        elif action == "cooling":
            return HVACAction.COOLING
        elif action == "fan":
            return HVACAction.FAN
        elif self.hvac_mode == HVACMode.OFF:
            return HVACAction.OFF
        return HVACAction.IDLE

    @property
    def current_temperature(self) -> float | None:
        """Return the current temperature."""
        status = self.device_status
        if status is None:
            return None
        return status.current_temperature

    @property
    def target_temperature(self) -> float | None:
        """Return the target temperature."""
        status = self.device_status
        if status is None:
            return None

        # Only return single target temp for heat or cool mode
        if status.target_temperature_type in ("heat", "cool"):
            return status.target_temperature
        return None

    @property
    def target_temperature_low(self) -> float | None:
        """Return the low target temperature for range mode."""
        status = self.device_status
        if status is None:
            return None

        if status.target_temperature_type == "range":
            return status.target_temperature_low
        return None

    @property
    def target_temperature_high(self) -> float | None:
        """Return the high target temperature for range mode."""
        status = self.device_status
        if status is None:
            return None

        if status.target_temperature_type == "range":
            return status.target_temperature_high
        return None

    @property
    def fan_mode(self) -> str | None:
        """Return the current fan mode."""
        status = self.device_status
        if status is None:
            return None
        return status.fan_mode

    @property
    def preset_mode(self) -> str | None:
        """Return the current preset mode."""
        status = self.device_status
        if status is None:
            return None
        if status.is_away:
            return "away"
        if status.eco_mode_enabled:
            return "eco"
        return "home"

    @property
    def preset_modes(self) -> list[str]:
        """Return the list of available preset modes."""
        return ["home", "away", "eco"]

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set the HVAC mode."""
        try:
            if hvac_mode == HVACMode.HEAT:
                await self.coordinator.async_set_hvac_mode(self._device_id, "heat")
            elif hvac_mode == HVACMode.COOL:
                await self.coordinator.async_set_hvac_mode(self._device_id, "cool")
            elif hvac_mode == HVACMode.HEAT_COOL:
                await self.coordinator.async_set_hvac_mode(self._device_id, "heat-cool")
            elif hvac_mode == HVACMode.OFF:
                await self.coordinator.async_set_hvac_mode(self._device_id, "off")
        except NLEError as err:
            _LOGGER.error("Failed to set HVAC mode: %s", err)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature."""
        try:
            status = self.device_status
            if status is None:
                return

            # Handle temperature range
            low = kwargs.get("target_temp_low")
            high = kwargs.get("target_temp_high")
            if low is not None and high is not None:
                await self.coordinator.async_set_temperature_range(
                    self._device_id, low, high
                )
                return

            # Handle single temperature
            temperature = kwargs.get(ATTR_TEMPERATURE)
            if temperature is not None:
                # Determine mode based on current setting
                mode = status.target_temperature_type
                if mode == "range":
                    mode = "heat"  # Default to heat if in range mode
                await self.coordinator.async_set_temperature(
                    self._device_id, temperature, mode
                )

            # Handle HVAC mode change with temperature
            hvac_mode = kwargs.get("hvac_mode")
            if hvac_mode is not None:
                await self.async_set_hvac_mode(hvac_mode)

        except NLEError as err:
            _LOGGER.error("Failed to set temperature: %s", err)

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set the fan mode."""
        try:
            await self.coordinator.async_set_fan_mode(self._device_id, fan_mode)
        except NLEError as err:
            _LOGGER.error("Failed to set fan mode: %s", err)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set the preset mode."""
        try:
            if preset_mode == "away":
                await self.coordinator.async_set_away_mode(self._device_id, True)
            elif preset_mode in ("home", "eco"):
                await self.coordinator.async_set_away_mode(self._device_id, False)
        except NLEError as err:
            _LOGGER.error("Failed to set preset mode: %s", err)

    async def async_turn_on(self) -> None:
        """Turn on the thermostat."""
        status = self.device_status
        if status is None:
            return

        # Turn on to heat or cool based on capabilities
        if status.can_heat:
            await self.async_set_hvac_mode(HVACMode.HEAT)
        elif status.can_cool:
            await self.async_set_hvac_mode(HVACMode.COOL)

    async def async_turn_off(self) -> None:
        """Turn off the thermostat."""
        await self.async_set_hvac_mode(HVACMode.OFF)
