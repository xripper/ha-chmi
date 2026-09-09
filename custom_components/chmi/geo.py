"""Geographic helpers: web-mercator projection, radar pixels, region lookup."""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

from .const import (
    EARTH_RADIUS,
    RADAR_BBOX,
    RADAR_DATA_SIZE,
    RADAR_PIXEL_METERS,
    REGION_FORECAST_CODES,
    REGIONS_GEOJSON,
)

Point = tuple[float, float]
Ring = list[list[float]]


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points in kilometres."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * 6371.0088 * math.asin(math.sqrt(a))


def mercator_x(lon: float) -> float:
    """X coordinate in EPSG:3857 metres."""
    return EARTH_RADIUS * math.radians(lon)


def mercator_y(lat: float) -> float:
    """Y coordinate in EPSG:3857 metres."""
    lat = max(min(lat, 89.5), -89.5)
    return EARTH_RADIUS * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def latlon_to_pixel(lat: float, lon: float) -> tuple[float, float] | None:
    """Map coordinates to a pixel of the cropped radar image.

    Returns floating point pixel coordinates (x from the left, y from the top)
    or None when the point lies outside the radar data area.
    """
    west, north = RADAR_BBOX[0], RADAR_BBOX[3]
    x = (mercator_x(lon) - mercator_x(west)) / RADAR_PIXEL_METERS
    y = (mercator_y(north) - mercator_y(lat)) / RADAR_PIXEL_METERS
    width, height = RADAR_DATA_SIZE
    if not (0 <= x < width and 0 <= y < height):
        return None
    return x, y


def latlon_to_pixel_unbounded(lat: float, lon: float) -> tuple[float, float]:
    """Map coordinates to radar image pixels without a bounds check.

    Used when drawing polygons that reach outside the radar data area.
    """
    west, _, _, north = RADAR_BBOX
    x = (mercator_x(lon) - mercator_x(west)) / RADAR_PIXEL_METERS
    y = (mercator_y(north) - mercator_y(lat)) / RADAR_PIXEL_METERS
    return x, y


def point_in_ring(lat: float, lon: float, ring: Ring) -> bool:
    """Ray-casting test of a point against a single GeoJSON linear ring."""
    inside = False
    count = len(ring)
    j = count - 1
    for i in range(count):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def point_in_polygon(lat: float, lon: float, polygon: list[Ring]) -> bool:
    """Test a point against a GeoJSON polygon (outer ring minus holes)."""
    if not polygon or not point_in_ring(lat, lon, polygon[0]):
        return False
    return not any(point_in_ring(lat, lon, hole) for hole in polygon[1:])


@lru_cache(maxsize=1)
def load_regions() -> list[tuple[str, list[list[Ring]]]]:
    """Load the bundled region boundaries.

    Blocking file access; call from the executor or during import-time setup.
    """
    path = Path(__file__).parent / "data" / REGIONS_GEOJSON
    doc = json.loads(path.read_text(encoding="utf-8"))
    regions: list[tuple[str, list[list[Ring]]]] = []
    for feature in doc["features"]:
        geometry = feature["geometry"]
        coordinates = geometry["coordinates"]
        polygons = coordinates if geometry["type"] == "MultiPolygon" else [coordinates]
        regions.append((feature["properties"]["name"], polygons))
    return regions


def region_for_point(lat: float, lon: float) -> str | None:
    """Return the name of the Czech region containing the point."""
    for name, polygons in load_regions():
        if any(point_in_polygon(lat, lon, polygon) for polygon in polygons):
            return name
    return None


def nearest_region(lat: float, lon: float) -> str | None:
    """Return the region whose boundary is closest to the point.

    Used as a fallback for locations just outside the simplified boundaries.
    """
    best: tuple[float, str] | None = None
    for name, polygons in load_regions():
        for polygon in polygons:
            for ring in polygon:
                for point in ring:
                    distance = haversine_km(lat, lon, point[1], point[0])
                    if best is None or distance < best[0]:
                        best = (distance, name)
    return best[1] if best else None


def forecast_code_for_region(region: str | None) -> str | None:
    """Map a region name to the ČHMÚ regional text forecast code."""
    if region is None:
        return None
    return REGION_FORECAST_CODES.get(region)
