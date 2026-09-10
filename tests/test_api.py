"""Tests of the open data parsers."""

from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import patch

import pytest

from custom_components.chmi.api.alerts import filter_alerts, parse_alerts
from custom_components.chmi.api.client import ChmiApiError
from custom_components.chmi.api.observations import (
    async_load_observations,
    async_load_station_data,
)
from custom_components.chmi.api.radar import (
    async_load_latest_frame,
    floor_to_frame,
    frame_url,
)
from custom_components.chmi.api.stations import async_load_catalog
from custom_components.chmi.api.text_forecast import async_load_text_forecasts
from custom_components.chmi.const import DATASET_10M, RADAR_VARIANT_PNG
from tests.conftest import load_bytes, load_json, load_text

STATION = "0-20000-0-11518"
TODAY = date(2026, 9, 9)
# The fixtures were captured on 2026-09-09; the newest sample they carry.
NEWEST_SAMPLE = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
LOCAL_MIDNIGHT = datetime(2026, 9, 8, 22, 0, tzinfo=UTC)  # 2026-09-09 00:00 CEST


def _document(*rows: list) -> dict:
    """Wrap rows in the ČHMÚ DataCollection envelope."""
    return {
        "data": {
            "data": {
                "header": "STATION,ELEMENT,DT,VAL,FLAG,QUALITY",
                "values": list(rows),
            }
        }
    }


def _frozen(moment: datetime = NEWEST_SAMPLE):
    """Pretend it is ``moment``, so the fixture samples count as current."""
    return patch(
        "custom_components.chmi.api.observations.dt_util.utcnow",
        return_value=moment,
    )


class FakeClient:
    """A client returning canned responses by URL substring."""

    def __init__(self, responses: dict[str, object], *, missing: bool = True) -> None:
        """Store the canned responses."""
        self.responses = responses
        self.missing = missing
        self.requests: list[str] = []
        self.forgotten: list[str] = []

    def forget(self, url: str) -> None:
        """Drop a cached response (the real client evicts it here)."""
        self.forgotten.append(url)

    def _lookup(self, url: str) -> object | None:
        self.requests.append(url)
        # Most specific marker wins, so a directory index does not shadow the
        # files inside it.
        for marker in sorted(self.responses, key=len, reverse=True):
            if marker in url:
                return self.responses[marker]
        return None

    async def async_get_json(self, url: str, *, allow_missing: bool = False):
        """Return the canned JSON document for a URL."""
        payload = self._lookup(url)
        if payload is None and not allow_missing:
            raise ChmiApiError(f"{url} not found")
        return payload

    async def async_get_text(
        self, url: str, *, allow_missing: bool = False, cache: bool = True
    ):
        """Return the canned text document for a URL."""
        return self._lookup(url)

    async def async_get_bytes(
        self, url: str, *, allow_missing: bool = False, cache: bool = True
    ):
        """Return the canned binary document for a URL."""
        payload = self._lookup(url)
        if payload is None and not allow_missing:
            raise ChmiApiError(f"{url} not found")
        return payload


# ---------------------------------------------------------------------------
# Station metadata
# ---------------------------------------------------------------------------
async def test_catalog_lists_only_stations_with_data() -> None:
    """Stations missing from the element metadata are not offered."""
    client = FakeClient(
        {"meta1": load_json("meta1.json"), "meta2": load_json("meta2.json")}
    )
    catalog = await async_load_catalog(client, TODAY)

    assert set(catalog.stations) == {
        "0-20000-0-11518",
        "0-20000-0-11519",
        "0-20000-0-11464",
    }
    station = catalog.stations[STATION]
    assert station.name == "Praha, Ruzyně"
    assert station.latitude == pytest.approx(50.1003, abs=0.001)
    assert station.longitude == pytest.approx(14.2556, abs=0.001)
    assert "ww" in catalog.elements_for(STATION)


async def test_catalog_falls_back_to_the_previous_day() -> None:
    """Right after midnight UTC only the previous day's metadata exists."""
    yesterday = TODAY.strftime("%Y%m%d")
    client = FakeClient(
        {
            f"meta1-{yesterday}": load_json("meta1.json"),
            f"meta2-{yesterday}": load_json("meta2.json"),
        }
    )
    catalog = await async_load_catalog(client, date(2026, 9, 10))
    assert STATION in catalog.stations


async def test_catalog_reports_missing_metadata() -> None:
    """Without metadata the caller gets a clear error."""
    with pytest.raises(ChmiApiError, match="Station metadata is not available"):
        await async_load_catalog(FakeClient({}), TODAY)


async def test_stations_sorted_by_distance() -> None:
    """The nearest station to Prague is the Prague one."""
    client = FakeClient(
        {"meta1": load_json("meta1.json"), "meta2": load_json("meta2.json")}
    )
    catalog = await async_load_catalog(client, TODAY)
    ordered = catalog.sorted_by_distance(50.0693, 14.4278)
    assert ordered[0][0].name.startswith("Praha")
    assert ordered[0][1] < ordered[-1][1]


# ---------------------------------------------------------------------------
# Observations
# ---------------------------------------------------------------------------
async def test_observations_take_the_newest_sample() -> None:
    """Each element keeps its newest published value."""
    client = FakeClient({"10m-": load_json("station_10m.json")})
    with _frozen():
        observations = await async_load_observations(
            client, STATION, DATASET_10M, TODAY
        )

    assert "T" in observations
    temperature = observations["T"]
    assert isinstance(temperature.value, float)
    assert temperature.measured_at.tzinfo is not None
    raw = load_json("station_10m.json")["data"]["data"]["values"]
    newest = max(row[2] for row in raw if row[1] == "T")
    assert temperature.measured_at.isoformat().replace("+00:00", "Z") == newest


async def test_observations_skip_rejected_quality() -> None:
    """Values flagged as missing or poor are not used."""
    document = load_json("station_10m.json")
    values = document["data"]["data"]["values"]
    temperatures = [row for row in values if row[1] == "T"]
    newest = max(row[2] for row in temperatures)
    for row in temperatures:
        if row[2] == newest:
            row[4], row[5] = "", 4.0  # missing
            row[3] = -99.0

    client = FakeClient({"10m-": document})
    with _frozen():
        observations = await async_load_observations(
            client, STATION, DATASET_10M, TODAY
        )
    assert observations["T"].value != -99.0
    assert observations["T"].measured_at.isoformat().replace("+00:00", "Z") < newest


async def test_station_data_merges_both_datasets() -> None:
    """Hourly elements complement the ten minute ones."""
    client = FakeClient(
        {
            "10m-": load_json("station_10m.json"),
            "1h-": load_json("station_1h.json"),
        }
    )
    with _frozen():
        readings = await async_load_station_data(
            client, STATION, TODAY, precipitation_since=LOCAL_MIDNIGHT
        )
    observations = readings.observations
    assert {"T", "H", "SRA10M"} <= set(observations)  # from the 10 minute file
    assert {"ww", "N", "VV", "Td"} <= set(observations)  # from the hourly file


async def test_station_data_without_any_file() -> None:
    """A station without published data raises."""
    with pytest.raises(ChmiApiError, match="No current observations published"):
        await async_load_station_data(
            FakeClient({}), STATION, TODAY, precipitation_since=LOCAL_MIDNIGHT
        )


async def test_precipitation_since_local_midnight() -> None:
    """The daily total adds up the ten minute amounts of the window."""
    yesterday = _document(
        [STATION, "SRA10M", "2026-09-08T22:00:00Z", 5.0, "", 5.0],  # at the start
        [STATION, "SRA10M", "2026-09-08T22:10:00Z", 0.4, "", 5.0],
    )
    today = _document(
        [STATION, "SRA10M", "2026-09-09T06:00:00Z", 1.1, "", 5.0],
        [STATION, "SRA10M", "2026-09-09T11:50:00Z", 0.0, "", 5.0],
        [STATION, "T", "2026-09-09T11:50:00Z", 18.0, "", 5.0],
    )
    client = FakeClient(
        {f"10m-{STATION}-20260908": yesterday,
         f"10m-{STATION}-20260909": today}
    )

    with _frozen():
        readings = await async_load_station_data(
            client, STATION, TODAY, precipitation_since=LOCAL_MIDNIGHT
        )

    total = readings.precipitation
    assert total is not None
    # The sample stamped exactly at the window start belongs to the day before.
    assert total.total == 1.5
    assert total.element == "SRA10M"
    assert total.samples == 3
    assert total.window_start == LOCAL_MIDNIGHT


async def test_precipitation_reads_both_utc_days() -> None:
    """The window starts in the previous UTC day, so both files are read."""
    client = FakeClient(
        {
            f"10m-{STATION}-20260908": _document(
                [STATION, "SRA10M", "2026-09-08T22:30:00Z", 2.0, "", 5.0]
            ),
            f"10m-{STATION}-20260909": _document(
                [STATION, "SRA10M", "2026-09-09T11:50:00Z", 3.0, "", 5.0]
            ),
        }
    )

    with _frozen():
        readings = await async_load_station_data(
            client, STATION, TODAY, precipitation_since=LOCAL_MIDNIGHT
        )

    assert readings.precipitation.total == 5.0
    assert readings.precipitation.samples == 2


async def test_precipitation_falls_back_to_the_hourly_series() -> None:
    """A station without a ten minute series is summed from the hourly one."""
    hourly = _document(
        [STATION, "SRA1H", "2026-09-09T11:00:00Z", 1.2, "", 5.0],
        [STATION, "SRA1H", "2026-09-09T12:00:00Z", 0.8, "", 5.0],
    )
    client = FakeClient({f"1h-{STATION}-20260909": hourly})

    with _frozen():
        readings = await async_load_station_data(
            client, STATION, TODAY, precipitation_since=LOCAL_MIDNIGHT
        )

    assert readings.precipitation.element == "SRA1H"
    assert readings.precipitation.total == 2.0


async def test_precipitation_absent_for_stations_without_a_gauge() -> None:
    """A station measuring no precipitation reports no total."""
    client = FakeClient(
        {
            f"10m-{STATION}-20260909": _document(
                [STATION, "T", "2026-09-09T11:50:00Z", 18.0, "", 5.0]
            )
        }
    )

    with _frozen():
        readings = await async_load_station_data(
            client, STATION, TODAY, precipitation_since=LOCAL_MIDNIGHT
        )

    assert readings.precipitation is None


async def test_empty_values_are_ignored_not_fatal() -> None:
    """ČHMÚ writes a missing measurement as an empty string."""
    document = load_json("station_10m.json")
    values = document["data"]["data"]["values"]
    stamps = sorted({row[2] for row in values})
    for row in values:
        if row[1] == "T" and row[2] == stamps[-1]:
            row[3], row[5] = "", 5.0  # empty value, quality "unknown"

    client = FakeClient({"10m-": document})
    with patch(
        "custom_components.chmi.api.observations.dt_util.utcnow",
        return_value=datetime.fromisoformat(stamps[-1].replace("Z", "+00:00")),
    ):
        observations = await async_load_observations(
            client, STATION, DATASET_10M, TODAY
        )
    assert observations["T"].value is not None
    measured_at = observations["T"].measured_at.isoformat().replace("+00:00", "Z")
    assert measured_at == stamps[-2]


async def test_hourly_value_is_not_replaced_by_an_older_ten_minute_value() -> None:
    """A stale 10 minute file must not overwrite a newer hourly value."""
    hourly = load_json("station_1h.json")
    ten_minute = load_json("station_10m.json")
    hourly_rows = hourly["data"]["data"]["values"]
    newest = max(row[2] for row in hourly_rows)
    hourly["data"]["data"]["values"] = [
        [STATION, "T", newest, 21.5, "", 5.0],
    ]
    # The ten minute file only carries a much older sample of the same element.
    ten_minute["data"]["data"]["values"] = [
        [STATION, "T", "2026-09-09T09:00:00Z", 8.0, "", 5.0],
    ]

    client = FakeClient({"1h-": hourly, "10m-": ten_minute})
    with patch(
        "custom_components.chmi.api.observations.dt_util.utcnow",
        return_value=datetime.fromisoformat(newest.replace("Z", "+00:00")),
    ):
        readings = await async_load_station_data(
            client, STATION, TODAY, precipitation_since=LOCAL_MIDNIGHT
        )
    assert readings.observations["T"].value == 21.5


async def test_values_older_than_the_maximum_age_are_dropped() -> None:
    """A frozen instrument stops reporting instead of repeating its last value."""
    client = FakeClient({"10m-": load_json("station_10m.json")})
    with patch(
        "custom_components.chmi.api.observations.dt_util.utcnow",
        return_value=datetime(2026, 9, 10, 12, 0, tzinfo=UTC),
    ):
        observations = await async_load_observations(
            client, STATION, DATASET_10M, TODAY
        )
    assert observations == {}


async def test_missing_column_is_reported_as_api_error() -> None:
    """A changed document layout does not crash the coordinator."""
    document = load_json("station_10m.json")
    document["data"]["data"]["header"] = "STATION,DT,VAL,FLAG,QUALITY"
    client = FakeClient({"10m-": document})
    observations = await async_load_observations(client, STATION, DATASET_10M, TODAY)
    assert observations == {}


# ---------------------------------------------------------------------------
# Radar
# ---------------------------------------------------------------------------
def test_frame_url_uses_utc() -> None:
    """Frame file names are built from the UTC timestamp."""
    url = frame_url(RADAR_VARIANT_PNG, datetime(2026, 9, 9, 11, 50, tzinfo=UTC))
    assert url.endswith("/png/pacz2gmaps3.z_max3d.20260909.1150.0.png")


def test_frames_are_floored_to_five_minutes() -> None:
    """Radar frames exist on a five minute grid."""
    moment = datetime(2026, 9, 9, 11, 53, 41, tzinfo=UTC)
    assert floor_to_frame(moment) == datetime(2026, 9, 9, 11, 50, tzinfo=UTC)


async def test_radar_walks_back_to_the_newest_published_frame() -> None:
    """Frames are published late, so older candidates are tried."""
    client = FakeClient({"20260909.1140": load_bytes("radar_maxz.png")})
    frame = await async_load_latest_frame(
        client,
        RADAR_VARIANT_PNG,
        now=datetime(2026, 9, 9, 11, 52, tzinfo=UTC),
    )
    assert frame.frame_time == datetime(2026, 9, 9, 11, 40, tzinfo=UTC)
    assert len(client.requests) == 3  # 11:50, 11:45, 11:40


async def test_radar_keeps_the_previous_frame_without_downloading() -> None:
    """An unchanged newest frame is not downloaded again."""
    client = FakeClient({"20260909.1150": load_bytes("radar_maxz.png")})
    first = await async_load_latest_frame(
        client, RADAR_VARIANT_PNG, now=datetime(2026, 9, 9, 11, 52, tzinfo=UTC)
    )
    second = await async_load_latest_frame(
        client,
        RADAR_VARIANT_PNG,
        now=datetime(2026, 9, 9, 11, 53, tzinfo=UTC),
        previous=first,
    )
    assert second is first
    assert len(client.requests) == 1


async def test_radar_without_any_frame() -> None:
    """When nothing is published the caller gets an error."""
    with pytest.raises(ChmiApiError, match="No radar composite available"):
        await async_load_latest_frame(
            FakeClient({}),
            RADAR_VARIANT_PNG,
            now=datetime(2026, 9, 9, 11, 52, tzinfo=UTC),
        )


# ---------------------------------------------------------------------------
# Warnings
# ---------------------------------------------------------------------------
def test_idle_feed_has_no_alerts() -> None:
    """The permanent "no warning" blocks are ignored."""
    assert parse_alerts(load_text("alerts_idle.xml")) == []


def test_active_alert_is_parsed() -> None:
    """A warning is parsed with its metadata."""
    alerts = parse_alerts(load_text("alerts_active.xml"))
    assert len(alerts) == 2  # Czech and English version of the same warning

    czech = next(alert for alert in alerts if alert.language == "cs")
    assert czech.event == "Výstraha před velmi silným větrem"
    assert czech.severity == "Severe"
    assert czech.awareness_level == "3; orange; Severe"
    assert czech.awareness_type == "1; Wind"
    assert czech.areas == ("Hlavní město Praha", "Středočeský kraj")
    assert czech.region_codes == frozenset({"11", "21"})
    assert czech.expires == datetime.fromisoformat("2026-09-10T02:00:00+02:00")


def test_warnings_before_their_onset_are_not_active() -> None:
    """A warning issued in advance turns the sensor on only when it starts."""
    alerts = parse_alerts(load_text("alerts_active.xml"))  # onset 14:00 local
    before = datetime(2026, 9, 9, 11, 52, tzinfo=UTC)
    after = datetime(2026, 9, 9, 12, 30, tzinfo=UTC)

    assert filter_alerts(alerts, region=None, language="cs", now=before) == []
    upcoming = filter_alerts(
        alerts, region=None, language="cs", now=before, upcoming=True
    )
    assert len(upcoming) == 1
    assert len(filter_alerts(alerts, region=None, language="cs", now=after)) == 1
    assert (
        filter_alerts(alerts, region=None, language="cs", now=after, upcoming=True)
        == []
    )


def test_one_warning_is_counted_once_in_any_language() -> None:
    """A Home Assistant language without its own version falls back to Czech."""
    alerts = parse_alerts(load_text("alerts_current.xml"))
    now = datetime(2026, 9, 9, 11, 52, tzinfo=UTC)

    for language in ("cs", "en", "de", "sk", "pl"):
        matching = filter_alerts(alerts, region=None, language=language, now=now)
        assert len(matching) == 1, language
    fallback = filter_alerts(alerts, region=None, language="de", now=now)
    assert fallback[0].language == "cs"


def test_region_is_matched_by_geocode_not_by_wording() -> None:
    """District level area descriptions still match their region."""
    alerts = parse_alerts(load_text("alerts_district.xml"))
    now = datetime(2026, 9, 9, 11, 52, tzinfo=UTC)

    assert alerts[0].areas == ("Praha-východ", "Beroun")
    for region in ("Hlavní město Praha", "Středočeský kraj"):
        assert filter_alerts(alerts, region=region, language="cs", now=now)
    assert filter_alerts(alerts, region="Zlínský kraj", language="cs", now=now) == []


def test_alerts_filtered_by_language_and_region() -> None:
    """Only the local language and the home region are reported."""
    alerts = parse_alerts(load_text("alerts_current.xml"))
    now = datetime(2026, 9, 9, 14, 30, tzinfo=UTC)

    czech = filter_alerts(alerts, region="Hlavní město Praha", language="cs", now=now)
    assert [alert.language for alert in czech] == ["cs"]

    english = filter_alerts(alerts, region="Středočeský kraj", language="en", now=now)
    assert [alert.language for alert in english] == ["en-GB"]

    other_region = filter_alerts(
        alerts, region="Zlínský kraj", language="cs", now=now
    )
    assert other_region == []


def test_expired_alerts_are_dropped() -> None:
    """Warnings past their expiry are not reported."""
    alerts = parse_alerts(load_text("alerts_current.xml"))
    later = datetime(2026, 9, 11, 0, 0, tzinfo=UTC)
    assert filter_alerts(alerts, region=None, language="cs", now=later) == []


def test_invalid_feed_raises() -> None:
    """Malformed XML is reported as an API error."""
    with pytest.raises(ChmiApiError, match="not valid XML"):
        parse_alerts("<alert>")


# ---------------------------------------------------------------------------
# Text forecast
# ---------------------------------------------------------------------------
async def test_text_forecast_picks_the_newest_file_per_day() -> None:
    """The index is used to find the newest file for each forecast day."""
    client = FakeClient(
        {
            "forecast/now/": load_text("forecast_index.html"),
            "web_pCK0tx_RPPH_091000.json": load_json("forecast_pck0_rpph.json"),
            "web_pCK1tx_RPPH_091000.json": load_json("forecast_pck0_rpph.json"),
        }
    )
    forecasts = await async_load_text_forecasts(client, "RPPH")

    assert set(forecasts) == {"0", "1"}
    today = forecasts["0"]
    assert today.place == "pro Prahu"
    assert today.headline == "Předpověď na středu"
    assert today.summary == "Postupně zataženo a deštivo"
    assert any(section.name == "textWind" for section in today.sections)
    assert all("\xa0" not in section.text for section in today.sections)


async def test_text_forecast_for_unknown_region() -> None:
    """A region without published files raises."""
    client = FakeClient({"forecast/now/": load_text("forecast_index.html")})
    with pytest.raises(ChmiApiError, match="No text forecast published"):
        await async_load_text_forecasts(client, "RPZZ")
