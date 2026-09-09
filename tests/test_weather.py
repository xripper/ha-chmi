"""Tests of the derived weather condition."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest

from custom_components.chmi.api.observations import Observation
from custom_components.chmi.api.radar import RadarFrame
from custom_components.chmi.api.stations import Station
from custom_components.chmi.const import QUALITY_GOOD
from custom_components.chmi.coordinator import RadarState, StationData
from custom_components.chmi.radar_image import RadarSample, dbz_to_rain_rate
from custom_components.chmi.weather import ChmiWeather

PRAGUE = Station(
    wsi="0-20000-0-11518",
    station_id="P1PRUZ01",
    name="Praha, Ruzyně",
    latitude=50.1003,
    longitude=14.2556,
    elevation=364.0,
)
MEASURED_AT = datetime(2026, 9, 9, 11, 50, tzinfo=UTC)
NOON = "2026-09-09T10:00:00+00:00"
MIDNIGHT = "2026-09-09T23:00:00+00:00"


@dataclass
class StubCoordinator:
    """Minimal stand-in for a coordinator."""

    data: Any
    wsi: str = PRAGUE.wsi
    last_update_success: bool = True

    def async_add_listener(self, *args: Any, **kwargs: Any):
        """Accept listeners without doing anything."""
        return lambda: None


def _station(**values: float) -> StationData:
    """Build station data from element values."""
    return StationData(
        station=PRAGUE,
        elements=frozenset(values),
        observations={
            element: Observation(
                element=element,
                value=value,
                measured_at=MEASURED_AT,
                flag=None,
                quality=QUALITY_GOOD,
            )
            for element, value in values.items()
        },
    )


def _radar(dbz: int | None) -> StubCoordinator:
    """Build a radar coordinator holding one sample per location."""
    sample = RadarSample(
        dbz=dbz,
        rain_rate=None if dbz is None else round(dbz_to_rain_rate(dbz), 2),
        in_coverage=True,
        aloft_only=False,
    )
    state = RadarState(
        frame=RadarFrame(frame_time=MEASURED_AT, variant="png", png=b""),
        image=b"",
        home_sample=sample,
        station_sample=sample,
    )
    return StubCoordinator(data=state)


def _weather(station: StationData, radar: StubCoordinator | None = None) -> ChmiWeather:
    """Build the weather entity around stub coordinators."""
    return ChmiWeather(StubCoordinator(data=station), "entry-1", radar)


def test_present_weather_code_wins(freezer) -> None:
    """A station reported code takes precedence over derived values."""
    freezer.move_to(NOON)
    entity = _weather(_station(ww=61, T=8.0, N=1.0), _radar(None))
    assert entity.condition == "rainy"


def test_measured_precipitation_makes_it_rain(freezer) -> None:
    """Measured precipitation is enough to report rain."""
    freezer.move_to(NOON)
    entity = _weather(_station(SRA10M=0.2, T=12.0))
    assert entity.condition == "rainy"


def test_heavy_precipitation_is_pouring(freezer) -> None:
    """More than 4 mm/h is reported as pouring."""
    freezer.move_to(NOON)
    entity = _weather(_station(SRA10M=1.2, T=12.0))
    assert entity.condition == "pouring"


def test_radar_echo_makes_it_rain_without_a_rain_gauge(freezer) -> None:
    """A station without a rain gauge still reports rain from the radar."""
    freezer.move_to(NOON)
    entity = _weather(_station(T=10.0), _radar(24))
    assert entity.condition == "rainy"


def test_cold_precipitation_is_snow(freezer) -> None:
    """Precipitation below freezing is snow."""
    freezer.move_to(NOON)
    entity = _weather(_station(SRA10M=0.4, T=-1.0))
    assert entity.condition == "snowy"


def test_precipitation_near_zero_is_sleet(freezer) -> None:
    """Precipitation just above freezing is reported as sleet."""
    freezer.move_to(NOON)
    entity = _weather(_station(SRA10M=0.4, T=1.5))
    assert entity.condition == "snowy-rainy"


def test_strong_echo_is_a_thunderstorm(freezer) -> None:
    """A very strong echo above the station is reported as a thunderstorm."""
    freezer.move_to(NOON)
    entity = _weather(_station(SRA10M=1.0, T=18.0), _radar(52))
    assert entity.condition == "lightning-rainy"


def test_low_visibility_is_fog(freezer) -> None:
    """Visibility below one kilometre is reported as fog."""
    freezer.move_to(NOON)
    entity = _weather(_station(VV=5, T=6.0, N=8.0))  # 500 m
    assert entity.condition == "fog"


@pytest.mark.parametrize(
    ("octas", "condition"),
    [(0.0, "sunny"), (2.0, "sunny"), (4.0, "partlycloudy"), (8.0, "cloudy")],
)
def test_cloud_cover_during_the_day(
    freezer, octas: float, condition: str
) -> None:
    """Cloud cover in eighths drives the condition during the day."""
    freezer.move_to(NOON)
    entity = _weather(_station(N=octas, T=15.0))
    assert entity.condition == condition


def test_obscured_sky_is_not_cloud_cover(freezer) -> None:
    """WMO code 9 means the sky was obscured, not 112 % of cloud."""
    freezer.move_to(NOON)
    entity = _weather(_station(N=9.0, T=15.0))
    assert entity.cloud_coverage is None
    assert entity.condition is None


def test_clear_sky_at_night(freezer) -> None:
    """A clear sky after sunset is reported as a clear night."""
    freezer.move_to(MIDNIGHT)
    entity = _weather(_station(N=0.0, T=9.0))
    assert entity.condition == "clear-night"


def test_sunshine_duration_when_cloud_cover_is_missing(freezer) -> None:
    """Sunshine duration is the next best source during the day."""
    freezer.move_to(NOON)
    assert _weather(_station(SSV10M=560.0, T=20.0)).condition == "sunny"
    assert _weather(_station(SSV10M=200.0, T=20.0)).condition == "partlycloudy"
    assert _weather(_station(SSV10M=0.0, T=20.0)).condition == "cloudy"


def test_global_radiation_when_sunshine_is_missing(freezer) -> None:
    """Global radiation is used when nothing better is measured."""
    freezer.move_to(NOON)
    assert _weather(_station(RGLB10=700.0, T=20.0)).condition == "sunny"
    assert _weather(_station(RGLB10=100.0, T=20.0)).condition == "cloudy"


def test_condition_stays_unknown_without_any_source(freezer) -> None:
    """A station measuring only temperature reports no condition."""
    freezer.move_to(NOON)
    assert _weather(_station(T=14.0)).condition is None


def test_condition_unknown_at_night_without_cloud_data(freezer) -> None:
    """Sunshine and radiation say nothing at night, so nothing is reported."""
    freezer.move_to(MIDNIGHT)
    assert _weather(_station(T=9.0, SSV10M=0.0, RGLB10=0.0)).condition is None


def test_measured_values_are_exposed(freezer) -> None:
    """Measured values reach the weather entity attributes."""
    freezer.move_to(NOON)
    entity = _weather(
        _station(
            T=21.4,
            H=55.0,
            P=985.0,
            P_hm=1009.7,
            F=3.2,
            Fmax=7.1,
            D=270.0,
            Td=11.8,
            N=6.0,
            VV=75,
        )
    )
    assert entity.native_temperature == 21.4
    assert entity.humidity == 55.0
    assert entity.native_pressure == 1009.7  # sea level preferred
    assert entity.native_wind_speed == 3.2
    assert entity.native_wind_gust_speed == 7.1
    assert entity.wind_bearing == 270.0
    assert entity.native_dew_point == 11.8
    assert entity.cloud_coverage == 75.0
    assert entity.native_visibility == 25000
