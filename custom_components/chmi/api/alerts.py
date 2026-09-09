"""Parsing of the ČHMÚ CAP v1.2 warning feed."""

from __future__ import annotations

import logging
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from xml.etree.ElementTree import ParseError

from defusedxml import DefusedXmlException, ElementTree
from homeassistant.util import dt as dt_util

from ..const import REGION_CISORP_PREFIXES
from .client import ChmiApiError

_LOGGER = logging.getLogger(__name__)

CAP_NAMESPACE = {"cap": "urn:oasis:names:tc:emergency:cap:1.2"}

# ČHMÚ keeps one info block per hazard type in the feed even when nothing is
# happening; those blocks are marked as unlikely minor events and their event
# name starts with "no warning".
_IDLE_PREFIXES = ("žádná", "žádný", "no ")

# meteoalarm compatible awareness levels
_AWARENESS_LEVELS = {
    "minor": "2; yellow; Moderate",
    "moderate": "2; yellow; Moderate",
    "severe": "3; orange; Severe",
    "extreme": "4; red; Extreme",
}

# meteoalarm compatible awareness types, matched against the event name
_AWARENESS_TYPES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("vitr", "vetr", "vichr", "wind"), "1; Wind"),
    (("snih", "snez", "namraz", "led", "snow", "ice", "slippery"), "2; snow-ice"),
    (("bour", "thunder"), "3; Thunderstorm"),
    (("mlh", "fog"), "4; Fog"),
    (("nizk", "mraz", "cold"), "6; low-temperature"),
    (("vysok", "teplot", "heat"), "5; high-temperature"),
    (("pozar", "fire"), "8; forest-fire"),
    (("lavin", "avalanche"), "9; avalanches"),
    (("dest", "srazk", "rain"), "10; rain"),
    (("povodn", "flood"), "12; flooding"),
)


@dataclass(slots=True, frozen=True)
class Alert:
    """One CAP warning."""

    identifier: str
    event: str
    severity: str
    urgency: str
    certainty: str
    response_type: str | None
    onset: datetime | None
    expires: datetime | None
    description: str | None
    instruction: str | None
    web: str | None
    sent: datetime | None
    language: str
    areas: tuple[str, ...] = field(default_factory=tuple)
    region_codes: frozenset[str] = field(default_factory=frozenset)

    @property
    def awareness_level(self) -> str:
        """Meteoalarm compatible awareness level."""
        return _AWARENESS_LEVELS.get(self.severity.lower(), "2; yellow; Moderate")

    @property
    def awareness_type(self) -> str:
        """Meteoalarm compatible awareness type derived from the event name."""
        event = _normalise(self.event)
        for keywords, awareness in _AWARENESS_TYPES:
            if any(_normalise(keyword) in event for keyword in keywords):
                return awareness
        return "11; unknown"

    def is_active(self, now: datetime | None = None) -> bool:
        """Whether the warning is valid at the given moment."""
        moment = now or dt_util.utcnow()
        if self.onset is not None and self.onset > moment:
            return False
        return self.expires is None or self.expires >= moment

    def is_upcoming(self, now: datetime | None = None) -> bool:
        """Whether the warning starts in the future.

        ČHMÚ issues warnings up to two days ahead, so those are reported
        separately instead of turning the warning sensor on early.
        """
        moment = now or dt_util.utcnow()
        if self.onset is None or self.onset <= moment:
            return False
        return self.expires is None or self.expires >= moment

    def covers_region(self, region: str | None) -> bool:
        """Whether the warning covers the given region.

        The CISORP geocodes are authoritative: their first two digits identify
        the region, which works for area descriptions that name several regions
        or only a part of one.  The description is compared as a fallback for
        feeds that carry no geocodes.
        """
        if region is None:
            return True
        prefix = REGION_CISORP_PREFIXES.get(region)
        if self.region_codes:
            return prefix is not None and prefix in self.region_codes
        if not self.areas:
            # No area information at all means the whole country.
            return True
        normalised_region = _normalise(region)
        return any(_areas_match(normalised_region, area) for area in self.areas)


def _normalise(text: str) -> str:
    """Lowercase text without diacritics, for tolerant comparisons."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _text(node, path: str) -> str | None:
    """Return the stripped text of a child element."""
    child = node.find(path, CAP_NAMESPACE)
    if child is None or child.text is None:
        return None
    value = child.text.strip()
    return value or None


def _time(node, path: str) -> datetime | None:
    """Return a child element parsed as an aware UTC datetime."""
    raw = _text(node, path)
    if raw is None:
        return None
    parsed = dt_util.parse_datetime(raw)
    if parsed is None:
        return None
    return dt_util.as_utc(parsed)


def _is_idle(event: str, severity: str, certainty: str) -> bool:
    """Whether an info block is one of the permanent "no warning" entries."""
    normalised = _normalise(event)
    if normalised.startswith(_IDLE_PREFIXES):
        return True
    return severity.lower() == "minor" and certainty.lower() == "unlikely"


def _region_codes(area) -> set[str]:
    """Region prefixes of the CISORP geocodes of one CAP area."""
    codes: set[str] = set()
    for geocode in area.findall("cap:geocode", CAP_NAMESPACE):
        if _text(geocode, "cap:valueName") != "CISORP":
            continue
        value = _text(geocode, "cap:value")
        if value and len(value) >= 2:
            codes.add(value[:2])
    return codes


def _areas_match(normalised_region: str, area: str) -> bool:
    """Whether a CAP area description refers to the given region."""
    normalised_area = _normalise(area)
    return normalised_region in normalised_area or normalised_area in normalised_region


def parse_alerts(payload: str | bytes) -> list[Alert]:
    """Parse the CAP feed into alerts, one per language and hazard.

    Blocking (the feed is about 1.5 MB of XML); call from the executor.
    """
    try:
        root = ElementTree.fromstring(payload)
    except (DefusedXmlException, ParseError) as err:
        raise ChmiApiError(f"CAP feed is not valid XML: {err}") from err
    identifier = _text(root, "cap:identifier") or ""
    sent = _time(root, "cap:sent")

    alerts: list[Alert] = []
    for info in root.findall("cap:info", CAP_NAMESPACE):
        event = _text(info, "cap:event")
        if not event:
            continue
        severity = _text(info, "cap:severity") or "Unknown"
        certainty = _text(info, "cap:certainty") or "Unknown"
        if _is_idle(event, severity, certainty):
            continue
        areas: list[str] = []
        region_codes: set[str] = set()
        for area in info.findall("cap:area", CAP_NAMESPACE):
            area_desc = _text(area, "cap:areaDesc")
            if area_desc:
                areas.append(area_desc)
            region_codes.update(_region_codes(area))
        alerts.append(
            Alert(
                identifier=identifier,
                event=event,
                severity=severity,
                urgency=_text(info, "cap:urgency") or "Unknown",
                certainty=certainty,
                response_type=_text(info, "cap:responseType"),
                onset=_time(info, "cap:onset"),
                expires=_time(info, "cap:expires"),
                description=_text(info, "cap:description"),
                instruction=_text(info, "cap:instruction"),
                web=_text(info, "cap:web"),
                sent=sent,
                language=_text(info, "cap:language") or "cs",
                areas=tuple(areas),
                region_codes=frozenset(region_codes),
            )
        )
    return alerts


def filter_alerts(
    alerts: list[Alert],
    *,
    region: str | None,
    language: str = "cs",
    now: datetime | None = None,
    upcoming: bool = False,
) -> list[Alert]:
    """Return the warnings of one language that cover the given region.

    With ``upcoming`` the warnings that have not started yet are returned
    instead of the ones in force.
    """
    selected = _in_language(alerts, language)
    matching = [alert for alert in selected if alert.covers_region(region)]
    if upcoming:
        return [alert for alert in matching if alert.is_upcoming(now)]
    return [alert for alert in matching if alert.is_active(now)]


def _in_language(alerts: list[Alert], language: str) -> list[Alert]:
    """Keep one language version of every warning.

    The feed carries each warning once per language.  Czech is the language of
    the issuing office, so it is used whenever the Home Assistant language has
    no version of its own; that also keeps warnings from being counted twice.
    """
    wanted = _normalise(language.split("-", 1)[0])
    selected = [
        alert for alert in alerts if _normalise(alert.language).startswith(wanted)
    ]
    if selected:
        return selected
    czech = [alert for alert in alerts if _normalise(alert.language).startswith("cs")]
    if czech:
        return czech
    return _first_language(alerts)


def _first_language(alerts: list[Alert]) -> list[Alert]:
    """Keep the warnings of whichever language comes first in the feed."""
    if not alerts:
        return []
    language = alerts[0].language
    return [alert for alert in alerts if alert.language == language]
