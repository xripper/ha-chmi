"""Shared entity base classes."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import ATTRIBUTION, DOMAIN
from .coordinator import ChmiStationCoordinator

MANUFACTURER = "Český hydrometeorologický ústav"
STATION_LIST_URL = (
    "https://www.chmi.cz/aktualni-situace/aktualni-stav-pocasi/ceska-republika"
)


def station_device_info(
    entry_id: str, coordinator: ChmiStationCoordinator
) -> DeviceInfo:
    """Device representing the configured station.

    The device and every entity are identified by the config entry, not by the
    station, so that switching the station keeps the history of the entities.
    """
    station = coordinator.data.station
    return DeviceInfo(
        identifiers={(DOMAIN, entry_id)},
        name=station.name,
        manufacturer=MANUFACTURER,
        model="Meteorologická stanice",
        serial_number=station.station_id,
        configuration_url=STATION_LIST_URL,
    )


class ChmiEntity(CoordinatorEntity[DataUpdateCoordinator]):
    """Base class for all entities of this integration."""

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        entry_id: str,
        station_coordinator: ChmiStationCoordinator,
    ) -> None:
        """Attach the entity to the station device."""
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._station_coordinator = station_coordinator
        self._attr_device_info = station_device_info(entry_id, station_coordinator)


class ChmiStationEntity(ChmiEntity):
    """Base class for entities fed by station observations."""

    def __init__(self, coordinator: ChmiStationCoordinator, entry_id: str) -> None:
        """Initialise a station entity."""
        super().__init__(coordinator, entry_id, coordinator)
        self.coordinator: ChmiStationCoordinator = coordinator
