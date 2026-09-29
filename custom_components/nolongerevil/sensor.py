"""Sensor platform for No Longer Evil integration."""

from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import NLEDevice
from .const import DOMAIN
from .coordinator import NLEDataUpdateCoordinator
from .entity import NLEEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the No Longer Evil sensor platform."""
    coordinator: NLEDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SensorEntity] = []

    for device in coordinator.devices.values():
        entities.extend([
            NLETemperatureSensor(coordinator, device),
            NLETargetTemperatureSensor(coordinator, device),
            NLEHVACActionSensor(coordinator, device),
            NLELastUpdateSensor(coordinator, device),
        ])

    async_add_entities(entities)


class NLETemperatureSensor(NLEEntity, SensorEntity):
    """Representation of a No Longer Evil temperature sensor."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_name = "Current temperature"

    def __init__(
        self,
        coordinator: NLEDataUpdateCoordinator,
        device: NLEDevice,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.id}_temperature"

    @property
    def native_value(self) -> float | None:
        """Return the current temperature."""
        status = self.device_status
        if status is None:
            return None
        return status.current_temperature


class NLETargetTemperatureSensor(NLEEntity, SensorEntity):
    """Representation of a No Longer Evil target temperature sensor."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_name = "Target temperature"

    def __init__(
        self,
        coordinator: NLEDataUpdateCoordinator,
        device: NLEDevice,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.id}_target_temperature"

    @property
    def native_value(self) -> float | None:
        """Return the target temperature."""
        status = self.device_status
        if status is None:
            return None
        return status.target_temperature

    @property
    def extra_state_attributes(self) -> dict[str, any] | None:
        """Return additional state attributes."""
        status = self.device_status
        if status is None:
            return None

        attrs = {
            "mode": status.target_temperature_type,
        }

        if status.target_temperature_type == "range":
            attrs["low"] = status.target_temperature_low
            attrs["high"] = status.target_temperature_high

        return attrs


class NLEHVACActionSensor(NLEEntity, SensorEntity):
    """Representation of a No Longer Evil HVAC action sensor."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["heating", "cooling", "fan", "idle", "off"]
    _attr_name = "HVAC action"

    def __init__(
        self,
        coordinator: NLEDataUpdateCoordinator,
        device: NLEDevice,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.id}_hvac_action"

    @property
    def native_value(self) -> str | None:
        """Return the current HVAC action."""
        status = self.device_status
        if status is None:
            return None
        return status.hvac_action

    @property
    def icon(self) -> str:
        """Return the icon based on the current action."""
        action = self.native_value
        if action == "heating":
            return "mdi:fire"
        elif action == "cooling":
            return "mdi:snowflake"
        elif action == "fan":
            return "mdi:fan"
        return "mdi:thermostat"


class NLELastUpdateSensor(NLEEntity, SensorEntity):
    """When the device last returned a complete status.

    While updates fail, the other entities keep showing the last known state
    for the configured unavailable-after window, so their values can be
    older than they look. This sensor makes that age visible, and stays
    available through outages so automations can alert on it.
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_name = "Last successful update"

    def __init__(
        self,
        coordinator: NLEDataUpdateCoordinator,
        device: NLEDevice,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.id}_last_update"

    @property
    def available(self) -> bool:
        """Return True once the device has updated successfully at least once."""
        return self.native_value is not None

    @property
    def native_value(self) -> datetime | None:
        """Return when the device last returned a complete status."""
        return self.coordinator.get_last_success(self._device_id)
