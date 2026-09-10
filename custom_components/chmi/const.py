"""Constants for the ČHMÚ (Czech Hydrometeorological Institute) integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "chmi"

ATTRIBUTION: Final = "Data © Český hydrometeorologický ústav (CC BY 4.0)"

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
CONF_STATION: Final = "station"
CONF_STATION_NAME: Final = "station_name"
CONF_RADAR: Final = "radar"
CONF_RADAR_VARIANT: Final = "radar_variant"
CONF_ALERTS: Final = "alerts"
CONF_TEXT_FORECAST: Final = "text_forecast"
CONF_MERGE: Final = "merge_precipitation"

RADAR_VARIANT_PNG: Final = "png"
RADAR_VARIANT_MASKED: Final = "png_masked"
RADAR_VARIANTS: Final = (RADAR_VARIANT_PNG, RADAR_VARIANT_MASKED)

DEFAULT_RADAR_VARIANT: Final = RADAR_VARIANT_MASKED

# ---------------------------------------------------------------------------
# Update intervals
#
# Station files on the open-data server are rewritten once per hour (around
# HH:02 UTC, newest sample ~HH-1:50 UTC), so polling more often only produces
# HTTP 304 replies.  Radar composites are published every 5 minutes.
# ---------------------------------------------------------------------------
STATION_UPDATE_INTERVAL: Final = timedelta(minutes=15)
# A value older than this is treated as no longer current: the daily file is
# rewritten hourly and its newest sample is about 70 minutes old at worst, so
# anything beyond three hours means the instrument or the feed stopped.
OBSERVATION_MAX_AGE: Final = timedelta(hours=3)
RADAR_UPDATE_INTERVAL: Final = timedelta(minutes=5)
ALERTS_UPDATE_INTERVAL: Final = timedelta(minutes=10)
MERGE_UPDATE_INTERVAL: Final = timedelta(minutes=10)
TEXT_FORECAST_UPDATE_INTERVAL: Final = timedelta(hours=3)
METADATA_MAX_AGE: Final = timedelta(hours=12)

# ---------------------------------------------------------------------------
# Open data endpoints
# ---------------------------------------------------------------------------
OPENDATA_BASE: Final = "https://opendata.chmi.cz"
NOW_METADATA_URL: Final = (
    OPENDATA_BASE + "/meteorology/climate/now/metadata/{name}-{date}.json"
)
NOW_DATA_URL: Final = (
    OPENDATA_BASE + "/meteorology/climate/now/data/{dataset}-{wsi}-{date}.json"
)
RADAR_URL: Final = (
    OPENDATA_BASE + "/meteorology/weather/radar/composite/maxz/{variant}"
    "/pacz2gmaps3.z_max3d.{date}.{time}.0.png"
)
MERGE_URL: Final = (
    OPENDATA_BASE + "/meteorology/weather/radar/composite/merge1h/hdf5/"
    "T_PASV23_C_OKPR_{stamp}.hdf"
)
TEXT_FORECAST_INDEX_URL: Final = OPENDATA_BASE + "/meteorology/weather/forecast/now/"
TEXT_FORECAST_URL: Final = OPENDATA_BASE + "/meteorology/weather/forecast/now/{name}"
ALERTS_URL: Final = "https://vystrahy-cr.chmi.cz/data/XOCZ50_OKPR.xml"

DATASET_10M: Final = "10m"
DATASET_1H: Final = "1h"

# Elements carrying a precipitation amount, most detailed first.  Only one of
# them is ever summed, because they describe the same rain.
PRECIPITATION_ELEMENTS: Final = ("SRA10M", "SRA1H")

# ---------------------------------------------------------------------------
# Radar geometry
#
# Verified against radar_description_en.pdf (v1.3) and the ODIM HDF5 attributes
# of the same product (xsize=598, ysize=378, xscale=yscale=1555.7 m,
# projdef "+proj=merc ... +a=+b=6378137").
#
# The published PNG is 680x460 px; only its lower-left 598x378 px are map data,
# the remaining strips are vertical cross sections plus a text label.
# ---------------------------------------------------------------------------
RADAR_IMAGE_SIZE: Final = (680, 460)
RADAR_CROP: Final = (0, 82, 598, 460)  # left, upper, right, lower
RADAR_DATA_SIZE: Final = (598, 378)
# west, south, east, north (degrees) of the cropped data area
RADAR_BBOX: Final = (11.267, 48.047, 19.624, 51.458)
RADAR_PIXEL_METERS: Final = 1555.6805  # EPSG:3857 metres per pixel
EARTH_RADIUS: Final = 6378137.0

# ČHMÚ burns a "CZRAD - Z: MAX ..." caption into the top rows of the data area.
# Those rows lie about 30 km north of the Czech border, so they are cleared
# before the frame is drawn on the map.
RADAR_CAPTION_ROWS: Final = 16

RADAR_FRAME_STEP: Final = timedelta(minutes=5)
RADAR_MAX_LOOKBACK: Final = 8  # frames tried backwards when the newest is missing
# Frames are published every five minutes; beyond this the held frame is stale
# and the entities go unavailable instead of showing an old situation.
RADAR_MAX_AGE: Final = timedelta(minutes=30)

# ---------------------------------------------------------------------------
# Merged 1h precipitation estimate (radar and rain gauges, kriging with
# external drift).  Published every 10 minutes on the same 598x378 grid as the
# radar composite; the file name carries the END of the 60 minute window.
# ---------------------------------------------------------------------------
MERGE_STEP: Final = timedelta(minutes=10)
MERGE_WINDOW: Final = timedelta(hours=1)
# Frames appear about 20 minutes after the end of their window.
MERGE_MAX_LOOKBACK: Final = 6
# Bounds the burst after a restart, when the whole day has to be filled in.
MERGE_MAX_FETCHES_PER_UPDATE: Final = 26
MERGE_ROLLING_WINDOW: Final = timedelta(hours=24)
MERGE_DATASET: Final = "dataset1/data1"

# Reflectivity classes of the ČHMÚ colour scale, ordered from the weakest to the
# strongest class, 4 dBZ per step.  The colours were read out of the published
# legend https://opendata.chmi.cz/meteorology/weather/radar/scl/scl-dbz-mmh.png
# and the value is the lower bound of each class, so a reading can understate
# the reflectivity by up to 4 dBZ.
RADAR_COLOR_SCALE: Final = (
    ((56, 0, 112), 4),
    ((48, 0, 168), 8),
    ((0, 0, 252), 12),
    ((0, 108, 192), 16),
    ((0, 160, 0), 20),
    ((0, 188, 0), 24),
    ((52, 216, 0), 28),
    ((156, 220, 0), 32),
    ((224, 220, 0), 36),
    ((252, 176, 0), 40),
    ((252, 132, 0), 44),
    ((252, 88, 0), 48),
    ((252, 0, 0), 52),
    ((160, 0, 0), 56),
    ((252, 252, 252), 60),
)
# Grey shade used for the area outside radar coverage.
RADAR_NO_COVERAGE_COLOR: Final = (196, 196, 196)
# Map outlines and the caption are drawn in black over the data.
RADAR_OUTLINE_COLOR: Final = (0, 0, 0)

# Marshall-Palmer Z = 200 * R^1.6, used to convert dBZ to mm/h.
MARSHALL_PALMER_A: Final = 200.0
MARSHALL_PALMER_B: Final = 1.6

# ---------------------------------------------------------------------------
# Region mapping (bundled cz_regions.geojson -> ČHMÚ regional forecast code)
# ---------------------------------------------------------------------------
REGIONS_GEOJSON: Final = "cz_regions.geojson"
REGION_FORECAST_CODES: Final = {
    "Hlavní město Praha": "RPPH",
    "Středočeský kraj": "RPSC",
    "Jihočeský kraj": "RPCB",
    "Plzeňský kraj": "RPPL",
    "Karlovarský kraj": "RPKV",
    "Ústecký kraj": "RPUL",
    "Liberecký kraj": "RPLB",
    "Královéhradecký kraj": "RPHK",
    "Pardubický kraj": "RPPU",
    "Kraj Vysočina": "RPVY",
    "Jihomoravský kraj": "RPJM",
    "Olomoucký kraj": "RPOL",
    "Zlínský kraj": "RPZL",
    "Moravskoslezský kraj": "RPMS",
}

# ---------------------------------------------------------------------------
# Quality flags (metadata file meta4)
# ---------------------------------------------------------------------------
QUALITY_GOOD: Final = 0
QUALITY_SUSPECT: Final = 1
QUALITY_POOR: Final = 2
QUALITY_ESTIMATED: Final = 3
QUALITY_MISSING: Final = 4
QUALITY_UNKNOWN: Final = 5

QUALITY_LABELS: Final = {
    QUALITY_GOOD: "good",
    QUALITY_SUSPECT: "suspect",
    QUALITY_POOR: "poor",
    QUALITY_ESTIMATED: "estimated",
    QUALITY_MISSING: "missing",
    QUALITY_UNKNOWN: "unknown",
}

# Values with these quality codes are not published as states.
QUALITY_REJECTED: Final = frozenset({QUALITY_MISSING, QUALITY_POOR})

# ---------------------------------------------------------------------------
# Regions of the CAP feed
#
# Every CAP area carries CISORP geocodes of the municipalities it covers; their
# first two digits identify the region, which makes the region match independent
# of how ČHMÚ words the area description ("Aglomerace Brno", "Třinecko",
# "kraje Karlovarský a Ústecký").
# ---------------------------------------------------------------------------
REGION_CISORP_PREFIXES: Final = {
    "Hlavní město Praha": "11",
    "Středočeský kraj": "21",
    "Jihočeský kraj": "31",
    "Plzeňský kraj": "32",
    "Karlovarský kraj": "41",
    "Ústecký kraj": "42",
    "Liberecký kraj": "51",
    "Královéhradecký kraj": "52",
    "Pardubický kraj": "53",
    "Kraj Vysočina": "61",
    "Jihomoravský kraj": "62",
    "Olomoucký kraj": "71",
    "Zlínský kraj": "72",
    "Moravskoslezský kraj": "81",
}
