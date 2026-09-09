"""Tests of the WMO code table decoding."""

from __future__ import annotations

import pytest

from custom_components.chmi.wmo import (
    present_weather_condition,
    visibility_meters,
    wind_bearing_label,
)


@pytest.mark.parametrize(
    ("code", "condition"),
    [
        (508, None),  # nothing to report
        (511, None),  # missing
        (100, None),  # automatic station, no significant weather
        (2, None),  # cloud evolution only
        (45, "fog"),
        (61, "rainy"),
        (65, "pouring"),
        (67, "snowy-rainy"),
        (71, "snowy"),
        (89, "hail"),
        (95, "lightning-rainy"),
        (101, "cloudy"),
        (103, "cloudy"),
        (110, "fog"),
        (112, "lightning"),
        (162, "rainy"),
        (163, "pouring"),
        (164, "snowy-rainy"),
        (171, "snowy"),
        (189, "hail"),
        (192, "lightning-rainy"),
    ],
)
def test_present_weather_condition(code: int, condition: str | None) -> None:
    """Present weather codes map to Home Assistant conditions."""
    assert present_weather_condition(code) == condition


def test_present_weather_condition_without_value() -> None:
    """A missing code yields no condition."""
    assert present_weather_condition(None) is None


@pytest.mark.parametrize(
    ("code", "metres"),
    [
        (0, 0),
        (10, 1000),
        (50, 5000),
        (60, 10000),
        (75, 25000),
        (82, 40000),
        (89, 70000),
        (97, 10000),
        (55, None),  # reserved
    ],
)
def test_visibility_meters(code: int, metres: int | None) -> None:
    """Visibility codes convert to metres."""
    assert visibility_meters(code) == metres


@pytest.mark.parametrize(
    ("bearing", "label"),
    [(0, "N"), (90, "E"), (180, "S"), (270, "W"), (350, "N"), (315, "NW")],
)
def test_wind_bearing_label(bearing: float, label: str) -> None:
    """Bearings map to compass abbreviations."""
    assert wind_bearing_label(bearing) == label
