"""Decoding of the WMO code tables used by ČHMÚ station data.

The ``ww`` element carries BUFR descriptor 0 20 003 (present weather):
values 0-99 come from WMO code table 4677 (manned observation), 100-199 from
table 4680 (automatic observation) and 508-511 are the "nothing to report" /
missing codes.  ``VV`` carries WMO code table 4377 (horizontal visibility).
"""

from __future__ import annotations

from homeassistant.components.weather import (
    ATTR_CONDITION_CLOUDY,
    ATTR_CONDITION_EXCEPTIONAL,
    ATTR_CONDITION_FOG,
    ATTR_CONDITION_HAIL,
    ATTR_CONDITION_LIGHTNING,
    ATTR_CONDITION_LIGHTNING_RAINY,
    ATTR_CONDITION_POURING,
    ATTR_CONDITION_RAINY,
    ATTR_CONDITION_SNOWY,
    ATTR_CONDITION_SNOWY_RAINY,
)

# Codes that explicitly mean "no significant weather"; the condition then has to
# be derived from cloud cover instead.
NO_SIGNIFICANT_WEATHER = frozenset({100, 508, 509})
MISSING_WEATHER = frozenset({510, 511})


def present_weather_condition(code: float | int | None) -> str | None:
    """Map a present weather code to a Home Assistant weather condition.

    Returns None when the code carries no usable information (no significant
    weather, missing value or a code describing only cloud evolution).
    """
    if code is None:
        return None
    value = int(code)
    if value in MISSING_WEATHER or value in NO_SIGNIFICANT_WEATHER:
        return None
    if 0 <= value <= 99:
        return _manned_condition(value)
    if 100 <= value <= 199:
        return _automatic_condition(value)
    return None


def _manned_condition(value: int) -> str | None:
    """Map a code of WMO table 4677 (manned observation)."""
    if value <= 3:  # cloud development, no precipitation
        return None
    if 4 <= value <= 9:  # smoke, haze, dust
        return ATTR_CONDITION_EXCEPTIONAL
    if 10 <= value <= 12:
        return ATTR_CONDITION_FOG
    if value == 13:
        return ATTR_CONDITION_LIGHTNING
    if value in (14, 15, 16):  # precipitation not reaching the ground
        return ATTR_CONDITION_CLOUDY
    if value == 17:
        return ATTR_CONDITION_LIGHTNING
    if value in (18, 19):  # squalls, funnel cloud
        return ATTR_CONDITION_EXCEPTIONAL
    if 20 <= value <= 29:  # phenomena during the past hour only
        return None
    if 30 <= value <= 35:  # dust or sandstorm
        return ATTR_CONDITION_EXCEPTIONAL
    if 36 <= value <= 39:  # drifting or blowing snow
        return ATTR_CONDITION_SNOWY
    if 40 <= value <= 49:
        return ATTR_CONDITION_FOG
    if 50 <= value <= 59:  # drizzle
        return ATTR_CONDITION_RAINY
    if 60 <= value <= 65:  # rain
        if value in (63, 64, 65):
            return ATTR_CONDITION_POURING
        return ATTR_CONDITION_RAINY
    if 66 <= value <= 69:  # freezing rain, rain and snow mixed
        return ATTR_CONDITION_SNOWY_RAINY
    if 70 <= value <= 79:  # snow, ice pellets
        return ATTR_CONDITION_SNOWY
    if 80 <= value <= 82:  # rain showers
        return ATTR_CONDITION_POURING if value == 82 else ATTR_CONDITION_RAINY
    if 83 <= value <= 84:  # sleet showers
        return ATTR_CONDITION_SNOWY_RAINY
    if 85 <= value <= 88:  # snow showers
        return ATTR_CONDITION_SNOWY
    if 89 <= value <= 90:  # hail
        return ATTR_CONDITION_HAIL
    if value in (91, 92):  # thunderstorm during the past hour
        return ATTR_CONDITION_LIGHTNING
    if 93 <= value <= 94:  # thunderstorm with snow or hail during the past hour
        return ATTR_CONDITION_SNOWY
    if 95 <= value <= 97:
        return ATTR_CONDITION_LIGHTNING_RAINY
    if value == 98:  # thunderstorm with duststorm
        return ATTR_CONDITION_EXCEPTIONAL
    return ATTR_CONDITION_LIGHTNING_RAINY


def _automatic_condition(value: int) -> str | None:
    """Map a code of WMO table 4680 (automatic observation)."""
    if value in (101, 102, 103):  # clouds dissolving, unchanged or forming
        return ATTR_CONDITION_CLOUDY
    if value in (104, 105):  # haze or smoke
        return ATTR_CONDITION_EXCEPTIONAL
    if value in (110, 111):  # mist, diamond dust
        return ATTR_CONDITION_FOG
    if value == 112:  # distant lightning
        return ATTR_CONDITION_LIGHTNING
    if value == 118:  # squalls
        return ATTR_CONDITION_EXCEPTIONAL
    if 120 <= value <= 129:  # phenomena during the past hour only
        return None
    if 130 <= value <= 135:
        return ATTR_CONDITION_FOG
    if value in (140, 141, 143):
        return ATTR_CONDITION_RAINY
    if value in (142, 144):
        return ATTR_CONDITION_POURING
    if value in (145, 146):
        return ATTR_CONDITION_SNOWY
    if value in (147, 148):
        return ATTR_CONDITION_SNOWY_RAINY
    if 150 <= value <= 158:  # drizzle
        return ATTR_CONDITION_SNOWY_RAINY if value >= 156 else ATTR_CONDITION_RAINY
    if 160 <= value <= 163:  # rain, not freezing
        return ATTR_CONDITION_POURING if value == 163 else ATTR_CONDITION_RAINY
    if 164 <= value <= 168:  # freezing rain, rain and snow mixed
        return ATTR_CONDITION_SNOWY_RAINY
    if 170 <= value <= 178:  # snow
        return ATTR_CONDITION_SNOWY
    if 180 <= value <= 184:  # rain showers
        if value in (182, 184):
            return ATTR_CONDITION_POURING
        return ATTR_CONDITION_RAINY
    if 185 <= value <= 187:  # snow showers
        return ATTR_CONDITION_SNOWY
    if value == 189:  # hail
        return ATTR_CONDITION_HAIL
    if 190 <= value <= 196:
        return ATTR_CONDITION_LIGHTNING_RAINY
    return None


def octas_to_percent(octas: float | int | None) -> float | None:
    """Convert cloud cover in eighths of the sky to percent.

    WMO code 9 means the sky was obscured, which carries no cover value.
    """
    if octas is None:
        return None
    if octas > 8:
        return None
    return octas * 12.5


def visibility_meters(code: float | int | None) -> int | None:
    """Convert WMO code table 4377 to metres."""
    if code is None:
        return None
    value = int(code)
    if 0 <= value <= 50:
        return value * 100
    if 56 <= value <= 80:
        return (value - 50) * 1000
    if 81 <= value <= 88:
        return (30 + (value - 80) * 5) * 1000
    if value == 89:
        return 70000
    automatic = {
        90: 50,
        91: 50,
        92: 200,
        93: 500,
        94: 1000,
        95: 2000,
        96: 4000,
        97: 10000,
        98: 20000,
        99: 50000,
    }
    return automatic.get(value)


def wind_bearing_label(bearing: float | None) -> str | None:
    """Return the 16-point compass abbreviation for a bearing in degrees."""
    if bearing is None:
        return None
    points = (
        "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
        "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
    )
    return points[int((bearing % 360) / 22.5 + 0.5) % 16]
