"""Tests of the merged 1 hour precipitation product."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from custom_components.chmi.api.client import ChmiApiError
from custom_components.chmi.api.merge import (
    floor_to_step,
    frame_url,
    hour_ends_between,
    sample_frame,
)
from tests.conftest import load_bytes

# Churáňov, a station whose gauge read 1.8 mm in the 11:00-12:00 UTC window
CHURANOV = (49.068333, 13.615278)
PRAGUE = (50.0693, 14.4278)


@pytest.fixture(name="frame")
def frame_fixture() -> bytes:
    """Return the merged estimate for the hour ending at 12:00 UTC."""
    return load_bytes("merge_20260909_1200.hdf")


def test_sample_matches_the_gauge_reading(frame: bytes) -> None:
    """At a gauge location the product reproduces the gauge value."""
    point = sample_frame(frame, *CHURANOV)
    assert point.millimetres == pytest.approx(1.8, abs=0.05)
    assert point.window_end == datetime(2026, 9, 9, 12, tzinfo=UTC)


def test_sample_without_precipitation(frame: bytes) -> None:
    """A dry point reports zero, not unknown."""
    assert sample_frame(frame, *PRAGUE).millimetres == 0.0


def test_sample_outside_the_grid(frame: bytes) -> None:
    """A point outside the composite has no value."""
    assert sample_frame(frame, 45.0, 14.0).millimetres is None


def test_broken_payload_is_reported(frame: bytes) -> None:
    """A truncated download is reported as an API error."""
    with pytest.raises(ChmiApiError, match="could not be read"):
        sample_frame(frame[:2000], *PRAGUE)


def test_frame_url_uses_the_window_end() -> None:
    """The file name carries the end of the 60 minute window in UTC."""
    url = frame_url(datetime(2026, 9, 9, 12, tzinfo=UTC))
    assert url.endswith("/merge1h/hdf5/T_PASV23_C_OKPR_20260909120000.hdf")


def test_frames_are_floored_to_ten_minutes() -> None:
    """Frames exist on a ten minute grid."""
    assert floor_to_step(datetime(2026, 9, 10, 6, 27, 13, tzinfo=UTC)) == datetime(
        2026, 9, 10, 6, 20, tzinfo=UTC
    )


def test_hour_ends_exclude_the_window_start() -> None:
    """The hour that closed at midnight belongs to the previous day."""
    midnight = datetime(2026, 9, 9, 22, 0, tzinfo=UTC)  # 2026-09-10 00:00 CEST
    ends = hour_ends_between(midnight, datetime(2026, 9, 10, 6, 27, tzinfo=UTC))

    assert ends[0] == datetime(2026, 9, 9, 23, 0, tzinfo=UTC)
    assert ends[-1] == datetime(2026, 9, 10, 6, 0, tzinfo=UTC)
    assert midnight not in ends
    assert len(ends) == 8


def test_hour_ends_of_an_hour_that_has_not_closed() -> None:
    """Nothing is summed before the first hour of the day closes."""
    midnight = datetime(2026, 9, 9, 22, 0, tzinfo=UTC)
    assert hour_ends_between(midnight, datetime(2026, 9, 9, 22, 40, tzinfo=UTC)) == []


def test_hourly_windows_do_not_overlap() -> None:
    """Consecutive hourly frames cover consecutive hours, so they can be added."""
    frames = {
        "12": load_bytes("merge_20260909_1200.hdf"),
        "13": load_bytes("merge_20260909_1300.hdf"),
        "14": load_bytes("merge_20260909_1400.hdf"),
    }
    points = {
        hour: sample_frame(payload, *CHURANOV) for hour, payload in frames.items()
    }
    # Each file declares its own hour, so the windows can be added up.
    assert [point.window_end.hour for point in points.values()] == [12, 13, 14]
    values = [point.millimetres for point in points.values()]
    assert all(value is not None for value in values)
    assert sum(values) > max(values)
