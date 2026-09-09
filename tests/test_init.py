"""Tests of the config entry setup and the created entities."""

from __future__ import annotations

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


def _register(mocker: AiohttpClientMocker, *, alerts: str = "alerts_idle.xml") -> None:
    """Register every request the integration makes during setup."""
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


@pytest.fixture(name="setup_entry")
def setup_entry_fixture(
    hass: HomeAssistant,
    custom_integration: None,
    aioclient_mock: AiohttpClientMocker,
    freezer,
) -> Callable:
    """Return a helper that sets the integration up."""

    async def _setup(*, alerts: str = "alerts_idle.xml") -> MockConfigEntry:
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
