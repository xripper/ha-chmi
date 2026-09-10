"""Merged 1 hour precipitation estimate (radar plus rain gauges).

ČHMÚ combines the radar precipitation estimate with the readings of its own and
partner rain gauges using kriging with external drift, and publishes the result
every 10 minutes as an ODIM HDF5 grid.  At a gauge location the product
reproduces the gauge reading; between gauges it is the radar field bent onto
them, which is what makes it usable for a point that has no gauge of its own.
"""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from io import BytesIO

import pyfive
from homeassistant.util import dt as dt_util

from ..const import (
    MERGE_DATASET,
    MERGE_MAX_LOOKBACK,
    MERGE_STEP,
    MERGE_URL,
    MERGE_WINDOW,
)
from ..geo import latlon_to_pixel
from .client import ChmiApiError, ChmiClient

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class FramePoint:
    """One point read out of a frame, with the window the file declares."""

    window_end: datetime
    millimetres: float | None


@dataclass(slots=True, frozen=True)
class MergeSample:
    """Precipitation of one 60 minute window at one point."""

    window_end: datetime
    millimetres: float | None
    in_coverage: bool

    @property
    def window_start(self) -> datetime:
        """Start of the accumulation window."""
        return self.window_end - MERGE_WINDOW


def frame_url(window_end: datetime) -> str:
    """Build the URL of the frame whose window ends at the given moment."""
    return MERGE_URL.format(stamp=dt_util.as_utc(window_end).strftime("%Y%m%d%H%M%S"))


def floor_to_step(moment: datetime) -> datetime:
    """Round a moment down to the published 10 minute grid."""
    utc = dt_util.as_utc(moment)
    step = int(MERGE_STEP.total_seconds() // 60)
    return utc.replace(minute=utc.minute - utc.minute % step, second=0, microsecond=0)


def _decode_text(value: object) -> str:
    """HDF5 string attributes come back as bytes."""
    if isinstance(value, bytes):
        return value.decode("ascii", errors="replace")
    return str(value)


def sample_frame(payload: bytes, latitude: float, longitude: float) -> FramePoint:
    """Read the accumulated millimetres at one point of a frame.

    ``millimetres`` is None when the point lies outside the grid or the product
    has no value there.  The window end is taken from the file itself, so the
    caller can check it against the window it asked for.  Blocking (HDF5
    decoding); call from the executor.
    """
    try:
        with pyfive.File(BytesIO(payload)) as handle:
            what = handle[f"{MERGE_DATASET}/what"].attrs
            window = handle["dataset1/what"].attrs
            gain = float(what["gain"])
            offset = float(what["offset"])
            nodata = float(what["nodata"])
            undetect = float(what["undetect"])
            # The file names the end of its window in UTC.
            end = datetime.strptime(
                f"{_decode_text(window['enddate'])}{_decode_text(window['endtime'])}",
                "%Y%m%d%H%M%S",
            ).replace(tzinfo=UTC)
            position = latlon_to_pixel(latitude, longitude)
            raw = None
            if position is not None:
                grid = handle[f"{MERGE_DATASET}/data"][...]
                height, width = grid.shape
                x, y = int(position[0]), int(position[1])
                if 0 <= x < width and 0 <= y < height:
                    raw = float(grid[y, x])
    except (KeyError, IndexError, OSError, ValueError, struct.error) as err:
        # A truncated or reshaped file must not take the coordinator down.
        raise ChmiApiError(f"Merge frame could not be read: {err}") from err

    if raw is None or raw == nodata:
        millimetres = None
    elif raw == undetect:
        millimetres = 0.0
    else:
        millimetres = round(raw * gain + offset, 2)
    return FramePoint(window_end=end, millimetres=millimetres)


async def async_load_frame(
    client: ChmiClient, window_end: datetime
) -> bytes | None:
    """Download one frame, or None when it is not published."""
    # Every window has its own URL, so response caching would only grow.
    return await client.async_get_bytes(
        frame_url(window_end), allow_missing=True, cache=False
    )


async def async_load_latest(
    client: ChmiClient, *, now: datetime | None = None
) -> tuple[datetime, bytes] | None:
    """Return the newest published frame and the end of its window.

    Frames appear roughly 20 minutes after their window closes, so the newest
    candidates usually do not exist yet and older ones are tried in turn.
    """
    candidate = floor_to_step(now or dt_util.utcnow())
    for _ in range(MERGE_MAX_LOOKBACK):
        payload = await async_load_frame(client, candidate)
        if payload:
            return candidate, payload
        candidate -= MERGE_STEP
    _LOGGER.debug("No merge frame published around %s", candidate)
    return None


def hour_ends_between(start: datetime, now: datetime) -> list[datetime]:
    """Full hours that closed inside ``(start, now]``.

    The windows of these frames do not overlap, so their values can be added
    up; the hour that is still running has no frame of its own.
    """
    first = dt_util.as_utc(start).replace(minute=0, second=0, microsecond=0)
    if first <= dt_util.as_utc(start):
        first += timedelta(hours=1)
    last = dt_util.as_utc(now).replace(minute=0, second=0, microsecond=0)
    ends: list[datetime] = []
    moment = first
    while moment <= last:
        ends.append(moment)
        moment += timedelta(hours=1)
    return ends
