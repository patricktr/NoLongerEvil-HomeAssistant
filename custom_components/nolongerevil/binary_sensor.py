"""Binary sensor platform for No Longer Evil integration."""

from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
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
    """Set up the No Longer Evil binary sensor platform."""
    coordinator: NLEDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[BinarySensorEntity] = []

    for device in coordinator.devices.values():
        entities.extend([
            NLEHeatingBinarySensor(coordinator, device),
            NLECoolingBinarySensor(coordinator, device),
            NLEFanBinarySensor(coordinator, device),
            NLEAwayBinarySensor(coordinator, device),
        ])

    async_add_entities(entities)


class NLEHeatingBinarySensor(NLEEntity, BinarySensorEntity):
    """Representation of a No Longer Evil heating state binary sensor."""

    _attr_device_class = BinarySensorDeviceClass.HEAT
    _attr_name = "Heating"

    def __init__(
        self,
        coordinator: NLEDataUpdateCoordinator,
        device: NLEDevice,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.id}_heating"

    @property
    def is_on(self) -> bool | None:
        """Return true if heating is active."""
        status = self.device_status
        if status is None:
            return None
        return status.heater_active


class NLECoolingBinarySensor(NLEEntity, BinarySensorEntity):
    """Representation of a No Longer Evil cooling state binary sensor."""

    _attr_device_class = BinarySensorDeviceClass.COLD
    _attr_name = "Cooling"

    def __init__(
        self,
        coordinator: NLEDataUpdateCoordinator,
        device: NLEDevice,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.id}_cooling"

    @property
    def is_on(self) -> bool | None:
        """Return true if cooling is active."""
        status = self.device_status
        if status is None:
            return None
        return status.ac_active


class NLEFanBinarySensor(NLEEntity, BinarySensorEntity):
    """Representation of a No Longer Evil fan state binary sensor."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_name = "Fan running"

    def __init__(
        self,
        coordinator: NLEDataUpdateCoordinator,
        device: NLEDevice,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.id}_fan_running"

    @property
    def is_on(self) -> bool | None:
        """Return true if fan is running."""
        status = self.device_status
        if status is None:
            return None
        return status.fan_active

    @property
    def icon(self) -> str:
        """Return the icon."""
        if self.is_on:
            return "mdi:fan"
        return "mdi:fan-off"


class NLEAwayBinarySensor(NLEEntity, BinarySensorEntity):
    """Representation of a No Longer Evil away mode binary sensor."""

    _attr_device_class = BinarySensorDeviceClass.PRESENCE
    _attr_name = "Home"

    def __init__(
        self,
        coordinator: NLEDataUpdateCoordinator,
        device: NLEDevice,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.id}_home"

    @property
    def is_on(self) -> bool | None:
        """Return true if at home (not away)."""
        status = self.device_status
        if status is None:
            return None
        return not status.is_away

    @property
    def icon(self) -> str:
        """Return the icon."""
        if self.is_on:
            return "mdi:home"
        return "mdi:home-outline"
