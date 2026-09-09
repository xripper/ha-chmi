"""Parsing of the 10-minute and hourly station observation files."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from homeassistant.util import dt as dt_util

from ..const import (
    DATASET_1H,
    DATASET_10M,
    NOW_DATA_URL,
    OBSERVATION_MAX_AGE,
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


def _column_indexes(header: list[str]) -> dict[str, int]:
    """Locate the columns of an observation document."""
    index = {name: position for position, name in enumerate(header)}
    missing = {"ELEMENT", "DT", "VAL"} - set(index)
    if missing:
        raise ChmiApiError(f"Observation document is missing columns {sorted(missing)}")
    return index


def _parse_document(
    document: object, *, now: datetime | None = None
) -> dict[str, Observation]:
    """Return the newest usable value for every element in the document."""
    header, values = data_rows(document)
    index = _column_indexes(header)
    element_index = index["ELEMENT"]
    time_index = index["DT"]
    value_index = index["VAL"]
    flag_index = index.get("FLAG")
    quality_index = index.get("QUALITY")

    latest: dict[str, Observation] = {}
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
        measured_at = dt_util.as_utc(measured_at)
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            _LOGGER.debug(
                "Ignoring non numeric value %r of element %s",
                raw_value,
                row[element_index],
            )
            continue
        element = row[element_index]
        current = latest.get(element)
        if current is not None and current.measured_at >= measured_at:
            continue
        latest[element] = Observation(
            element=element,
            value=value,
            measured_at=measured_at,
            flag=(row[flag_index] or None) if flag_index is not None else None,
            quality=quality,
        )

    return {
        element: observation
        for element, observation in latest.items()
        if observation.is_current(now)
    }


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
    client: ChmiClient, wsi: str, today: date
) -> dict[str, Observation]:
    """Load both the 10-minute and the hourly dataset of a station.

    Some elements appear in both files; the newer sample wins regardless of
    which file it came from.
    """
    merged = await async_load_observations(client, wsi, DATASET_1H, today)
    _merge_newest(
        merged, await async_load_observations(client, wsi, DATASET_10M, today)
    )
    if not merged:
        raise ChmiApiError(f"No current observations published for station {wsi}")
    return merged
