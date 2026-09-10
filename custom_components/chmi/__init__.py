"""The ČHMÚ (Czech Hydrometeorological Institute) integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api.client import ChmiClient
from .const import (
    CONF_ALERTS,
    CONF_MERGE,
    CONF_RADAR,
    CONF_RADAR_VARIANT,
    CONF_STATION,
    CONF_TEXT_FORECAST,
    DEFAULT_RADAR_VARIANT,
    DOMAIN,
)
from .coordinator import (
    ChmiAlertsCoordinator,
    ChmiCatalog,
    ChmiMergeCoordinator,
    ChmiRadarCoordinator,
    ChmiStationCoordinator,
    ChmiTextForecastCoordinator,
)
from .geo import forecast_code_for_region, nearest_region, region_for_point

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.CAMERA,
    Platform.SENSOR,
    Platform.WEATHER,
]


@dataclass(slots=True)
class ChmiRuntimeData:
    """Objects used by the platforms of one config entry."""

    station: ChmiStationCoordinator
    radar: ChmiRadarCoordinator | None = None
    merge: ChmiMergeCoordinator | None = None
    alerts: ChmiAlertsCoordinator | None = None
    text_forecast: ChmiTextForecastCoordinator | None = None
    region: str | None = None


@dataclass(slots=True)
class ChmiShared:
    """Objects shared by all config entries of the integration.

    Only stateless helpers are shared.  Every coordinator belongs to a single
    config entry, because Home Assistant shuts a coordinator down for good when
    the entry that created it is unloaded or reloaded.
    """

    client: ChmiClient
    catalog: ChmiCatalog


type ChmiConfigEntry = ConfigEntry[ChmiRuntimeData]


def _shared(hass: HomeAssistant) -> ChmiShared:
    """Return the shared objects, creating them on first use."""
    if DOMAIN not in hass.data:
        client = ChmiClient(async_get_clientsession(hass))
        hass.data[DOMAIN] = ChmiShared(client=client, catalog=ChmiCatalog(client))
    return hass.data[DOMAIN]


def _home_region(hass: HomeAssistant) -> str | None:
    """Region of the Home Assistant location (blocking, loads the boundaries).

    Locations just outside the simplified boundaries - or a few hundred metres
    across a river - fall back to the closest region, so warnings and the text
    forecast stay regional instead of country wide.
    """
    latitude, longitude = hass.config.latitude, hass.config.longitude
    return region_for_point(latitude, longitude) or nearest_region(
        latitude, longitude
    )


async def async_setup_entry(hass: HomeAssistant, entry: ChmiConfigEntry) -> bool:
    """Set up ČHMÚ from a config entry."""
    shared = _shared(hass)
    options = {**entry.data, **entry.options}

    station = ChmiStationCoordinator(
        hass, entry, shared.client, shared.catalog, options[CONF_STATION]
    )
    await station.async_config_entry_first_refresh()

    region = await hass.async_add_executor_job(_home_region, hass)
    runtime = ChmiRuntimeData(station=station, region=region)

    if options.get(CONF_RADAR, True):
        location = (station.data.station.latitude, station.data.station.longitude)
        radar = ChmiRadarCoordinator(
            hass,
            entry,
            shared.client,
            options.get(CONF_RADAR_VARIANT, DEFAULT_RADAR_VARIANT),
            location,
        )
        await radar.async_config_entry_first_refresh()
        runtime.radar = radar

    if options.get(CONF_MERGE, True):
        merge = ChmiMergeCoordinator(hass, entry, shared.client)
        await merge.async_config_entry_first_refresh()
        runtime.merge = merge

    if options.get(CONF_ALERTS, True):
        alerts = ChmiAlertsCoordinator(hass, entry, shared.client)
        await alerts.async_config_entry_first_refresh()
        runtime.alerts = alerts

    if options.get(CONF_TEXT_FORECAST, True):
        code = forecast_code_for_region(region)
        if code is None:
            _LOGGER.warning(
                "Home location is outside Czechia, the regional text forecast "
                "will not be provided"
            )
        else:
            forecast = ChmiTextForecastCoordinator(hass, entry, shared.client, code)
            await forecast.async_config_entry_first_refresh()
            runtime.text_forecast = forecast

    entry.runtime_data = runtime
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ChmiConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded and not [
        other
        for other in hass.config_entries.async_loaded_entries(DOMAIN)
        if other.entry_id != entry.entry_id
    ]:
        hass.data.pop(DOMAIN, None)
    return unloaded


async def _async_update_listener(hass: HomeAssistant, entry: ChmiConfigEntry) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)
