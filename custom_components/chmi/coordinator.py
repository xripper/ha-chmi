"""Data update coordinators for the ČHMÚ integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api.alerts import Alert, parse_alerts
from .api.client import ChmiApiError, ChmiClient
from .api.observations import Observation, async_load_station_data
from .api.radar import RadarFrame, async_load_latest_frame, frame_age
from .api.stations import Station, StationCatalog, async_load_catalog
from .api.text_forecast import TextForecast, async_load_text_forecasts
from .const import (
    ALERTS_UPDATE_INTERVAL,
    ALERTS_URL,
    DOMAIN,
    METADATA_MAX_AGE,
    RADAR_MAX_AGE,
    RADAR_UPDATE_INTERVAL,
    STATION_UPDATE_INTERVAL,
    TEXT_FORECAST_UPDATE_INTERVAL,
)
from .radar_image import (
    RadarSample,
    open_data_area,
    render_composite,
    sample_reflectivity,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class StationData:
    """Everything known about the configured station."""

    station: Station
    elements: frozenset[str]
    observations: dict[str, Observation]

    def value(self, element: str) -> float | None:
        """Return the newest value of an element."""
        observation = self.observations.get(element)
        return observation.value if observation else None


@dataclass(slots=True)
class RadarState:
    """The newest radar composite together with its derived products."""

    frame: RadarFrame
    image: bytes
    home_sample: RadarSample
    station_sample: RadarSample


class ChmiCatalog:
    """Shared, periodically refreshed station catalog."""

    def __init__(self, client: ChmiClient) -> None:
        """Initialise an empty catalog."""
        self._client = client
        self._catalog: StationCatalog | None = None
        self._loaded_at: datetime | None = None

    async def async_get(self, *, force: bool = False) -> StationCatalog:
        """Return the catalog, reloading it when it is stale."""
        now = dt_util.utcnow()
        if (
            force
            or self._catalog is None
            or self._loaded_at is None
            or now - self._loaded_at > METADATA_MAX_AGE
        ):
            self._catalog = await async_load_catalog(self._client, now.date())
            self._loaded_at = now
        return self._catalog


class ChmiStationCoordinator(DataUpdateCoordinator[StationData]):
    """Poll the observations of a single station."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: ChmiClient,
        catalog: ChmiCatalog,
        wsi: str,
    ) -> None:
        """Initialise the coordinator for one station."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} station {wsi}",
            update_interval=STATION_UPDATE_INTERVAL,
        )
        self._client = client
        self._catalog = catalog
        self.wsi = wsi

    async def _async_update_data(self) -> StationData:
        """Fetch the newest observations of the station."""
        try:
            catalog = await self._catalog.async_get()
            station = catalog.stations.get(self.wsi)
            if station is None:
                raise UpdateFailed(f"Station {self.wsi} is no longer published")
            observations = await async_load_station_data(
                self._client, self.wsi, dt_util.utcnow().date()
            )
        except ChmiApiError as err:
            raise UpdateFailed(str(err)) from err
        return StationData(
            station=station,
            elements=catalog.elements_for(self.wsi),
            observations=observations,
        )


class ChmiRadarCoordinator(DataUpdateCoordinator[RadarState]):
    """Poll the radar composite and render the map overlay."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: ChmiClient,
        variant: str,
        station_location: tuple[float, float],
    ) -> None:
        """Initialise the radar coordinator for one station."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} radar {variant}",
            update_interval=RADAR_UPDATE_INTERVAL,
        )
        self._client = client
        self._station_location = station_location
        self.variant = variant

    async def _async_update_data(self) -> RadarState:
        """Download the newest frame and derive image and point samples."""
        previous = self.data.frame if self.data else None
        try:
            frame = await async_load_latest_frame(
                self._client, self.variant, previous=previous
            )
        except ChmiApiError as err:
            raise UpdateFailed(str(err)) from err

        age = frame_age(frame)
        if age > RADAR_MAX_AGE:
            raise UpdateFailed(
                f"Newest radar composite is {age} old, "
                f"the last published frame is {frame.frame_time.isoformat()}"
            )

        if self.data is not None and frame is previous:
            return self.data

        # The home location can change while Home Assistant runs.
        home = (self.hass.config.latitude, self.hass.config.longitude)
        return await self.hass.async_add_executor_job(self._render, frame, home)

    def _render(self, frame: RadarFrame, home: tuple[float, float]) -> RadarState:
        """Render the composite and sample the two locations (blocking)."""
        label = dt_util.as_local(frame.frame_time).strftime("%Y-%m-%d %H:%M")
        try:
            data_area = open_data_area(frame.png)
            image = render_composite(data_area, label)
            home_sample = sample_reflectivity(data_area, *home)
            station_sample = sample_reflectivity(data_area, *self._station_location)
        except (OSError, ValueError) as err:
            raise UpdateFailed(f"Radar frame could not be processed: {err}") from err
        return RadarState(
            frame=frame,
            image=image,
            home_sample=home_sample,
            station_sample=station_sample,
        )


class ChmiAlertsCoordinator(DataUpdateCoordinator[list[Alert]]):
    """Poll the CAP warning feed."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: ChmiClient
    ) -> None:
        """Initialise the shared alerts coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} alerts",
            update_interval=ALERTS_UPDATE_INTERVAL,
        )
        self._client = client

    async def _async_update_data(self) -> list[Alert]:
        """Download and parse the CAP feed."""
        try:
            payload = await self._client.async_get_bytes(ALERTS_URL)
        except ChmiApiError as err:
            raise UpdateFailed(str(err)) from err
        if payload is None:
            raise UpdateFailed("Empty CAP feed")
        try:
            return await self.hass.async_add_executor_job(parse_alerts, payload)
        except ChmiApiError as err:
            raise UpdateFailed(str(err)) from err


class ChmiTextForecastCoordinator(DataUpdateCoordinator[dict[str, TextForecast]]):
    """Poll the regional text forecasts."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: ChmiClient, code: str
    ) -> None:
        """Initialise the coordinator for one regional forecast code."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} text forecast {code}",
            update_interval=TEXT_FORECAST_UPDATE_INTERVAL,
        )
        self._client = client
        self.code = code

    async def _async_update_data(self) -> dict[str, TextForecast]:
        """Download the newest text forecasts."""
        try:
            return await async_load_text_forecasts(self._client, self.code)
        except ChmiApiError as err:
            raise UpdateFailed(str(err)) from err
