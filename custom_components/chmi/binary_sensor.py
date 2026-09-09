"""Binary sensor platform exposing active ČHMÚ warnings."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ChmiConfigEntry
from .api.alerts import Alert, filter_alerts
from .coordinator import ChmiAlertsCoordinator, ChmiStationCoordinator
from .entity import ChmiEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ChmiConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the warning binary sensor when warnings are enabled."""
    runtime = entry.runtime_data
    if runtime.alerts is None:
        return
    async_add_entities(
        [
            ChmiAlertBinarySensor(
                runtime.alerts, entry.entry_id, runtime.station, runtime.region
            )
        ]
    )


class ChmiAlertBinarySensor(ChmiEntity, BinarySensorEntity):
    """On while a ČHMÚ warning is valid for the home region."""

    _attr_translation_key = "alert"
    _attr_device_class = BinarySensorDeviceClass.SAFETY

    def __init__(
        self,
        coordinator: ChmiAlertsCoordinator,
        entry_id: str,
        station: ChmiStationCoordinator,
        region: str | None,
    ) -> None:
        """Initialise the binary sensor."""
        super().__init__(coordinator, entry_id, station)
        self.coordinator: ChmiAlertsCoordinator = coordinator
        self._region = region
        self._attr_unique_id = f"{entry_id}_alert"

    def _alerts(self) -> list[Alert]:
        """Active warnings for the home region, most severe first."""
        if not self.coordinator.data:
            return []
        alerts = filter_alerts(
            self.coordinator.data,
            region=self._region,
            language=self.hass.config.language,
        )
        order = {"extreme": 0, "severe": 1, "moderate": 2, "minor": 3}
        return sorted(alerts, key=lambda alert: order.get(alert.severity.lower(), 9))

    @property
    def is_on(self) -> bool:
        """Whether any warning is active."""
        return bool(self._alerts())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the most severe warning in a meteoalarm compatible shape."""
        alerts = self._alerts()
        if not alerts:
            return {"region": self._region, "count": 0}
        alert = alerts[0]
        return {
            "region": self._region,
            "count": len(alerts),
            "event": alert.event,
            "headline": alert.event,
            "description": alert.description,
            "instruction": alert.instruction,
            "severity": alert.severity,
            "urgency": alert.urgency,
            "certainty": alert.certainty,
            "awareness_level": alert.awareness_level,
            "awareness_type": alert.awareness_type,
            "onset": alert.onset.isoformat() if alert.onset else None,
            "expires": alert.expires.isoformat() if alert.expires else None,
            "areas": list(alert.areas),
            "web": alert.web,
            "attribution": self._attr_attribution,
        }
