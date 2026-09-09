"""Download of the CZRAD radar composite images."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from homeassistant.util import dt as dt_util

from ..const import RADAR_FRAME_STEP, RADAR_MAX_LOOKBACK, RADAR_URL
from .client import ChmiApiError, ChmiClient

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class RadarFrame:
    """One radar composite image."""

    frame_time: datetime
    variant: str
    png: bytes


def frame_url(variant: str, frame_time: datetime) -> str:
    """Build the URL of a radar frame; ČHMÚ names the files in UTC."""
    utc = dt_util.as_utc(frame_time)
    return RADAR_URL.format(
        variant=variant, date=utc.strftime("%Y%m%d"), time=utc.strftime("%H%M")
    )


def floor_to_frame(moment: datetime) -> datetime:
    """Round a moment down to the published 5-minute grid."""
    utc = dt_util.as_utc(moment)
    step = int(RADAR_FRAME_STEP.total_seconds() // 60)
    return utc.replace(minute=utc.minute - utc.minute % step, second=0, microsecond=0)


async def async_load_latest_frame(
    client: ChmiClient,
    variant: str,
    *,
    now: datetime | None = None,
    previous: RadarFrame | None = None,
) -> RadarFrame:
    """Return the newest available radar frame.

    Frames are published with a delay of one to three minutes, so the newest
    candidates may not exist yet and older ones are tried in turn.  When the
    newest existing frame is the one already held, it is returned unchanged
    instead of being downloaded again.
    """
    candidate = floor_to_frame(now or dt_util.utcnow())
    for _ in range(RADAR_MAX_LOOKBACK):
        if (
            previous is not None
            and previous.variant == variant
            and previous.frame_time == candidate
        ):
            return previous
        url = frame_url(variant, candidate)
        # Every frame has its own URL, so response caching would only grow.
        payload = await client.async_get_bytes(url, allow_missing=True, cache=False)
        if payload:
            return RadarFrame(frame_time=candidate, variant=variant, png=payload)
        candidate -= RADAR_FRAME_STEP

    if previous is not None and previous.variant == variant:
        _LOGGER.debug("No new radar frame published, keeping %s", previous.frame_time)
        return previous
    raise ChmiApiError("No radar composite available")


def frame_age(frame: RadarFrame, now: datetime | None = None) -> timedelta:
    """Age of a frame relative to now."""
    return (now or dt_util.utcnow()) - frame.frame_time
