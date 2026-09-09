"""Regional text forecasts issued by ČHMÚ forecasters."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime

from homeassistant.util import dt as dt_util

from ..const import TEXT_FORECAST_INDEX_URL, TEXT_FORECAST_URL
from .client import ChmiApiError, ChmiClient

_LOGGER = logging.getLogger(__name__)

# <a href="web_pCK0tx_RPPH_091000.json">…</a>   09-Sep-2026 10:15   27917
_INDEX_ENTRY = re.compile(
    r'<a href="(?P<name>web_pCK(?P<day>[0-9n])tx_(?P<code>RP[A-Z]{2})_\d+\.json)">'
    r"[^<]*</a>\s+(?P<modified>\d{2}-[A-Za-z]{3}-\d{4} \d{2}:\d{2})"
)
_INDEX_TIME_FORMAT = "%d-%b-%Y %H:%M"

# Forecast day indexes to publish: today and tomorrow.
FORECAST_DAYS: tuple[str, ...] = ("0", "1")


@dataclass(slots=True, frozen=True)
class ForecastSection:
    """One labelled paragraph of a text forecast."""

    name: str
    headline: str | None
    text: str


@dataclass(slots=True, frozen=True)
class TextForecast:
    """A regional text forecast for one day."""

    day: str
    place: str | None
    headline: str | None
    issued: datetime | None
    start: datetime | None
    end: datetime | None
    sections: tuple[ForecastSection, ...] = field(default_factory=tuple)

    @property
    def summary(self) -> str | None:
        """The short introductory sentence, if the forecast has one."""
        for section in self.sections:
            if section.name in ("textIntro", "textHeadline"):
                return section.text
        return self.sections[0].text if self.sections else None


def _parse_index(html: str, code: str) -> dict[str, str]:
    """Return the newest file name per forecast day for one region code."""
    newest: dict[str, tuple[datetime, str]] = {}
    for match in _INDEX_ENTRY.finditer(html):
        if match["code"] != code or match["day"] not in FORECAST_DAYS:
            continue
        try:
            modified = datetime.strptime(match["modified"], _INDEX_TIME_FORMAT)
        except ValueError:
            continue
        current = newest.get(match["day"])
        if current is None or modified > current[0]:
            newest[match["day"]] = (modified, match["name"])
    return {day: name for day, (_, name) in newest.items()}


def _parse_forecast(document: object, day: str) -> TextForecast:
    """Convert a forecast document into a :class:`TextForecast`."""
    try:
        properties = document["data"]["features"][0]["properties"]
    except (KeyError, IndexError, TypeError) as err:
        raise ChmiApiError(f"Unexpected forecast structure: {err}") from err

    headline_block = properties.get("headline-main") or {}
    sections = tuple(
        ForecastSection(
            name=item.get("name") or "",
            headline=item.get("headline"),
            text=item["displayText"].replace("\xa0", " ").strip(),
        )
        for item in properties.get("data") or []
        if item.get("displayText")
    )
    place = (properties.get("place") or {}).get("name")
    return TextForecast(
        day=day,
        place=place,
        headline=headline_block.get("headline"),
        issued=dt_util.parse_datetime(properties.get("sent") or ""),
        start=dt_util.parse_datetime(headline_block.get("startTime") or ""),
        end=dt_util.parse_datetime(headline_block.get("endTime") or ""),
        sections=sections,
    )


async def async_load_text_forecasts(
    client: ChmiClient, code: str
) -> dict[str, TextForecast]:
    """Load the newest text forecasts for a regional forecast code."""
    index = await client.async_get_text(TEXT_FORECAST_INDEX_URL, cache=False)
    if not index:
        raise ChmiApiError("Text forecast index is empty")

    names = _parse_index(index, code)
    if not names:
        raise ChmiApiError(f"No text forecast published for {code}")

    forecasts: dict[str, TextForecast] = {}
    for day, name in sorted(names.items()):
        document = await client.async_get_json(
            TEXT_FORECAST_URL.format(name=name), allow_missing=True
        )
        if document is None:
            continue
        forecasts[day] = _parse_forecast(document, day)
    if not forecasts:
        raise ChmiApiError(f"Text forecasts for {code} could not be read")
    return forecasts
