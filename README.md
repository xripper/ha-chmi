# ČHMÚ for Home Assistant

Home Assistant integration for open data of the **Czech Hydrometeorological
Institute** (Český hydrometeorologický ústav). It provides current measurements
from a station you pick during setup, the CZRAD weather radar composite drawn
over a map of Czechia, warnings from the official CAP feed and the regional
text forecast.

Czech version of this document: [README.cs.md](README.cs.md)

## What you get

| Entity | Description |
| --- | --- |
| `weather.<station>` | Current conditions measured at the station. No forecast - see [Why there is no forecast](#why-there-is-no-forecast). |
| `sensor.<station>_*` | One sensor per measured element the station publishes: temperature (also 5 cm above ground, soil at 5-100 cm), humidity, pressure, dew point, wind speed, gusts and bearing, precipitation, snow depth, sunshine duration, global and diffuse radiation, cloud cover, visibility, present weather code. |
| `camera.<station>_weather_radar` | The newest radar composite (5 minute steps) cropped to its georeferenced data area and drawn over the borders of the Czech regions. |
| `sensor.<station>_radar_rain_rate` | Rain rate in mm/h read from the radar pixel above your Home Assistant location. |
| `binary_sensor.<station>_warning` | On while a ČHMÚ warning is in force for your region (not before its onset), with [MeteoalarmCard](https://github.com/MrBartusek/MeteoalarmCard) compatible attributes. |
| `sensor.<station>_active_warnings` | Number of warnings in force, with all of them in the attributes; warnings issued for later carry an `upcoming` attribute. |
| `sensor.<station>_text_forecast` | The forecaster written outlook for your region, today and tomorrow. |

Only the elements a station actually measures become entities. Out of the 475
stations that publish current data, 296 measure temperature, 432 precipitation,
208 wind, 84 pressure and only 34 cloud cover, so the entity list differs a lot
between stations.

## Installation

### HACS

1. HACS → three dot menu → **Custom repositories**
2. Repository `https://github.com/jakubgottwald/ha-chmi`, category **Integration**
3. Install **ČHMÚ**, then restart Home Assistant
4. **Settings → Devices & services → Add integration → ČHMÚ**

### Manual

Copy `custom_components/chmi` into your `config/custom_components` directory and
restart Home Assistant.

## Configuration

The setup dialog lists all stations ordered by distance from your Home Assistant
location, with the distance and altitude in the label; stations already
configured are left out. Radar, warnings and the text forecast are country wide,
so they are only enabled for the first station you add.

- **⋮ → Configure** switches the radar between **maximum reflectivity** and
  **precipitation reaching the ground** and toggles the warnings and the text
  forecast.
- **⋮ → Reconfigure** moves the entry to a different station. Entity ids and
  their recorded history are kept, because entities are identified by the
  config entry rather than by the station.

## How fresh the data is

| Data | Published | Polled |
| --- | --- | --- |
| Station observations | The daily file is rewritten **once per hour**, around HH:02 UTC, and its newest sample is from about HH-1:50 UTC | every 15 min (conditional request, so unchanged files cost nothing) |
| Radar composite | every 5 minutes, 1-3 minutes behind the frame time | every 5 min |
| Warnings (CAP) | on change | every 10 min |
| Text forecast | a few times a day | every 3 h |

**Station values are therefore 10 to 70 minutes old.** That is a property of the
open data service, not of this integration - ČHMÚ publishes no faster feed of
station measurements. Values older than three hours are dropped instead of being
repeated, so a failed instrument makes its sensor unavailable; the same applies
to the radar, which goes unavailable when no frame newer than 30 minutes is
published.

## Data sources

Everything comes from the public services listed below, no account or API key
is needed.

- Stations: `https://opendata.chmi.cz/meteorology/climate/now/`
  (`metadata/meta1-*.json` station list, `metadata/meta2-*.json` elements per
  station, `data/10m-*.json` and `data/1h-*.json` measurements)
- Radar: `https://opendata.chmi.cz/meteorology/weather/radar/composite/maxz/png/`
  and `.../png_masked/`, file name
  `pacz2gmaps3.z_max3d.YYYYMMDD.HHMM.0.png` (UTC)
- Warnings: `https://vystrahy-cr.chmi.cz/data/XOCZ50_OKPR.xml` (CAP v1.2).
  A warning is matched to your region through the CISORP geocodes of its areas,
  so district level and multi-region warnings are handled correctly.
- Text forecast:
  `https://opendata.chmi.cz/meteorology/weather/forecast/now/web_pCK{0,1}tx_R{region}_*.json`

### Radar georeferencing

The published PNG is 680×460 px, but only its lower left **598×378 px** carry
map data; the remaining strips are vertical cross sections and a caption. The
data area is EPSG:3857 with 1 km pixels (1555.68 m in mercator units) and covers
**E 11.267°-19.624°, N 48.047°-51.458°**. The integration crops to that area,
clears the caption rows and reads reflectivity from the single pixel containing
the location by matching its colour against the official dBZ colour scale
(neighbouring pixels are only consulted when that pixel is covered by a drawn
map outline). The rain rate follows from Z = 200 R^1.6, and because the scale
has 4 dBZ classes a reading can understate the reflectivity by up to one class.

### How the weather condition is derived

Station files contain measurements, not a condition, so the entity uses the best
available source in this order:

1. the station's present weather code (`ww`, WMO tables 4677 and 4680) where the
   station reports one - 35 stations do
2. measured precipitation or the radar echo above the station's coordinates,
   split into rain,
   heavy rain, sleet, snow or thunderstorm using the temperature and reflectivity
3. visibility below 1 km → fog
4. cloud cover in eighths (`N`), 34 stations
5. sunshine duration or global radiation compared with clear sky irradiance,
   during daylight only

A station that measures none of these keeps the condition `unknown` rather than
reporting a guess, and after sunset stations without cloud data do the same.

## Why there is no forecast

ČHMÚ publishes its ALADIN model only as GRIB2 files (about 12-30 MB per variable
for the 1 km Czech domain, 60-100 MB for the 2.3 km Lambert domain). Downloading
a usable subset would mean roughly 120 MB per model run four times a day plus a
GRIB2 decoder that cannot be installed on Home Assistant OS. The regional text
forecast written by ČHMÚ forecasters is provided instead.

## Development

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-test.txt
pytest          # 132 tests, fixtures are real ČHMÚ responses
ruff check .
```

## Licence and attribution

The code is MIT licensed. ČHMÚ data are published under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) and every entity
carries the attribution `Data © Český hydrometeorologický ústav (CC BY 4.0)`.
The bundled region boundaries in `custom_components/chmi/data/cz_regions.geojson`
are simplified public administrative boundaries of the Czech Republic.

This project is not affiliated with ČHMÚ.
