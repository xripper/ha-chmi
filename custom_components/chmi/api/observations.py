"""Parsing of the 10-minute and hourly station observation files."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from homeassistant.util import dt as dt_util

from ..const import (
    DATASET_1H,
    DATASET_10M,
    NOW_DATA_URL,
    OBSERVATION_MAX_AGE,
    PRECIPITATION_ELEMENTS,
    QUALITY_REJECTED,
    QUALITY_UNKNOWN,
)
from .client import ChmiApiError, ChmiClient
from .document import data_rows

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class Observation:
    """A single measured value."""

    element: str
    value: float | None
    measured_at: datetime
    flag: str | None
    quality: int

    def is_current(self, now: datetime | None = None) -> bool:
        """Whether the value is recent enough to be published as a state."""
        age = (now or dt_util.utcnow()) - self.measured_at
        return age <= OBSERVATION_MAX_AGE


@dataclass(slots=True, frozen=True)
class PrecipitationTotal:
    """Precipitation accumulated over a period."""

    total: float
    element: str
    window_start: datetime
    samples: int
    measured_to: datetime


@dataclass(slots=True)
class StationReadings:
    """Everything read out of one station's files."""

    observations: dict[str, Observation] = field(default_factory=dict)
    precipitation: PrecipitationTotal | None = None


def _column_indexes(header: list[str]) -> dict[str, int]:
    """Locate the columns of an observation document."""
    index = {name: position for position, name in enumerate(header)}
    missing = {"ELEMENT", "DT", "VAL"} - set(index)
    if missing:
        raise ChmiApiError(f"Observation document is missing columns {sorted(missing)}")
    return index


def _iter_samples(document: object):
    """Yield the usable ``(element, value, measured_at, flag, quality)`` rows."""
    header, values = data_rows(document)
    index = _column_indexes(header)
    element_index = index["ELEMENT"]
    time_index = index["DT"]
    value_index = index["VAL"]
    flag_index = index.get("FLAG")
    quality_index = index.get("QUALITY")

    for row in values:
        raw_value = row[value_index]
        # Missing measurements are published as null or as an empty string.
        if raw_value is None or raw_value == "":
            continue
        quality = QUALITY_UNKNOWN
        if quality_index is not None and row[quality_index] is not None:
            try:
                quality = int(row[quality_index])
            except (TypeError, ValueError):
                quality = QUALITY_UNKNOWN
        if quality in QUALITY_REJECTED:
            continue
        measured_at = dt_util.parse_datetime(str(row[time_index]))
        if measured_at is None:
            continue
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            _LOGGER.debug(
                "Ignoring non numeric value %r of element %s",
                raw_value,
                row[element_index],
            )
            continue
        yield (
            row[element_index],
            value,
            dt_util.as_utc(measured_at),
            (row[flag_index] or None) if flag_index is not None else None,
            quality,
        )


def _parse_document(
    document: object, *, now: datetime | None = None
) -> dict[str, Observation]:
    """Return the newest usable value for every element in the document."""
    latest: dict[str, Observation] = {}
    for element, value, measured_at, flag, quality in _iter_samples(document):
        current = latest.get(element)
        if current is not None and current.measured_at >= measured_at:
            continue
        latest[element] = Observation(
            element=element,
            value=value,
            measured_at=measured_at,
            flag=flag,
            quality=quality,
        )

    return {
        element: observation
        for element, observation in latest.items()
        if observation.is_current(now)
    }


def _precipitation_since(
    documents: list[object], since: datetime
) -> PrecipitationTotal | None:
    """Add up the precipitation samples measured after ``since``.

    Each sample carries the amount of the interval that ends at its timestamp,
    so a sample stamped exactly at the start of the window still belongs to the
    previous one and is left out.  The ten minute and the hourly series describe
    the same rain, so only one of them is ever summed.
    """
    series: dict[str, list[tuple[datetime, float]]] = {
        element: [] for element in PRECIPITATION_ELEMENTS
    }
    for document in documents:
        for element, value, measured_at, _flag, _quality in _iter_samples(document):
            if element in series and measured_at > since:
                series[element].append((measured_at, value))

    for element in PRECIPITATION_ELEMENTS:
        samples = series[element]
        if not samples:
            continue
        return PrecipitationTotal(
            total=round(sum(value for _dt, value in samples), 1),
            element=element,
            window_start=since,
            samples=len(samples),
            measured_to=max(dt for dt, _value in samples),
        )
    return None


async def _load_documents(
    client: ChmiClient, wsi: str, dataset: str, days: list[date]
) -> dict[date, object]:
    """Download the daily files of one dataset for the given days."""
    documents: dict[date, object] = {}
    for day in days:
        url = NOW_DATA_URL.format(
            dataset=dataset, wsi=wsi, date=day.strftime("%Y%m%d")
        )
        document = await client.async_get_json(url, allow_missing=True)
        if document is not None:
            documents[day] = document
    return documents


async def async_load_observations(
    client: ChmiClient, wsi: str, dataset: str, today: date
) -> dict[str, Observation]:
    """Load the newest values of one dataset for a station.

    The daily file is rewritten roughly once per hour.  Right after midnight
    UTC the current day's file can be missing or still empty, so the previous
    day is used as a fallback.
    """
    result: dict[str, Observation] = {}
    for day in (today, today - timedelta(days=1)):
        url = NOW_DATA_URL.format(
            dataset=dataset, wsi=wsi, date=day.strftime("%Y%m%d")
        )
        document = await client.async_get_json(url, allow_missing=True)
        if document is None:
            continue
        try:
            parsed = _parse_document(document)
        except ChmiApiError as err:
            _LOGGER.debug("Malformed %s document at %s: %s", dataset, url, err)
            continue
        _merge_newest(result, parsed)
        if result:
            break
        # Nothing current in today's file, so drop it from the cache and try
        # the previous day.
        client.forget(url)
    return result


def _merge_newest(
    target: dict[str, Observation], source: dict[str, Observation]
) -> None:
    """Keep the newer observation of every element."""
    for element, observation in source.items():
        current = target.get(element)
        if current is None or observation.measured_at > current.measured_at:
            target[element] = observation


async def async_load_station_data(
    client: ChmiClient, wsi: str, today: date, *, precipitation_since: datetime
) -> StationReadings:
    """Load both datasets of a station and the running precipitation total.

    Some elements appear in both files; the newer sample wins regardless of
    which file it came from.  ``precipitation_since`` is normally the local
    midnight, which lies in the previous UTC day, so both daily files are read.
    """
    days = sorted({dt_util.as_utc(precipitation_since).date(), today})
    readings = StationReadings()
    documents: list[object] = []

    for dataset in (DATASET_1H, DATASET_10M):
        by_day = await _load_documents(client, wsi, dataset, days)
        documents.extend(by_day.values())
        latest: dict[str, Observation] = {}
        for day in sorted(by_day, reverse=True):
            try:
                parsed = _parse_document(by_day[day])
            except ChmiApiError as err:
                _LOGGER.debug("Malformed %s document of %s: %s", dataset, day, err)
                continue
            _merge_newest(latest, parsed)
            if latest:
                break
        _merge_newest(readings.observations, latest)

    if not readings.observations:
        raise ChmiApiError(f"No current observations published for station {wsi}")

    readings.precipitation = _precipitation_since(documents, precipitation_since)
    return readings
