"""Camera platform showing the ČHMÚ radar composite over a map of Czechia."""

from __future__ import annotations

from typing import Any

from homeassistant.components.camera import Camera
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ChmiConfigEntry
from .const import ATTRIBUTION, RADAR_UPDATE_INTERVAL
from .coordinator import ChmiRadarCoordinator, ChmiStationCoordinator
from .entity import station_device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ChmiConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the radar camera when the entry has the radar enabled."""
    runtime = entry.runtime_data
    if runtime.radar is None:
        return
    async_add_entities(
        [ChmiRadarCamera(runtime.radar, entry.entry_id, runtime.station)]
    )


class ChmiRadarCamera(Camera):
    """The newest radar composite rendered over the regions of Czechia."""

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION
    _attr_translation_key = "radar"

    def __init__(
        self,
        coordinator: ChmiRadarCoordinator,
        entry_id: str,
        station: ChmiStationCoordinator,
    ) -> None:
        """Initialise the camera."""
        super().__init__()
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry_id}_radar"
        self._attr_device_info = station_device_info(entry_id, station)
        self._attr_frame_interval = RADAR_UPDATE_INTERVAL.total_seconds()

    async def async_added_to_hass(self) -> None:
        """Follow the radar coordinator."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._coordinator.async_add_listener(self.async_write_ha_state)
        )

    @property
    def available(self) -> bool:
        """Whether a radar frame is available."""
        return (
            self._coordinator.last_update_success and self._coordinator.data is not None
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the time and product of the displayed frame."""
        state = self._coordinator.data
        if state is None:
            return {}
        return {
            "frame_time": state.frame.frame_time.isoformat(),
            "product": state.frame.variant,
        }

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return the rendered composite."""
        state = self._coordinator.data
        return state.image if state else None
