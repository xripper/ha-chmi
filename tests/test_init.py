"""Tests of the config entry setup and the created entities."""

from __future__ import annotations

import json
import re
from collections.abc import Callable

import pytest
from homeassistant.components.camera import async_get_image
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.chmi.const import (
    CONF_ALERTS,
    CONF_MERGE,
    CONF_RADAR,
    CONF_RADAR_VARIANT,
    CONF_STATION,
    CONF_STATION_NAME,
    CONF_TEXT_FORECAST,
    DOMAIN,
    RADAR_VARIANT_MASKED,
)
from tests.conftest import load_bytes, load_text

STATION = "0-20000-0-11518"
STATION_NAME = "Praha, Ruzyně"
NOW = "2026-09-09T11:52:00+00:00"
OPENDATA = "https://opendata.chmi.cz"


def _register(
    mocker: AiohttpClientMocker,
    *,
    alerts: str = "alerts_idle.xml",
    previous_day: bool = True,
) -> None:
    """Register every request the integration makes during setup.

    In a Czech time zone the local day starts in the previous UTC file, so
    those two URLs are requested as well; ``previous_day=False`` leaves them
    for the caller to serve.
    """
    _register_merge(mocker)
    if previous_day:
        for dataset in ("10m", "1h"):
            mocker.get(
                f"{OPENDATA}/meteorology/climate/now/data"
                f"/{dataset}-{STATION}-20260908.json",
                status=404,
            )
    mocker.get(
        f"{OPENDATA}/meteorology/climate/now/metadata/meta1-20260909.json",
        text=load_text("meta1.json"),
    )
    mocker.get(
        f"{OPENDATA}/meteorology/climate/now/metadata/meta2-20260909.json",
        text=load_text("meta2.json"),
    )
    mocker.get(
        f"{OPENDATA}/meteorology/climate/now/data/10m-{STATION}-20260909.json",
        text=load_text("station_10m.json"),
    )
    mocker.get(
        f"{OPENDATA}/meteorology/climate/now/data/1h-{STATION}-20260909.json",
        text=load_text("station_1h.json"),
    )
    mocker.get(
        f"{OPENDATA}/meteorology/weather/radar/composite/maxz/png_masked"
        "/pacz2gmaps3.z_max3d.20260909.1150.0.png",
        content=load_bytes("radar_maxz_masked.png"),
    )
    mocker.get(
        f"{OPENDATA}/meteorology/weather/forecast/now/",
        text=load_text("forecast_index.html"),
    )
    for name in ("web_pCK0tx_RPPH_091000.json", "web_pCK1tx_RPPH_091000.json"):
        mocker.get(
            f"{OPENDATA}/meteorology/weather/forecast/now/{name}",
            text=load_text("forecast_pck0_rpph.json"),
        )
    mocker.get(
        "https://vystrahy-cr.chmi.cz/data/XOCZ50_OKPR.xml", text=load_text(alerts)
    )


def _register_merge(mocker: AiohttpClientMocker) -> None:
    """Serve the three captured merged frames; every other window is missing."""
    for stamp, fixture in (
        ("20260909120000", "merge_20260909_1200.hdf"),
        ("20260909130000", "merge_20260909_1300.hdf"),
        ("20260909140000", "merge_20260909_1400.hdf"),
    ):
        mocker.get(
            f"{OPENDATA}/meteorology/weather/radar/composite/merge1h/hdf5"
            f"/T_PASV23_C_OKPR_{stamp}.hdf",
            content=load_bytes(fixture),
        )
    mocker.get(re.compile(r".*/merge1h/hdf5/.*\.hdf$"), status=404)


@pytest.fixture(name="setup_entry")
def setup_entry_fixture(
    hass: HomeAssistant,
    custom_integration: None,
    aioclient_mock: AiohttpClientMocker,
    freezer,
) -> Callable:
    """Return a helper that sets the integration up."""

    async def _setup(
        *, alerts: str = "alerts_idle.xml", merge: bool = True
    ) -> MockConfigEntry:
        freezer.move_to(NOW)
        hass.config.latitude = 50.0693
        hass.config.longitude = 14.4278
        _register(aioclient_mock, alerts=alerts)

        entry = MockConfigEntry(
            domain=DOMAIN,
            title=STATION_NAME,
            unique_id=STATION,
            data={
                CONF_STATION: STATION,
                CONF_STATION_NAME: STATION_NAME,
                CONF_RADAR: True,
                CONF_RADAR_VARIANT: RADAR_VARIANT_MASKED,
                CONF_MERGE: merge,
                CONF_ALERTS: True,
                CONF_TEXT_FORECAST: True,
            },
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        return entry

    return _setup


def _entity_id(hass: HomeAssistant, entry: MockConfigEntry, key: str) -> str:
    """Look an entity of one config entry up by its key."""
    registry = er.async_get(hass)
    unique_id = f"{entry.entry_id}_{key}"
    for registry_entry in registry.entities.values():
        if registry_entry.platform == DOMAIN and registry_entry.unique_id == unique_id:
            return registry_entry.entity_id
    raise AssertionError(f"No entity with unique id {unique_id}")


async def test_entry_loads(hass: HomeAssistant, setup_entry: Callable) -> None:
    """The entry sets up and exposes one device."""
    entry = await setup_entry()
    assert entry.state is ConfigEntryState.LOADED


async def test_station_sensors(hass: HomeAssistant, setup_entry: Callable) -> None:
    """Sensors are created for the elements the station measures."""
    entry = await setup_entry()

    temperature = hass.states.get(_entity_id(hass, entry, "temperature"))
    assert temperature is not None
    assert float(temperature.state) == pytest.approx(21.5, abs=5)
    assert temperature.attributes["element"] == "T"
    assert temperature.attributes["station"] == STATION_NAME
    assert "measured_at" in temperature.attributes

    # The station reports no soil temperatures, so no such sensor exists.
    registry = er.async_get(hass)
    unique_ids = {
        entity.unique_id
        for entity in registry.entities.values()
        if entity.platform == DOMAIN
    }
    assert f"{entry.entry_id}_soil_temperature_5" not in unique_ids
    assert f"{entry.entry_id}_visibility" in unique_ids  # hourly element
    assert f"{entry.entry_id}_present_weather" in unique_ids


async def test_precipitation_today_spans_both_utc_days(
    hass: HomeAssistant,
    custom_integration: None,
    aioclient_mock: AiohttpClientMocker,
    freezer,
) -> None:
    """In Czech local time the day starts in the previous UTC file."""
    await hass.config.async_set_time_zone("Europe/Prague")
    freezer.move_to(NOW)  # 2026-09-09 13:52 local, so the day started at 22:00Z
    hass.config.latitude = 50.0693
    hass.config.longitude = 14.4278
    _register(aioclient_mock, previous_day=False)

    def envelope(*rows):
        return json.dumps(
            {
                "data": {
                    "data": {
                        "header": "STATION,ELEMENT,DT,VAL,FLAG,QUALITY",
                        "values": list(rows),
                    }
                }
            }
        )

    aioclient_mock.get(
        f"{OPENDATA}/meteorology/climate/now/data/10m-{STATION}-20260908.json",
        text=envelope(
            [STATION, "SRA10M", "2026-09-08T22:00:00Z", 9.9, "", 5.0],  # previous day
            [STATION, "SRA10M", "2026-09-08T23:00:00Z", 1.5, "", 5.0],
        ),
    )
    aioclient_mock.get(
        f"{OPENDATA}/meteorology/climate/now/data/1h-{STATION}-20260908.json",
        status=404,
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        title=STATION_NAME,
        unique_id=STATION,
        data={
            CONF_STATION: STATION,
            CONF_STATION_NAME: STATION_NAME,
            CONF_RADAR: False,
            CONF_RADAR_VARIANT: RADAR_VARIANT_MASKED,
            CONF_MERGE: False,
            CONF_ALERTS: False,
            CONF_TEXT_FORECAST: False,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get(_entity_id(hass, entry, "precipitation_today"))
    assert state is not None
    assert state.attributes["window_start"] == "2026-09-08T22:00:00+00:00"
    assert state.attributes["element"] == "SRA10M"
    # 9.9 mm is stamped exactly at the window start and belongs to the day
    # before; 1.5 mm from the previous UTC day still counts.
    assert float(state.state) >= 1.5
    assert float(state.state) < 9.9


async def test_merge_precipitation_at_home(
    hass: HomeAssistant,
    custom_integration: None,
    aioclient_mock: AiohttpClientMocker,
    freezer,
) -> None:
    """The merged product accumulates the completed hours at the home location."""
    from custom_components.chmi.api.merge import sample_frame

    await hass.config.async_set_time_zone("Europe/Prague")
    freezer.move_to("2026-09-09T14:30:00+00:00")
    # Churáňov, where the captured frames carry rain.
    hass.config.latitude = 49.068333
    hass.config.longitude = 13.615278
    _register(aioclient_mock)

    entry = MockConfigEntry(
        domain=DOMAIN,
        title=STATION_NAME,
        unique_id=STATION,
        data={
            CONF_STATION: STATION,
            CONF_STATION_NAME: STATION_NAME,
            CONF_RADAR: False,
            CONF_RADAR_VARIANT: RADAR_VARIANT_MASKED,
            CONF_MERGE: True,
            CONF_ALERTS: False,
            CONF_TEXT_FORECAST: False,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    expected = [
        sample_frame(load_bytes(name), 49.068333, 13.615278).millimetres
        for name in (
            "merge_20260909_1200.hdf",
            "merge_20260909_1300.hdf",
            "merge_20260909_1400.hdf",
        )
    ]

    today = hass.states.get(_entity_id(hass, entry, "precipitation_home_today"))
    assert today is not None
    assert float(today.state) == pytest.approx(round(sum(expected), 1), abs=0.05)
    assert today.attributes["hours_counted"] == 3
    assert today.attributes["covered_to"] == "2026-09-09T14:00:00+00:00"
    assert today.attributes["product"] == "merge1h"

    hour = hass.states.get(_entity_id(hass, entry, "precipitation_home_1h"))
    assert float(hour.state) == pytest.approx(expected[-1], abs=0.05)
    assert hour.attributes["window_end"] == "2026-09-09T14:00:00+00:00"
    assert hour.attributes["window_start"] == "2026-09-09T13:00:00+00:00"

    rolling = hass.states.get(_entity_id(hass, entry, "precipitation_home_24h"))
    assert float(rolling.state) == pytest.approx(round(sum(expected), 1), abs=0.05)
    assert rolling.attributes["hours_counted"] == 3


async def test_merge_follows_the_home_location(
    hass: HomeAssistant,
    custom_integration: None,
    aioclient_mock: AiohttpClientMocker,
    freezer,
) -> None:
    """Moving the Home Assistant location re-reads the day at the new point."""
    await hass.config.async_set_time_zone("Europe/Prague")
    freezer.move_to("2026-09-09T14:30:00+00:00")
    hass.config.latitude = 50.0693  # Praha, on the edge of the rain band
    hass.config.longitude = 14.4278
    _register(aioclient_mock)

    entry = MockConfigEntry(
        domain=DOMAIN,
        title=STATION_NAME,
        unique_id=STATION,
        data={
            CONF_STATION: STATION,
            CONF_STATION_NAME: STATION_NAME,
            CONF_RADAR: False,
            CONF_RADAR_VARIANT: RADAR_VARIANT_MASKED,
            CONF_MERGE: True,
            CONF_ALERTS: False,
            CONF_TEXT_FORECAST: False,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_id = _entity_id(hass, entry, "precipitation_home_today")
    in_prague = float(hass.states.get(entity_id).state)

    # Move home to Churáňov, where the same frames carry much more rain.
    hass.config.latitude = 49.068333
    hass.config.longitude = 13.615278
    await entry.runtime_data.merge.async_refresh()
    await hass.async_block_till_done()

    in_churanov = float(hass.states.get(entity_id).state)
    assert in_churanov > in_prague
    # The stored hours were re-read, not mixed with the ones from Prague.
    assert entry.runtime_data.merge.data.today_hours == 3


async def test_merge_can_be_switched_off(
    hass: HomeAssistant, setup_entry: Callable
) -> None:
    """Without the toggle no merged precipitation entity is created."""
    entry = await setup_entry(merge=False)
    registry = er.async_get(hass)
    keys = {
        entity.unique_id
        for entity in registry.entities.values()
        if entity.platform == DOMAIN
    }
    assert f"{entry.entry_id}_precipitation_home_today" not in keys
    assert entry.runtime_data.merge is None


async def test_units_are_converted(hass: HomeAssistant, setup_entry: Callable) -> None:
    """Cloud cover in eighths becomes percent, sunshine tenths become minutes."""
    entry = await setup_entry()

    cloud = hass.states.get(_entity_id(hass, entry, "cloud_coverage"))
    assert cloud is not None
    assert 0 <= float(cloud.state) <= 100

    sunshine = hass.states.get(_entity_id(hass, entry, "sunshine_1h"))
    assert sunshine is not None
    assert 0 <= float(sunshine.state) <= 60


async def test_weather_entity(hass: HomeAssistant, setup_entry: Callable) -> None:
    """The weather entity reports measurements and no forecast support."""
    entry = await setup_entry()

    state = hass.states.get(_entity_id(hass, entry, "weather"))
    assert state is not None
    assert state.attributes["temperature"] is not None
    assert state.attributes["humidity"] is not None
    # No forecast is published by ČHMÚ, so the entity advertises no features.
    assert not state.attributes.get("supported_features")


async def test_radar_camera_and_sensor(
    hass: HomeAssistant, setup_entry: Callable
) -> None:
    """The camera serves the rendered composite and the sensor its rain rate."""
    entry = await setup_entry()

    camera_entity = _entity_id(hass, entry, "radar")
    image = await async_get_image(hass, camera_entity)
    assert image.content.startswith(b"\x89PNG")

    state = hass.states.get(camera_entity)
    assert state.attributes["frame_time"] == "2026-09-09T11:50:00+00:00"
    assert state.attributes["product"] == RADAR_VARIANT_MASKED

    rain = hass.states.get(_entity_id(hass, entry, "radar_rain_rate"))
    assert rain is not None
    assert float(rain.state) >= 0
    assert rain.attributes["product"] == RADAR_VARIANT_MASKED


async def test_text_forecast_sensor(
    hass: HomeAssistant, setup_entry: Callable
) -> None:
    """The regional text forecast is exposed with its full text."""
    entry = await setup_entry()

    state = hass.states.get(_entity_id(hass, entry, "text_forecast"))
    assert state is not None
    assert state.state == "Postupně zataženo a deštivo"
    assert state.attributes["region_code"] == "RPPH"
    assert state.attributes["today"]["place"] == "pro Prahu"
    assert state.attributes["tomorrow"]["sections"]


async def test_no_warnings(hass: HomeAssistant, setup_entry: Callable) -> None:
    """With an idle feed the warning sensor stays off."""
    entry = await setup_entry()

    binary = hass.states.get(_entity_id(hass, entry, "alert"))
    assert binary.state == "off"
    assert binary.attributes["count"] == 0

    count = hass.states.get(_entity_id(hass, entry, "alert_count"))
    assert count.state == "0"


async def test_active_warning(hass: HomeAssistant, setup_entry: Callable) -> None:
    """A warning in force for the home region turns the binary sensor on."""
    hass.config.language = "cs"
    entry = await setup_entry(alerts="alerts_current.xml")

    binary = hass.states.get(_entity_id(hass, entry, "alert"))
    assert binary.state == "on"
    assert binary.attributes["event"] == "Výstraha před velmi silným větrem"
    assert binary.attributes["awareness_level"] == "3; orange; Severe"
    assert binary.attributes["awareness_type"] == "1; Wind"

    count = hass.states.get(_entity_id(hass, entry, "alert_count"))
    assert count.state == "1"


async def test_warning_issued_in_advance(
    hass: HomeAssistant, setup_entry: Callable
) -> None:
    """A warning that starts later is listed as upcoming, not as active."""
    hass.config.language = "cs"
    entry = await setup_entry(alerts="alerts_active.xml")  # onset 14:00 local

    binary = hass.states.get(_entity_id(hass, entry, "alert"))
    assert binary.state == "off"

    count = hass.states.get(_entity_id(hass, entry, "alert_count"))
    assert count.state == "0"
    upcoming = count.attributes["upcoming"]
    assert len(upcoming) == 1
    assert upcoming[0]["event"] == "Výstraha před velmi silným větrem"


async def test_stale_radar_frame_makes_the_camera_unavailable(
    hass: HomeAssistant,
    custom_integration: None,
    aioclient_mock: AiohttpClientMocker,
    freezer,
) -> None:
    """A radar outage must not leave an old frame on display."""
    freezer.move_to(NOW)
    hass.config.latitude = 50.0693
    hass.config.longitude = 14.4278
    _register(aioclient_mock)

    entry = MockConfigEntry(
        domain=DOMAIN,
        title=STATION_NAME,
        unique_id=STATION,
        data={
            CONF_STATION: STATION,
            CONF_STATION_NAME: STATION_NAME,
            CONF_RADAR: True,
            CONF_RADAR_VARIANT: RADAR_VARIANT_MASKED,
            CONF_ALERTS: False,
            CONF_TEXT_FORECAST: False,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    camera_entity = _entity_id(hass, entry, "radar")
    assert hass.states.get(camera_entity).state != "unavailable"

    # Move an hour ahead; the newest published frame is now far too old.
    freezer.move_to("2026-09-09T12:52:00+00:00")
    await entry.runtime_data.radar.async_refresh()
    await hass.async_block_till_done()

    assert entry.runtime_data.radar.last_update_success is False
    assert hass.states.get(camera_entity).state == "unavailable"


async def test_reload_keeps_the_coordinators_working(
    hass: HomeAssistant, setup_entry: Callable, aioclient_mock: AiohttpClientMocker
) -> None:
    """Reloading an entry must not leave its coordinators shut down."""
    entry = await setup_entry()
    before = len(
        [call for call in aioclient_mock.mock_calls if "pacz2gmaps3" in str(call[1])]
    )

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    radar = entry.runtime_data.radar
    await radar.async_refresh()
    await hass.async_block_till_done()

    assert radar.last_update_success is True
    assert radar.data is not None
    after = len(
        [call for call in aioclient_mock.mock_calls if "pacz2gmaps3" in str(call[1])]
    )
    assert after > before
    assert hass.states.get(_entity_id(hass, entry, "radar")).state != "unavailable"


async def test_unload(hass: HomeAssistant, setup_entry: Callable) -> None:
    """Unloading the entry releases the shared objects."""
    entry = await setup_entry()

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert DOMAIN not in hass.data
