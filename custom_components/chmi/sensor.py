"""Sensor platform for the ČHMÚ integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    DEGREE,
    PERCENTAGE,
    UnitOfIrradiance,
    UnitOfLength,
    UnitOfPrecipitationDepth,
    UnitOfPressure,
    UnitOfSpeed,
    UnitOfTemperature,
    UnitOfTime,
    UnitOfVolumetricFlux,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ChmiConfigEntry
from .api.alerts import filter_alerts
from .const import QUALITY_LABELS
from .coordinator import (
    ChmiAlertsCoordinator,
    ChmiRadarCoordinator,
    ChmiStationCoordinator,
    ChmiTextForecastCoordinator,
)
from .entity import ChmiEntity, ChmiStationEntity
from .wmo import (
    octas_to_percent,
    present_weather_condition,
    visibility_meters,
    wind_bearing_label,
)


@dataclass(frozen=True, kw_only=True)
class ChmiSensorDescription(SensorEntityDescription):
    """Description of a sensor derived from one measured element."""

    element: str
    convert: Callable[[float], float | None] | None = None


SENSOR_TYPES: tuple[ChmiSensorDescription, ...] = (
    ChmiSensorDescription(
        key="temperature",
        element="T",
        translation_key="temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    ChmiSensorDescription(
        key="temperature_max",
        element="TMA",
        translation_key="temperature_max",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    ChmiSensorDescription(
        key="temperature_min",
        element="TMI",
        translation_key="temperature_min",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    ChmiSensorDescription(
        key="temperature_ground",
        element="TPM",
        translation_key="temperature_ground",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    ChmiSensorDescription(
        key="dew_point",
        element="Td",
        translation_key="dew_point",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    ChmiSensorDescription(
        key="humidity",
        element="H",
        translation_key="humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    ChmiSensorDescription(
        key="pressure",
        element="P",
        translation_key="pressure",
        device_class=SensorDeviceClass.PRESSURE,
        native_unit_of_measurement=UnitOfPressure.HPA,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    ChmiSensorDescription(
        key="pressure_sea_level",
        element="P_hm",
        translation_key="pressure_sea_level",
        device_class=SensorDeviceClass.PRESSURE,
        native_unit_of_measurement=UnitOfPressure.HPA,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    ChmiSensorDescription(
        key="vapour_pressure",
        element="E",
        translation_key="vapour_pressure",
        device_class=SensorDeviceClass.PRESSURE,
        native_unit_of_measurement=UnitOfPressure.HPA,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
    ),
    ChmiSensorDescription(
        key="wind_speed",
        element="F",
        translation_key="wind_speed",
        device_class=SensorDeviceClass.WIND_SPEED,
        native_unit_of_measurement=UnitOfSpeed.METERS_PER_SECOND,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    ChmiSensorDescription(
        key="wind_speed_average",
        element="Fprum",
        translation_key="wind_speed_average",
        device_class=SensorDeviceClass.WIND_SPEED,
        native_unit_of_measurement=UnitOfSpeed.METERS_PER_SECOND,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
    ),
    ChmiSensorDescription(
        key="wind_gust",
        element="Fmax",
        translation_key="wind_gust",
        device_class=SensorDeviceClass.WIND_SPEED,
        native_unit_of_measurement=UnitOfSpeed.METERS_PER_SECOND,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    ChmiSensorDescription(
        key="wind_bearing",
        element="D",
        translation_key="wind_bearing",
        native_unit_of_measurement=DEGREE,
        suggested_display_precision=0,
    ),
    ChmiSensorDescription(
        key="wind_gust_bearing",
        element="Dmax",
        translation_key="wind_gust_bearing",
        native_unit_of_measurement=DEGREE,
        suggested_display_precision=0,
        entity_registry_enabled_default=False,
    ),
    ChmiSensorDescription(
        key="precipitation_10m",
        element="SRA10M",
        translation_key="precipitation_10m",
        device_class=SensorDeviceClass.PRECIPITATION,
        native_unit_of_measurement=UnitOfPrecipitationDepth.MILLIMETERS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    ChmiSensorDescription(
        key="precipitation_1h",
        element="SRA1H",
        translation_key="precipitation_1h",
        device_class=SensorDeviceClass.PRECIPITATION,
        native_unit_of_measurement=UnitOfPrecipitationDepth.MILLIMETERS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    ChmiSensorDescription(
        key="snow_depth",
        element="SCEa",
        translation_key="snow_depth",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.CENTIMETERS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    ChmiSensorDescription(
        key="sunshine_10m",
        element="SSV10M",
        translation_key="sunshine_10m",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    ChmiSensorDescription(
        key="sunshine_1h",
        element="SSV1H",
        translation_key="sunshine_1h",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        # published in tenths of an hour
        convert=lambda value: value * 6,
    ),
    ChmiSensorDescription(
        key="global_radiation",
        element="RGLB10",
        translation_key="global_radiation",
        device_class=SensorDeviceClass.IRRADIANCE,
        native_unit_of_measurement=UnitOfIrradiance.WATTS_PER_SQUARE_METER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    ChmiSensorDescription(
        key="diffuse_radiation",
        element="RDIF10",
        translation_key="diffuse_radiation",
        device_class=SensorDeviceClass.IRRADIANCE,
        native_unit_of_measurement=UnitOfIrradiance.WATTS_PER_SQUARE_METER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        entity_registry_enabled_default=False,
    ),
    ChmiSensorDescription(
        key="cloud_coverage",
        element="N",
        translation_key="cloud_coverage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        # published in eighths of the sky; code 9 means "sky obscured"
        convert=octas_to_percent,
    ),
    ChmiSensorDescription(
        key="visibility",
        element="VV",
        translation_key="visibility",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.METERS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        convert=visibility_meters,
    ),
    ChmiSensorDescription(
        key="soil_temperature_5",
        element="T05",
        translation_key="soil_temperature_5",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
    ),
    ChmiSensorDescription(
        key="soil_temperature_10",
        element="T10",
        translation_key="soil_temperature_10",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
    ),
    ChmiSensorDescription(
        key="soil_temperature_20",
        element="T20",
        translation_key="soil_temperature_20",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
    ),
    ChmiSensorDescription(
        key="soil_temperature_50",
        element="T50",
        translation_key="soil_temperature_50",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
    ),
    ChmiSensorDescription(
        key="soil_temperature_100",
        element="T100",
        translation_key="soil_temperature_100",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ChmiConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the ČHMÚ sensors."""
    runtime = entry.runtime_data
    station = runtime.station
    available = station.data.elements

    entry_id = entry.entry_id
    entities: list[SensorEntity] = [
        ChmiStationSensor(station, entry_id, description)
        for description in SENSOR_TYPES
        if description.element in available
    ]

    if "ww" in available:
        entities.append(ChmiPresentWeatherSensor(station, entry_id))

    if runtime.radar is not None:
        entities.append(ChmiRadarRainSensor(runtime.radar, entry_id, station))
    if runtime.alerts is not None:
        entities.append(
            ChmiAlertCountSensor(runtime.alerts, entry_id, station, runtime.region)
        )
    if runtime.text_forecast is not None:
        entities.append(
            ChmiTextForecastSensor(runtime.text_forecast, entry_id, station)
        )

    async_add_entities(entities)


class ChmiStationSensor(ChmiStationEntity, SensorEntity):
    """A sensor reporting one measured element."""

    entity_description: ChmiSensorDescription

    def __init__(
        self,
        coordinator: ChmiStationCoordinator,
        entry_id: str,
        description: ChmiSensorDescription,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, entry_id)
        self.entity_description = description
        self._attr_unique_id = f"{entry_id}_{description.key}"

    @property
    def available(self) -> bool:
        """Whether the element currently has a published value."""
        return (
            super().available
            and self.entity_description.element in self.coordinator.data.observations
        )

    @property
    def native_value(self) -> float | None:
        """Return the measured value."""
        observation = self.coordinator.data.observations.get(
            self.entity_description.element
        )
        if observation is None or observation.value is None:
            return None
        if self.entity_description.convert is not None:
            return self.entity_description.convert(observation.value)
        return observation.value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return metadata of the underlying measurement."""
        observation = self.coordinator.data.observations.get(
            self.entity_description.element
        )
        if observation is None:
            return {}
        attributes: dict[str, Any] = {
            "element": observation.element,
            "measured_at": observation.measured_at.isoformat(),
            "quality": QUALITY_LABELS.get(observation.quality, "unknown"),
            "station": self.coordinator.data.station.name,
        }
        if observation.flag:
            attributes["flag"] = observation.flag
        if self.entity_description.element in ("D", "Dmax"):
            attributes["bearing"] = wind_bearing_label(observation.value)
        if self.entity_description.element == "VV":
            attributes["wmo_code"] = observation.value
        return attributes


class ChmiPresentWeatherSensor(ChmiStationEntity, SensorEntity):
    """The present weather code reported by the station."""

    _attr_translation_key = "present_weather"

    def __init__(self, coordinator: ChmiStationCoordinator, entry_id: str) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_present_weather"

    @property
    def available(self) -> bool:
        """Whether a present weather code is published."""
        return super().available and "ww" in self.coordinator.data.observations

    @property
    def native_value(self) -> int | None:
        """Return the WMO present weather code."""
        value = self.coordinator.data.value("ww")
        return int(value) if value is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the derived weather condition."""
        observation = self.coordinator.data.observations.get("ww")
        if observation is None:
            return {}
        return {
            "condition": present_weather_condition(observation.value),
            "measured_at": observation.measured_at.isoformat(),
        }


class ChmiRadarRainSensor(ChmiEntity, SensorEntity):
    """Rain rate over the Home Assistant location, read from the radar."""

    _attr_translation_key = "radar_rain_rate"
    _attr_device_class = SensorDeviceClass.PRECIPITATION_INTENSITY
    _attr_native_unit_of_measurement = UnitOfVolumetricFlux.MILLIMETERS_PER_HOUR
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    def __init__(
        self,
        coordinator: ChmiRadarCoordinator,
        entry_id: str,
        station: ChmiStationCoordinator,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, entry_id, station)
        self.coordinator: ChmiRadarCoordinator = coordinator
        self._attr_unique_id = f"{entry_id}_radar_rain_rate"

    @property
    def available(self) -> bool:
        """Whether the home location lies inside the radar coverage."""
        state = self.coordinator.data
        return super().available and state is not None and state.home_sample.in_coverage

    @property
    def native_value(self) -> float | None:
        """Return the rain rate in mm/h."""
        state = self.coordinator.data
        return state.home_sample.rain_rate if state else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return reflectivity and frame metadata."""
        state = self.coordinator.data
        if state is None:
            return {}
        return {
            "frame_time": state.frame.frame_time.isoformat(),
            "product": state.frame.variant,
            "reflectivity_dbz": state.home_sample.dbz,
            "aloft_only": state.home_sample.aloft_only,
        }


class ChmiAlertCountSensor(ChmiEntity, SensorEntity):
    """Number of active ČHMÚ warnings for the home region."""

    _attr_translation_key = "alert_count"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: ChmiAlertsCoordinator,
        entry_id: str,
        station: ChmiStationCoordinator,
        region: str | None,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, entry_id, station)
        self.coordinator: ChmiAlertsCoordinator = coordinator
        self._region = region
        self._attr_unique_id = f"{entry_id}_alert_count"

    def _alerts(self, *, upcoming: bool = False) -> list:
        """Return the warnings relevant for the home region."""
        if not self.coordinator.data:
            return []
        return filter_alerts(
            self.coordinator.data,
            region=self._region,
            language=self.hass.config.language,
            upcoming=upcoming,
        )

    @property
    def native_value(self) -> int:
        """Return the number of active warnings."""
        return len(self._alerts())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the list of active warnings."""
        return {
            "region": self._region,
            "upcoming": [
                {
                    "event": alert.event,
                    "severity": alert.severity,
                    "onset": alert.onset.isoformat() if alert.onset else None,
                    "expires": alert.expires.isoformat() if alert.expires else None,
                }
                for alert in self._alerts(upcoming=True)
            ],
            "alerts": [
                {
                    "event": alert.event,
                    "severity": alert.severity,
                    "urgency": alert.urgency,
                    "certainty": alert.certainty,
                    "awareness_level": alert.awareness_level,
                    "awareness_type": alert.awareness_type,
                    "onset": alert.onset.isoformat() if alert.onset else None,
                    "expires": alert.expires.isoformat() if alert.expires else None,
                    "description": alert.description,
                    "instruction": alert.instruction,
                    "areas": list(alert.areas),
                }
                for alert in self._alerts()
            ],
        }


class ChmiTextForecastSensor(ChmiEntity, SensorEntity):
    """The regional text forecast issued by ČHMÚ."""

    _attr_translation_key = "text_forecast"

    def __init__(
        self,
        coordinator: ChmiTextForecastCoordinator,
        entry_id: str,
        station: ChmiStationCoordinator,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, entry_id, station)
        self.coordinator: ChmiTextForecastCoordinator = coordinator
        self._attr_unique_id = f"{entry_id}_text_forecast"

    @property
    def native_value(self) -> str | None:
        """Return the short summary of today's forecast."""
        today = (self.coordinator.data or {}).get("0")
        if today is None:
            return None
        # State values are limited to 255 characters.
        return ((today.summary or today.headline or "")[:255]) or None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the full text of every published forecast day."""
        forecasts = self.coordinator.data or {}
        attributes: dict[str, Any] = {"region_code": self.coordinator.code}
        for day, forecast in sorted(forecasts.items()):
            key = "today" if day == "0" else "tomorrow" if day == "1" else f"day_{day}"
            attributes[key] = {
                "place": forecast.place,
                "headline": forecast.headline,
                "issued": forecast.issued.isoformat() if forecast.issued else None,
                "valid_from": forecast.start.isoformat() if forecast.start else None,
                "valid_to": forecast.end.isoformat() if forecast.end else None,
                "sections": [
                    {
                        "name": section.name,
                        "headline": section.headline,
                        "text": section.text,
                    }
                    for section in forecast.sections
                ],
            }
        return attributes
