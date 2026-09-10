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
from .api.merge import (
    MergeSample,
    async_load_frame,
    async_load_latest,
    hour_ends_between,
    sample_frame,
)
from .api.observations import (
    Observation,
    PrecipitationTotal,
    async_load_station_data,
)
from .api.radar import RadarFrame, async_load_latest_frame, frame_age
from .api.stations import Station, StationCatalog, async_load_catalog
from .api.text_forecast import TextForecast, async_load_text_forecasts
from .const import (
    ALERTS_UPDATE_INTERVAL,
    ALERTS_URL,
    DOMAIN,
    MERGE_MAX_FETCHES_PER_UPDATE,
    MERGE_ROLLING_WINDOW,
    MERGE_UPDATE_INTERVAL,
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
    precipitation_today: PrecipitationTotal | None = None

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
            readings = await async_load_station_data(
                self._client,
                self.wsi,
                dt_util.utcnow().date(),
                precipitation_since=dt_util.as_utc(dt_util.start_of_local_day()),
            )
        except ChmiApiError as err:
            raise UpdateFailed(str(err)) from err
        return StationData(
            station=station,
            elements=catalog.elements_for(self.wsi),
            observations=readings.observations,
            precipitation_today=readings.precipitation,
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


@dataclass(slots=True)
class MergeState:
    """Precipitation accumulated at one point from the merged product."""

    latest: MergeSample | None
    today_total: float | None
    today_covered_to: datetime | None
    today_hours: int
    today_hours_expected: int
    rolling_total: float | None
    rolling_hours: int


class ChmiMergeCoordinator(DataUpdateCoordinator[MergeState]):
    """Accumulate the merged 1 hour precipitation estimate at one point.

    The product only exists as 60 minute windows published every 10 minutes,
    so the hourly frames - whose windows do not overlap - are added up for the
    running day and for the last 24 hours, while the newest sliding frame gives
    the hour in progress.  Values already read are kept in memory, so a normal
    update fetches one new frame per hour.
    """

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: ChmiClient
    ) -> None:
        """Initialise the coordinator for the Home Assistant location."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} merged precipitation",
            update_interval=MERGE_UPDATE_INTERVAL,
        )
        self._client = client
        self._location = (hass.config.latitude, hass.config.longitude)
        self._hourly: dict[datetime, float] = {}
        self._unpublished: dict[datetime, int] = {}

    async def _async_update_data(self) -> MergeState:
        """Fetch the frames that are missing and add the windows up."""
        now = dt_util.utcnow()
        midnight = dt_util.as_utc(dt_util.start_of_local_day())
        self._follow_home_location()

        today_hours = hour_ends_between(midnight, now)
        rolling_hours = hour_ends_between(now - MERGE_ROLLING_WINDOW, now)
        wanted = sorted(set(today_hours) | set(rolling_hours))
        self._prune(wanted[0] if wanted else now)

        try:
            await self._async_fill(wanted, now)
            latest = await self._async_latest(now)
        except ChmiApiError as err:
            raise UpdateFailed(str(err)) from err

        today = [self._hourly[hour] for hour in today_hours if hour in self._hourly]
        rolling = [self._hourly[hour] for hour in rolling_hours if hour in self._hourly]
        covered = [hour for hour in today_hours if hour in self._hourly]

        return MergeState(
            latest=latest,
            today_total=round(sum(today), 1) if today else None,
            today_covered_to=max(covered) if covered else None,
            today_hours=len(today),
            today_hours_expected=len(today_hours),
            rolling_total=round(sum(rolling), 1) if rolling else None,
            rolling_hours=len(rolling),
        )

    def _follow_home_location(self) -> None:
        """Pick up a change of the Home Assistant location.

        The stored hourly values belong to the point they were sampled at, so
        they are dropped and read again when the home location moves.
        """
        location = (self.hass.config.latitude, self.hass.config.longitude)
        if self._location == location:
            return
        _LOGGER.debug(
            "Home location moved from %s to %s, re-reading the day",
            self._location,
            location,
        )
        self._hourly.clear()
        self._unpublished.clear()
        self._location = location

    def _prune(self, oldest_wanted: datetime) -> None:
        """Forget windows that no sensor covers any more."""
        for store in (self._hourly, self._unpublished):
            for hour in [hour for hour in store if hour < oldest_wanted]:
                del store[hour]

    async def _async_fill(self, wanted: list[datetime], now: datetime) -> None:
        """Download and sample the windows that are not known yet."""
        fetches = 0
        for hour in wanted:
            if hour in self._hourly:
                continue
            # A frame can be published late, but not indefinitely so.
            if self._unpublished.get(hour, 0) >= 3:
                continue
            if fetches >= MERGE_MAX_FETCHES_PER_UPDATE:
                _LOGGER.debug("Merge backfill continues on the next update")
                break
            fetches += 1
            payload = await async_load_frame(self._client, hour)
            if payload is None:
                self._unpublished[hour] = self._unpublished.get(hour, 0) + 1
                continue
            point = await self.hass.async_add_executor_job(
                sample_frame, payload, *self._location
            )
            if point.window_end != hour:
                _LOGGER.warning(
                    "Merge frame for %s declares the window %s, ignoring it",
                    hour.isoformat(),
                    point.window_end.isoformat(),
                )
                self._unpublished[hour] = 3
                continue
            if point.millimetres is None:
                # Outside the grid or no value at this point.
                self._unpublished[hour] = 3
                continue
            self._hourly[hour] = point.millimetres
            self._unpublished.pop(hour, None)

    async def _async_latest(self, now: datetime) -> MergeSample | None:
        """Sample the newest published sliding window."""
        newest = await async_load_latest(self._client, now=now)
        if newest is None:
            return None
        window_end, payload = newest
        point = await self.hass.async_add_executor_job(
            sample_frame, payload, *self._location
        )
        return MergeSample(
            window_end=point.window_end or window_end,
            millimetres=point.millimetres,
            in_coverage=point.millimetres is not None,
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
