"""Base entity for No Longer Evil integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import NLEDevice
from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import NLEDataUpdateCoordinator


class NLEEntity(CoordinatorEntity[NLEDataUpdateCoordinator]):
    """Base entity for No Longer Evil devices."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: NLEDataUpdateCoordinator,
        device: NLEDevice,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._device = device
        self._device_id = device.id

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.id)},
            name=device.display_name,
            manufacturer=MANUFACTURER,
            model=MODEL,
            serial_number=device.serial,
        )

    @property
    def device_status(self):
        """Return the current device status."""
        return self.coordinator.get_device_status(self._device_id)

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return super().available and self.device_status is not None
