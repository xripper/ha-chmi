"""Tests of the projection and region helpers."""

from __future__ import annotations

import pytest

from custom_components.chmi.const import RADAR_BBOX, RADAR_DATA_SIZE
from custom_components.chmi.geo import (
    forecast_code_for_region,
    haversine_km,
    latlon_to_pixel,
    nearest_region,
    region_for_point,
)

PRAGUE = (50.0693, 14.4278)
BRNO = (49.1951, 16.6068)


def test_pixel_of_the_north_west_corner_is_the_image_origin() -> None:
    """The bounding box corner has to map onto pixel 0,0."""
    west, _south, _east, north = RADAR_BBOX
    x, y = latlon_to_pixel(north, west)
    assert x == pytest.approx(0, abs=0.05)
    assert y == pytest.approx(0, abs=0.05)


def test_pixel_of_the_south_east_corner_is_the_image_size() -> None:
    """The opposite corner has to map onto the last pixel of the grid."""
    _west, south, east, _north = RADAR_BBOX
    width, height = RADAR_DATA_SIZE
    x, y = latlon_to_pixel(south + 0.001, east - 0.001)
    assert x == pytest.approx(width, abs=1.0)
    assert y == pytest.approx(height, abs=1.0)


def test_pixel_of_prague_lies_inside_the_grid() -> None:
    """Prague has to fall roughly in the middle of the radar image."""
    x, y = latlon_to_pixel(*PRAGUE)
    assert 200 < x < 250
    assert 140 < y < 175


def test_points_outside_the_radar_area_have_no_pixel() -> None:
    """Coordinates south of the composite return no pixel."""
    assert latlon_to_pixel(45.0, 14.0) is None
    assert latlon_to_pixel(50.0, 25.0) is None


@pytest.mark.parametrize(
    ("coordinates", "region"),
    [
        (PRAGUE, "Hlavní město Praha"),
        (BRNO, "Jihomoravský kraj"),
        ((49.8209, 18.2625), "Moravskoslezský kraj"),
        ((50.7663, 15.0543), "Liberecký kraj"),
    ],
)
def test_region_lookup(coordinates: tuple[float, float], region: str) -> None:
    """Known coordinates resolve to their region."""
    assert region_for_point(*coordinates) == region


def test_region_lookup_outside_czechia() -> None:
    """Vienna is outside every region, but the nearest one is still found."""
    assert region_for_point(48.2082, 16.3738) is None
    assert nearest_region(48.2082, 16.3738) == "Jihomoravský kraj"


def test_forecast_code_mapping() -> None:
    """Region names map to the ČHMÚ regional forecast codes."""
    assert forecast_code_for_region("Hlavní město Praha") == "RPPH"
    assert forecast_code_for_region("Kraj Vysočina") == "RPVY"
    assert forecast_code_for_region(None) is None
    assert forecast_code_for_region("Wien") is None


def test_haversine_distance() -> None:
    """Distance between Prague and Brno is about 185 km."""
    assert haversine_km(*PRAGUE, *BRNO) == pytest.approx(185, abs=5)
