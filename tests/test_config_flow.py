"""Tests of the config and options flow."""

from __future__ import annotations

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
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
    RADAR_VARIANT_PNG,
)
from tests.conftest import load_text

PRAGUE_RUZYNE = "0-20000-0-11518"
PRAGUE_KARLOV = "0-20000-0-11519"
OPENDATA = "https://opendata.chmi.cz"


@pytest.fixture(name="catalog")
def catalog_fixture(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer):
    """Serve the station metadata."""
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
    return aioclient_mock


async def test_user_flow_lists_stations_by_distance(
    hass: HomeAssistant, custom_integration: None, catalog: AiohttpClientMocker
) -> None:
    """The nearest station is preselected and labelled with its distance."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    selector = result["data_schema"].schema[CONF_STATION]
    options = selector.config["options"]
    assert options[0]["value"] in (PRAGUE_KARLOV, PRAGUE_RUZYNE)
    assert "km" in options[0]["label"]
    assert [option["value"] for option in options][-1] == "0-20000-0-11464"


async def test_user_flow_creates_entry(
    hass: HomeAssistant, custom_integration: None, catalog: AiohttpClientMocker
) -> None:
    """The first entry also gets the country wide entities."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_STATION: PRAGUE_RUZYNE}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Praha, Ruzyně"
    assert result["data"] == {
        CONF_STATION: PRAGUE_RUZYNE,
        CONF_STATION_NAME: "Praha, Ruzyně",
        CONF_RADAR: True,
        CONF_RADAR_VARIANT: "png_masked",
        CONF_MERGE: True,
        CONF_ALERTS: True,
        CONF_TEXT_FORECAST: True,
    }


async def test_second_entry_has_no_country_wide_entities(
    hass: HomeAssistant, custom_integration: None, catalog: AiohttpClientMocker
) -> None:
    """Radar, warnings and forecast are not duplicated for a second station."""
    MockConfigEntry(
        domain=DOMAIN, unique_id=PRAGUE_RUZYNE, data={CONF_STATION: PRAGUE_RUZYNE}
    ).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_STATION: PRAGUE_KARLOV}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_RADAR] is False
    assert result["data"][CONF_MERGE] is False
    assert result["data"][CONF_ALERTS] is False
    assert result["data"][CONF_TEXT_FORECAST] is False


async def test_configured_stations_are_not_offered_again(
    hass: HomeAssistant, custom_integration: None, catalog: AiohttpClientMocker
) -> None:
    """A station already configured is left out of the picker."""
    MockConfigEntry(
        domain=DOMAIN, unique_id=PRAGUE_RUZYNE, data={CONF_STATION: PRAGUE_RUZYNE}
    ).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    options = result["data_schema"].schema[CONF_STATION].config["options"]
    values = [option["value"] for option in options]
    assert PRAGUE_RUZYNE not in values
    assert PRAGUE_KARLOV in values


async def test_flow_aborts_without_metadata(
    hass: HomeAssistant,
    custom_integration: None,
    aioclient_mock: AiohttpClientMocker,
    freezer,
) -> None:
    """An unreachable open data server aborts the flow."""
    freezer.move_to("2026-09-09T11:52:00+00:00")
    aioclient_mock.get(
        f"{OPENDATA}/meteorology/climate/now/metadata/meta1-20260909.json", status=404
    )
    aioclient_mock.get(
        f"{OPENDATA}/meteorology/climate/now/metadata/meta2-20260909.json", status=404
    )
    aioclient_mock.get(
        f"{OPENDATA}/meteorology/climate/now/metadata/meta1-20260908.json", status=404
    )
    aioclient_mock.get(
        f"{OPENDATA}/meteorology/climate/now/metadata/meta2-20260908.json", status=404
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


def _entry(**overrides) -> MockConfigEntry:
    """Return a configured entry for the Ruzyně station."""
    data = {
        CONF_STATION: PRAGUE_RUZYNE,
        CONF_STATION_NAME: "Praha, Ruzyně",
        CONF_RADAR: True,
        CONF_RADAR_VARIANT: "png_masked",
        CONF_MERGE: True,
        CONF_ALERTS: True,
        CONF_TEXT_FORECAST: True,
    }
    data.update(overrides)
    return MockConfigEntry(
        domain=DOMAIN, title="Praha, Ruzyně", unique_id=PRAGUE_RUZYNE, data=data
    )


async def test_options_flow_toggles_country_wide_entities(
    hass: HomeAssistant, custom_integration: None, catalog: AiohttpClientMocker
) -> None:
    """The options flow only switches the optional entities."""
    entry = _entry()
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    assert CONF_STATION not in result["data_schema"].schema

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_RADAR: True,
            CONF_RADAR_VARIANT: RADAR_VARIANT_PNG,
            CONF_MERGE: False,
            CONF_ALERTS: False,
            CONF_TEXT_FORECAST: False,
        },
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.unique_id == PRAGUE_RUZYNE  # identity untouched
    assert entry.data[CONF_STATION] == PRAGUE_RUZYNE
    assert entry.options[CONF_RADAR_VARIANT] == RADAR_VARIANT_PNG
    assert entry.options[CONF_ALERTS] is False


async def test_reconfigure_changes_the_station_and_keeps_the_entities(
    hass: HomeAssistant, custom_integration: None, catalog: AiohttpClientMocker
) -> None:
    """Moving an entry to another station keeps the entity registry entries."""
    entry = _entry()
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    kept = registry.async_get_or_create(
        "sensor", DOMAIN, f"{entry.entry_id}_temperature", config_entry=entry
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_STATION: PRAGUE_KARLOV}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.unique_id == PRAGUE_KARLOV
    assert entry.title == "Praha, Karlov"
    assert entry.data[CONF_STATION] == PRAGUE_KARLOV

    # The entity keeps its id, and with it its recorded history.
    assert registry.async_get(kept.entity_id) is not None
    assert registry.async_get(kept.entity_id).unique_id == kept.unique_id


async def test_reconfigure_cannot_take_a_station_of_another_entry(
    hass: HomeAssistant, custom_integration: None, catalog: AiohttpClientMocker
) -> None:
    """The station of another entry is not offered during reconfiguration."""
    first = _entry()
    first.add_to_hass(hass)
    second = MockConfigEntry(
        domain=DOMAIN,
        title="Praha, Karlov",
        unique_id=PRAGUE_KARLOV,
        data={CONF_STATION: PRAGUE_KARLOV, CONF_STATION_NAME: "Praha, Karlov"},
    )
    second.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": first.entry_id,
        },
    )
    options = result["data_schema"].schema[CONF_STATION].config["options"]
    values = [option["value"] for option in options]
    assert PRAGUE_KARLOV not in values
    assert PRAGUE_RUZYNE in values

    # Submitting it anyway - bypassing the picker - is refused as well.
    with pytest.raises(InvalidData):
        await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_STATION: PRAGUE_KARLOV}
        )
    assert first.unique_id == PRAGUE_RUZYNE
    assert first.data[CONF_STATION] == PRAGUE_RUZYNE
