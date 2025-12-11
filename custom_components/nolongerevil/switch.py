"""Switch platform for No Longer Evil integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import NLEDevice
from .const import DOMAIN
from .coordinator import NLEDataUpdateCoordinator
from .entity import NLEEntity
from .exceptions import NLEError

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the No Longer Evil switch platform."""
    coordinator: NLEDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SwitchEntity] = []

    for device in coordinator.devices.values():
        entities.append(NLEAwaySwitch(coordinator, device))

    async_add_entities(entities)


class NLEAwaySwitch(NLEEntity, SwitchEntity):
    """Representation of a No Longer Evil away mode switch."""

    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_name = "Away mode"

    def __init__(
        self,
        coordinator: NLEDataUpdateCoordinator,
        device: NLEDevice,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.id}_away_mode"

    @property
    def is_on(self) -> bool | None:
        """Return true if away mode is enabled."""
        status = self.device_status
        if status is None:
            return None
        return status.is_away

    @property
    def icon(self) -> str:
        """Return the icon."""
        if self.is_on:
            return "mdi:home-export-outline"
        return "mdi:home"

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on away mode."""
        try:
            await self.coordinator.async_set_away_mode(self._device_id, True)
        except NLEError as err:
            _LOGGER.error("Failed to enable away mode: %s", err)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off away mode."""
        try:
            await self.coordinator.async_set_away_mode(self._device_id, False)
        except NLEError as err:
            _LOGGER.error("Failed to disable away mode: %s", err)
