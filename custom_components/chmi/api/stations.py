"""Station metadata (list of stations and their measured elements)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from ..const import NOW_METADATA_URL
from ..geo import haversine_km
from .client import ChmiApiError, ChmiClient
from .document import data_rows

_LOGGER = logging.getLogger(__name__)

META_STATIONS = "meta1"
META_ELEMENTS = "meta2"


@dataclass(slots=True, frozen=True)
class Station:
    """A ČHMÚ measuring station."""

    wsi: str
    station_id: str
    name: str
    latitude: float
    longitude: float
    elevation: float | None

    def distance_km(self, latitude: float, longitude: float) -> float:
        """Distance from the given coordinates in kilometres."""
        return haversine_km(latitude, longitude, self.latitude, self.longitude)


@dataclass(slots=True)
class StationCatalog:
    """Stations that publish current data, with their available elements."""

    stations: dict[str, Station] = field(default_factory=dict)
    elements: dict[str, frozenset[str]] = field(default_factory=dict)

    def sorted_by_distance(
        self, latitude: float, longitude: float
    ) -> list[tuple[Station, float]]:
        """Return stations ordered by distance from the given coordinates."""
        pairs = [
            (station, station.distance_km(latitude, longitude))
            for station in self.stations.values()
        ]
        pairs.sort(key=lambda item: item[1])
        return pairs

    def elements_for(self, wsi: str) -> frozenset[str]:
        """Return the elements a station measures."""
        return self.elements.get(wsi, frozenset())


def _optional_float(row: list[Any], position: int | None) -> float | None:
    """Read an optional numeric column of a metadata row."""
    if position is None or row[position] is None:
        return None
    try:
        return float(row[position])
    except (TypeError, ValueError):
        return None


async def async_load_catalog(client: ChmiClient, today: date) -> StationCatalog:
    """Load the station catalog.

    Metadata files are published once a day; shortly after midnight UTC the
    file for the current day may be missing, so the previous day is used as a
    fallback.
    """
    for day in (today, today - timedelta(days=1)):
        stamp = day.strftime("%Y%m%d")
        stations_doc = await client.async_get_json(
            NOW_METADATA_URL.format(name=META_STATIONS, date=stamp), allow_missing=True
        )
        elements_doc = await client.async_get_json(
            NOW_METADATA_URL.format(name=META_ELEMENTS, date=stamp), allow_missing=True
        )
        if stations_doc and elements_doc:
            return _build_catalog(stations_doc, elements_doc)
        _LOGGER.debug("Station metadata for %s not available yet", stamp)

    raise ChmiApiError("Station metadata is not available")


def _build_catalog(stations_doc: Any, elements_doc: Any) -> StationCatalog:
    """Build the catalog from the two metadata documents."""
    elements: dict[str, set[str]] = {}
    header, values = data_rows(elements_doc)
    try:
        wsi_index = header.index("WSI")
        element_index = header.index("EG_EL_ABBREVIATION")
    except ValueError as err:
        raise ChmiApiError(f"Element metadata is missing a column: {err}") from err
    for row in values:
        elements.setdefault(row[wsi_index], set()).add(row[element_index])

    header, values = data_rows(stations_doc)
    index = {name: position for position, name in enumerate(header)}
    missing = {"WSI", "GH_ID", "FULL_NAME", "GEOGR1", "GEOGR2"} - set(index)
    if missing:
        raise ChmiApiError(f"Station metadata is missing columns {sorted(missing)}")
    stations: dict[str, Station] = {}
    for row in values:
        wsi = row[index["WSI"]]
        # Only stations that appear in the element metadata publish current data.
        if wsi not in elements:
            continue
        longitude = row[index["GEOGR1"]]
        latitude = row[index["GEOGR2"]]
        if latitude is None or longitude is None:
            continue
        stations[wsi] = Station(
            wsi=wsi,
            station_id=row[index["GH_ID"]],
            name=row[index["FULL_NAME"]],
            latitude=float(latitude),
            longitude=float(longitude),
            elevation=_optional_float(row, index.get("ELEVATION")),
        )

    return StationCatalog(
        stations=stations,
        elements={wsi: frozenset(items) for wsi, items in elements.items()},
    )
