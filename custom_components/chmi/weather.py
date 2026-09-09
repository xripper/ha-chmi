"""Weather platform for the ČHMÚ integration.

The station files contain measured values only; ČHMÚ publishes no numeric
forecast that a Home Assistant instance could reasonably download (the ALADIN
model is only available as multi-megabyte GRIB2 files), so this entity reports
the current conditions and offers no forecast.

The reported condition comes from the station's present weather code where the
station has one.  Otherwise it is derived - in this order - from measured
precipitation and the radar echo above the station's own coordinates, from the
reported cloud cover, from the sunshine duration and from global radiation.
Stations that measure none of these leave the condition unknown instead of
guessing.
"""

from __future__ import annotations

import math

from astral import LocationInfo
from astral.sun import elevation as solar_elevation
from homeassistant.components.weather import (
    ATTR_CONDITION_CLEAR_NIGHT,
    ATTR_CONDITION_CLOUDY,
    ATTR_CONDITION_FOG,
    ATTR_CONDITION_LIGHTNING_RAINY,
    ATTR_CONDITION_PARTLYCLOUDY,
    ATTR_CONDITION_POURING,
    ATTR_CONDITION_RAINY,
    ATTR_CONDITION_SNOWY,
    ATTR_CONDITION_SNOWY_RAINY,
    ATTR_CONDITION_SUNNY,
    WeatherEntity,
)
from homeassistant.const import (
    UnitOfLength,
    UnitOfPrecipitationDepth,
    UnitOfPressure,
    UnitOfSpeed,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import ChmiConfigEntry
from .coordinator import ChmiRadarCoordinator, ChmiStationCoordinator
from .entity import ChmiStationEntity
from .wmo import octas_to_percent, present_weather_condition, visibility_meters

# Rain rate in mm/h from which the condition is reported as "pouring"
POURING_RAIN_RATE = 4.0
# Reflectivity from which a thunderstorm is assumed
LIGHTNING_DBZ = 45
# Reflectivity from which the radar is treated as precipitation over the station
PRECIPITATION_DBZ = 12
# Solar constant reduced by average clear sky atmospheric transmittance
CLEAR_SKY_IRRADIANCE = 1361.0 * 0.75


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ChmiConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the ČHMÚ weather entity."""
    runtime = entry.runtime_data
    async_add_entities(
        [ChmiWeather(runtime.station, entry.entry_id, runtime.radar)]
    )


class ChmiWeather(ChmiStationEntity, WeatherEntity):
    """Current weather measured at the configured station."""

    _attr_name = None
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_native_pressure_unit = UnitOfPressure.HPA
    _attr_native_wind_speed_unit = UnitOfSpeed.METERS_PER_SECOND
    _attr_native_precipitation_unit = UnitOfPrecipitationDepth.MILLIMETERS
    _attr_native_visibility_unit = UnitOfLength.METERS

    def __init__(
        self,
        coordinator: ChmiStationCoordinator,
        entry_id: str,
        radar: ChmiRadarCoordinator | None,
    ) -> None:
        """Initialise the weather entity."""
        super().__init__(coordinator, entry_id)
        self._radar = radar
        self._attr_unique_id = f"{entry_id}_weather"

    async def async_added_to_hass(self) -> None:
        """Also follow the radar coordinator, which drives the condition."""
        await super().async_added_to_hass()
        if self._radar is not None:
            self.async_on_remove(
                self._radar.async_add_listener(self.async_write_ha_state)
            )

    # ------------------------------------------------------------------
    # Measured values
    # ------------------------------------------------------------------
    @property
    def native_temperature(self) -> float | None:
        """Air temperature."""
        return self.coordinator.data.value("T")

    @property
    def native_dew_point(self) -> float | None:
        """Dew point."""
        return self.coordinator.data.value("Td")

    @property
    def humidity(self) -> float | None:
        """Relative humidity."""
        return self.coordinator.data.value("H")

    @property
    def native_pressure(self) -> float | None:
        """Air pressure, reduced to sea level where the station reports it."""
        data = self.coordinator.data
        sea_level = data.value("P_hm")
        return sea_level if sea_level is not None else data.value("P")

    @property
    def native_wind_speed(self) -> float | None:
        """Wind speed."""
        return self.coordinator.data.value("F")

    @property
    def native_wind_gust_speed(self) -> float | None:
        """Maximum gust speed."""
        return self.coordinator.data.value("Fmax")

    @property
    def wind_bearing(self) -> float | None:
        """Wind bearing in degrees."""
        return self.coordinator.data.value("D")

    @property
    def cloud_coverage(self) -> float | None:
        """Cloud cover in percent, converted from eighths of the sky."""
        return octas_to_percent(self.coordinator.data.value("N"))

    @property
    def native_visibility(self) -> float | None:
        """Horizontal visibility in metres."""
        return visibility_meters(self.coordinator.data.value("VV"))

    # ------------------------------------------------------------------
    # Condition
    # ------------------------------------------------------------------
    @property
    def condition(self) -> str | None:
        """Return the current condition, or None when it cannot be derived."""
        data = self.coordinator.data

        reported = present_weather_condition(data.value("ww"))
        if reported is not None:
            return reported

        precipitation = self._precipitation_condition()
        if precipitation is not None:
            return precipitation

        visibility = self.native_visibility
        if visibility is not None and visibility < 1000:
            return ATTR_CONDITION_FOG

        return self._cloud_condition()

    def _radar_sample(self):
        """Radar sample above the station, if the radar is enabled."""
        if self._radar is None or self._radar.data is None:
            return None
        return self._radar.data.station_sample

    def _precipitation_condition(self) -> str | None:
        """Condition derived from measured precipitation and the radar echo."""
        data = self.coordinator.data
        measured = data.value("SRA10M")
        sample = self._radar_sample()
        dbz = sample.dbz if sample else None

        raining = bool(measured) or (dbz is not None and dbz >= PRECIPITATION_DBZ)
        if not raining:
            return None

        if dbz is not None and dbz >= LIGHTNING_DBZ:
            return ATTR_CONDITION_LIGHTNING_RAINY

        temperature = data.value("T")
        if temperature is not None:
            if temperature <= 0.5:
                return ATTR_CONDITION_SNOWY
            if temperature <= 2.0:
                return ATTR_CONDITION_SNOWY_RAINY

        # 10 minutes of accumulation extrapolated to an hourly rate
        rate = (measured or 0.0) * 6
        if sample is not None and sample.rain_rate is not None:
            rate = max(rate, sample.rain_rate)
        if rate >= POURING_RAIN_RATE:
            return ATTR_CONDITION_POURING
        return ATTR_CONDITION_RAINY

    def _solar_elevation(self) -> float:
        """Solar elevation above the horizon at the station, in degrees."""
        station = self.coordinator.data.station
        location = LocationInfo(
            name=station.name,
            region="CZ",
            timezone="UTC",
            latitude=station.latitude,
            longitude=station.longitude,
        )
        return solar_elevation(location.observer, dt_util.utcnow())

    def _cloud_condition(self) -> str | None:
        """Condition derived from cloud cover, sunshine or global radiation."""
        data = self.coordinator.data
        elevation = self._solar_elevation()
        night = elevation < 0

        octas = data.value("N")
        if octas is not None and octas <= 8:
            if octas <= 2:
                return ATTR_CONDITION_CLEAR_NIGHT if night else ATTR_CONDITION_SUNNY
            if octas <= 5:
                return ATTR_CONDITION_PARTLYCLOUDY
            return ATTR_CONDITION_CLOUDY

        if night:
            # Sunshine and radiation say nothing after sunset.
            return None

        sunshine = data.value("SSV10M")
        if sunshine is not None and elevation > 5:
            ratio = sunshine / 600
            if ratio > 0.6:
                return ATTR_CONDITION_SUNNY
            if ratio > 0.1:
                return ATTR_CONDITION_PARTLYCLOUDY
            return ATTR_CONDITION_CLOUDY

        radiation = data.value("RGLB10")
        if radiation is not None and elevation > 10:
            clear_sky = CLEAR_SKY_IRRADIANCE * math.sin(math.radians(elevation))
            if clear_sky <= 0:
                return None
            ratio = radiation / clear_sky
            if ratio > 0.75:
                return ATTR_CONDITION_SUNNY
            if ratio > 0.4:
                return ATTR_CONDITION_PARTLYCLOUDY
            return ATTR_CONDITION_CLOUDY

        return None
