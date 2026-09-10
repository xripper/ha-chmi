"""Tests of two stations configured at the same time."""

from __future__ import annotations

import pytest
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

FIRST = "0-20000-0-11518"
SECOND = "0-20000-0-11519"
OPENDATA = "https://opendata.chmi.cz"


@pytest.fixture(name="two_stations")
async def two_stations_fixture(
    hass: HomeAssistant,
    custom_integration: None,
    aioclient_mock: AiohttpClientMocker,
    freezer,
) -> tuple[MockConfigEntry, MockConfigEntry]:
    """Set up one station with and one without the country wide entities."""
    freezer.move_to("2026-09-09T11:52:00+00:00")
    hass.config.latitude = 50.0693
    hass.config.longitude = 14.4278

    aioclient_mock.get(
        f"{OPENDATA}/meteorology/climate/now/metadata/meta1-20260909.json",
        text=load_text("meta1.json"),
    )
    aioclient_mock.get(
        f"{OPENDATA}/meteorology/climate/now/metadata/meta2-20260909.json",
        text=load_text("meta2.json"),
    )
    for wsi in (FIRST, SECOND):
        aioclient_mock.get(
            f"{OPENDATA}/meteorology/climate/now/data/10m-{wsi}-20260909.json",
            text=load_text("station_10m.json"),
        )
        aioclient_mock.get(
            f"{OPENDATA}/meteorology/climate/now/data/1h-{wsi}-20260909.json",
            text=load_text("station_1h.json"),
        )
    aioclient_mock.get(
        f"{OPENDATA}/meteorology/weather/radar/composite/maxz/png_masked"
        "/pacz2gmaps3.z_max3d.20260909.1150.0.png",
        content=load_bytes("radar_maxz_masked.png"),
    )
    aioclient_mock.get(
        f"{OPENDATA}/meteorology/weather/forecast/now/",
        text=load_text("forecast_index.html"),
    )
    for name in ("web_pCK0tx_RPPH_091000.json", "web_pCK1tx_RPPH_091000.json"):
        aioclient_mock.get(
            f"{OPENDATA}/meteorology/weather/forecast/now/{name}",
            text=load_text("forecast_pck0_rpph.json"),
        )
    aioclient_mock.get(
        "https://vystrahy-cr.chmi.cz/data/XOCZ50_OKPR.xml",
        text=load_text("alerts_idle.xml"),
    )

    entries = []
    for wsi, name, country_wide in (
        (FIRST, "Praha, Ruzyně", True),
        (SECOND, "Praha, Karlov", False),
    ):
        entry = MockConfigEntry(
            domain=DOMAIN,
            title=name,
            unique_id=wsi,
            data={
                CONF_STATION: wsi,
                CONF_STATION_NAME: name,
                CONF_RADAR: country_wide,
                CONF_RADAR_VARIANT: RADAR_VARIANT_MASKED,
                CONF_MERGE: False,
                CONF_ALERTS: country_wide,
                CONF_TEXT_FORECAST: country_wide,
            },
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        entries.append(entry)
    await hass.async_block_till_done()
    return entries[0], entries[1]


def _keys_of(hass: HomeAssistant, entry: MockConfigEntry) -> set[str]:
    """Return the entity keys created for one config entry."""
    registry = er.async_get(hass)
    prefix = f"{entry.entry_id}_"
    return {
        entity.unique_id.removeprefix(prefix)
        for entity in registry.entities.values()
        if entity.platform == DOMAIN and entity.unique_id.startswith(prefix)
    }


async def test_both_entries_load(
    hass: HomeAssistant, two_stations: tuple[MockConfigEntry, MockConfigEntry]
) -> None:
    """Two stations coexist, each with its own device."""
    first, second = two_stations
    assert first.state is ConfigEntryState.LOADED
    assert second.state is ConfigEntryState.LOADED

    assert "temperature" in _keys_of(hass, first)
    assert "temperature" in _keys_of(hass, second)


async def test_country_wide_entities_exist_once(
    hass: HomeAssistant, two_stations: tuple[MockConfigEntry, MockConfigEntry]
) -> None:
    """Radar, warnings and forecast belong to the first station only."""
    first, second = two_stations
    first_keys = _keys_of(hass, first)
    second_keys = _keys_of(hass, second)
    for key in ("radar", "radar_rain_rate", "alert", "alert_count", "text_forecast"):
        assert key in first_keys
        assert key not in second_keys


async def test_shared_data_is_fetched_once(
    hass: HomeAssistant,
    two_stations: tuple[MockConfigEntry, MockConfigEntry],
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """The radar frame and the CAP feed are downloaded once for both entries."""
    radar_requests = [
        request
        for request in aioclient_mock.mock_calls
        if "pacz2gmaps3" in str(request[1])
    ]
    alert_requests = [
        request
        for request in aioclient_mock.mock_calls
        if "XOCZ50_OKPR" in str(request[1])
    ]
    assert len(radar_requests) == 1
    assert len(alert_requests) == 1


async def test_reloading_one_entry_keeps_the_other_working(
    hass: HomeAssistant,
    two_stations: tuple[MockConfigEntry, MockConfigEntry],
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Reloading the station that owns the radar keeps the radar polling."""
    first, second = two_stations

    assert await hass.config_entries.async_reload(second.entry_id)
    await hass.async_block_till_done()

    radar = first.runtime_data.radar
    assert radar is not None
    await radar.async_refresh()
    await hass.async_block_till_done()
    assert radar.last_update_success is True

    assert await hass.config_entries.async_reload(first.entry_id)
    await hass.async_block_till_done()
    radar = first.runtime_data.radar
    await radar.async_refresh()
    await hass.async_block_till_done()
    assert radar.last_update_success is True
    assert first.runtime_data.station.last_update_success is True


async def test_unloading_one_entry_keeps_the_other(
    hass: HomeAssistant, two_stations: tuple[MockConfigEntry, MockConfigEntry]
) -> None:
    """Shared objects survive while another entry still uses them."""
    first, second = two_stations

    assert await hass.config_entries.async_unload(second.entry_id)
    await hass.async_block_till_done()

    assert second.state is ConfigEntryState.NOT_LOADED
    assert first.state is ConfigEntryState.LOADED
    assert DOMAIN in hass.data
    assert hass.states.get("camera.praha_ruzyne_weather_radar") is not None
