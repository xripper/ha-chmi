"""Config flow for the ČHMÚ integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)
from homeassistant.util import dt as dt_util

from .api.client import ChmiApiError, ChmiClient
from .api.stations import StationCatalog, async_load_catalog
from .const import (
    CONF_ALERTS,
    CONF_MERGE,
    CONF_RADAR,
    CONF_RADAR_VARIANT,
    CONF_STATION,
    CONF_STATION_NAME,
    CONF_TEXT_FORECAST,
    DEFAULT_RADAR_VARIANT,
    DOMAIN,
    RADAR_VARIANTS,
)

_LOGGER = logging.getLogger(__name__)


async def _async_load_catalog(hass: HomeAssistant) -> StationCatalog:
    """Load the station catalog, reusing the running integration's cache."""
    shared = hass.data.get(DOMAIN)
    if shared is not None:
        return await shared.catalog.async_get()
    client = ChmiClient(async_get_clientsession(hass))
    return await async_load_catalog(client, dt_util.utcnow().date())


def _station_options(
    catalog: StationCatalog,
    latitude: float,
    longitude: float,
    *,
    taken: set[str],
) -> list[SelectOptionDict]:
    """Build the station picker, nearest station first.

    Stations already used by another config entry are left out, so two entries
    can never end up describing the same station.
    """
    options: list[SelectOptionDict] = []
    for station, distance in catalog.sorted_by_distance(latitude, longitude):
        if station.wsi in taken:
            continue
        elevation = (
            f", {station.elevation:.0f} m" if station.elevation is not None else ""
        )
        options.append(
            SelectOptionDict(
                value=station.wsi,
                label=f"{station.name} – {distance:.0f} km{elevation}",
            )
        )
    return options


def _station_schema(options: list[SelectOptionDict], default: str | None) -> vol.Schema:
    """Return the schema of the station picker."""
    return vol.Schema(
        {
            vol.Required(CONF_STATION, default=default): SelectSelector(
                SelectSelectorConfig(
                    options=options,
                    mode=SelectSelectorMode.DROPDOWN,
                    custom_value=False,
                    sort=False,
                )
            )
        }
    )


class ChmiConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial configuration and reconfiguration."""

    VERSION = 1

    def _taken_stations(self, *, exclude: str | None = None) -> set[str]:
        """Stations already configured by other entries."""
        return {
            entry.data[CONF_STATION]
            for entry in self._async_current_entries(include_ignore=False)
            if entry.entry_id != exclude and CONF_STATION in entry.data
        }

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the measuring station."""
        try:
            catalog = await _async_load_catalog(self.hass)
        except ChmiApiError as err:
            _LOGGER.error("Station list could not be loaded: %s", err)
            return self.async_abort(reason="cannot_connect")

        if user_input is not None:
            wsi = user_input[CONF_STATION]
            station = catalog.stations[wsi]
            await self.async_set_unique_id(wsi)
            self._abort_if_unique_id_configured()
            # Radar, warnings and the text forecast are country wide, so they
            # are only added to the first station of an installation.
            first_entry = not self._async_current_entries()
            return self.async_create_entry(
                title=station.name,
                data={
                    CONF_STATION: wsi,
                    CONF_STATION_NAME: station.name,
                    CONF_RADAR: first_entry,
                    CONF_RADAR_VARIANT: DEFAULT_RADAR_VARIANT,
                    CONF_MERGE: first_entry,
                    CONF_ALERTS: first_entry,
                    CONF_TEXT_FORECAST: first_entry,
                },
            )

        options = _station_options(
            catalog,
            self.hass.config.latitude,
            self.hass.config.longitude,
            taken=self._taken_stations(),
        )
        if not options:
            return self.async_abort(reason="no_stations_left")
        return self.async_show_form(
            step_id="user", data_schema=_station_schema(options, options[0]["value"])
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Move an existing entry to a different station."""
        entry = self._get_reconfigure_entry()
        try:
            catalog = await _async_load_catalog(self.hass)
        except ChmiApiError as err:
            _LOGGER.error("Station list could not be loaded: %s", err)
            return self.async_abort(reason="cannot_connect")

        if user_input is not None:
            wsi = user_input[CONF_STATION]
            if wsi == entry.data[CONF_STATION]:
                return self.async_abort(reason="reconfigure_successful")
            station = catalog.stations[wsi]
            await self.async_set_unique_id(wsi)
            # Aborts when another entry already follows the chosen station.
            self._abort_if_unique_id_configured()
            return self.async_update_reload_and_abort(
                entry,
                unique_id=wsi,
                title=station.name,
                data_updates={
                    CONF_STATION: wsi,
                    CONF_STATION_NAME: station.name,
                },
            )

        options = _station_options(
            catalog,
            self.hass.config.latitude,
            self.hass.config.longitude,
            taken=self._taken_stations(exclude=entry.entry_id),
        )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_station_schema(options, entry.data[CONF_STATION]),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> ChmiOptionsFlow:
        """Return the options flow."""
        return ChmiOptionsFlow()


class ChmiOptionsFlow(OptionsFlow):
    """Handle the optional country wide entities of an entry."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Turn the radar, the warnings and the text forecast on or off."""
        current = {**self.config_entry.data, **self.config_entry.options}

        if user_input is not None:
            return self.async_create_entry(data=user_input)

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_RADAR, default=current.get(CONF_RADAR, True)
                ): BooleanSelector(),
                vol.Required(
                    CONF_RADAR_VARIANT,
                    default=current.get(CONF_RADAR_VARIANT, DEFAULT_RADAR_VARIANT),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=list(RADAR_VARIANTS),
                        mode=SelectSelectorMode.DROPDOWN,
                        translation_key="radar_variant",
                    )
                ),
                vol.Required(
                    CONF_MERGE, default=current.get(CONF_MERGE, True)
                ): BooleanSelector(),
                vol.Required(
                    CONF_ALERTS, default=current.get(CONF_ALERTS, True)
                ): BooleanSelector(),
                vol.Required(
                    CONF_TEXT_FORECAST, default=current.get(CONF_TEXT_FORECAST, True)
                ): BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
