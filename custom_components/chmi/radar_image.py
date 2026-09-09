"""Rendering of the radar composite and sampling of reflectivity values."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

from .const import (
    MARSHALL_PALMER_A,
    MARSHALL_PALMER_B,
    RADAR_CAPTION_ROWS,
    RADAR_COLOR_SCALE,
    RADAR_CROP,
    RADAR_DATA_SIZE,
    RADAR_IMAGE_SIZE,
    RADAR_NO_COVERAGE_COLOR,
    RADAR_OUTLINE_COLOR,
)
from .geo import latlon_to_pixel, latlon_to_pixel_unbounded, load_regions

_LOGGER = logging.getLogger(__name__)

_DBZ_BY_COLOR: dict[tuple[int, int, int], int] = dict(RADAR_COLOR_SCALE)

# Colours of the rendered base map
_COLOR_BACKGROUND = (232, 236, 240)
_COLOR_LAND = (250, 250, 248)
_COLOR_REGION_BORDER = (166, 176, 186)
_COLOR_LABEL_BACKGROUND = (255, 255, 255)
_COLOR_LABEL_TEXT = (40, 44, 48)

# Neighbouring pixels (one pixel is 1 km of terrain) are only consulted when
# the pixel of the location itself carries a drawn map outline instead of data.
_NEIGHBOUR_RADIUS = 1


@dataclass(slots=True, frozen=True)
class RadarSample:
    """Reflectivity sampled at one point of the composite."""

    dbz: int | None
    rain_rate: float | None
    in_coverage: bool
    aloft_only: bool


def dbz_to_rain_rate(dbz: float) -> float:
    """Convert reflectivity to a rain rate in mm/h (Marshall-Palmer)."""
    return (10 ** (dbz / 10) / MARSHALL_PALMER_A) ** (1 / MARSHALL_PALMER_B)


def open_data_area(png: bytes) -> Image.Image:
    """Return the map data part of a published radar composite as RGBA.

    The caption ČHMÚ draws over the northernmost rows is cleared, so the image
    contains only georeferenced data.
    """
    with Image.open(BytesIO(png)) as image:
        if image.size != RADAR_IMAGE_SIZE:
            raise ValueError(
                f"Unexpected radar image size {image.size}, "
                f"expected {RADAR_IMAGE_SIZE}"
            )
        data_area = image.convert("RGBA").crop(RADAR_CROP)
    caption = Image.new("RGBA", (data_area.width, RADAR_CAPTION_ROWS), (0, 0, 0, 0))
    data_area.paste(caption, (0, 0))
    return data_area


def _as_data_area(source: Image.Image | bytes) -> Image.Image:
    """Accept either a published PNG or an already cropped data area."""
    if isinstance(source, Image.Image):
        return source
    return open_data_area(source)


def _classify(pixel: tuple[int, int, int, int]) -> tuple[str, int | None]:
    """Classify one pixel of the composite.

    Returns one of ``echo`` (with its reflectivity), ``clear`` (measured, no
    echo), ``aloft`` (precipitation that does not reach the ground, only in the
    masked product), ``outline`` (a drawn map line hiding the data) or
    ``no_coverage``.
    """
    red, green, blue, alpha = pixel
    if alpha == 0:
        return "clear", None
    color = (red, green, blue)
    if color == RADAR_NO_COVERAGE_COLOR:
        return "no_coverage", None
    if color == RADAR_OUTLINE_COLOR:
        return "outline", None
    dbz = _DBZ_BY_COLOR.get(color)
    if dbz is not None:
        return "echo", dbz
    return "aloft", None


def _neighbour_echo(
    image: Image.Image, center_x: int, center_y: int
) -> tuple[str, int | None]:
    """Classification of the strongest neighbour of a hidden pixel."""
    width, height = image.size
    best: tuple[str, int | None] = ("outline", None)
    for offset_y in range(-_NEIGHBOUR_RADIUS, _NEIGHBOUR_RADIUS + 1):
        for offset_x in range(-_NEIGHBOUR_RADIUS, _NEIGHBOUR_RADIUS + 1):
            x, y = center_x + offset_x, center_y + offset_y
            if not (0 <= x < width and 0 <= y < height):
                continue
            kind, dbz = _classify(image.getpixel((x, y)))
            if kind == "echo" and (best[1] is None or dbz > best[1]):
                best = ("echo", dbz)
            elif kind in ("clear", "aloft", "no_coverage") and best[0] == "outline":
                best = (kind, None)
    return best


def sample_reflectivity(
    source: Image.Image | bytes, latitude: float, longitude: float
) -> RadarSample:
    """Sample the composite at the given coordinates.

    The value of the pixel containing the location is used; neighbours are only
    consulted when that pixel is covered by a drawn map outline.
    """
    position = latlon_to_pixel(latitude, longitude)
    if position is None:
        return RadarSample(
            dbz=None, rain_rate=None, in_coverage=False, aloft_only=False
        )

    image = _as_data_area(source)
    center_x, center_y = int(position[0]), int(position[1])
    kind, dbz = _classify(image.getpixel((center_x, center_y)))
    if kind == "outline":
        kind, dbz = _neighbour_echo(image, center_x, center_y)

    if kind == "echo" and dbz is not None:
        return RadarSample(
            dbz=dbz,
            rain_rate=round(dbz_to_rain_rate(dbz), 2),
            in_coverage=True,
            aloft_only=False,
        )
    if kind == "aloft":
        return RadarSample(dbz=None, rain_rate=0.0, in_coverage=True, aloft_only=True)
    if kind == "clear":
        return RadarSample(dbz=None, rain_rate=0.0, in_coverage=True, aloft_only=False)
    return RadarSample(dbz=None, rain_rate=None, in_coverage=False, aloft_only=False)


@lru_cache(maxsize=1)
def _base_map() -> Image.Image:
    """Render the map of Czech regions matching the radar data area."""
    image = Image.new("RGB", RADAR_DATA_SIZE, _COLOR_BACKGROUND)
    draw = ImageDraw.Draw(image)
    outlines: list[list[tuple[float, float]]] = []

    for _name, polygons in load_regions():
        for polygon in polygons:
            for index, ring in enumerate(polygon):
                points = [
                    latlon_to_pixel_unbounded(point[1], point[0]) for point in ring
                ]
                if len(points) < 3:
                    continue
                if index == 0:
                    draw.polygon(points, fill=_COLOR_LAND)
                    outlines.append(points)
                else:
                    draw.polygon(points, fill=_COLOR_BACKGROUND)

    for points in outlines:
        draw.line([*points, points[0]], fill=_COLOR_REGION_BORDER, width=1)

    return image


def _label_font() -> ImageFont.ImageFont:
    """Return a small font for the timestamp label."""
    try:
        return ImageFont.load_default(size=13)
    except TypeError:  # Pillow < 10.1 has no size argument
        return ImageFont.load_default()


def render_composite(source: Image.Image | bytes, label: str) -> bytes:
    """Compose the radar frame over the map of Czechia and return a PNG.

    ``label`` is drawn into the image, so it has to stay ASCII: the bundled
    Pillow font has no glyphs for Czech diacritics.
    """
    radar = _as_data_area(source)
    base = _base_map().copy().convert("RGBA")
    base.alpha_composite(radar)

    draw = ImageDraw.Draw(base)
    font = _label_font()
    text = label
    box = draw.textbbox((0, 0), text, font=font)
    width, height = box[2] - box[0], box[3] - box[1]
    margin = 4
    origin = (margin, RADAR_DATA_SIZE[1] - height - 2 * margin)
    draw.rectangle(
        [
            origin[0] - 2,
            origin[1] - 2,
            origin[0] + width + 4,
            origin[1] + height + 6,
        ],
        fill=_COLOR_LABEL_BACKGROUND,
    )
    draw.text(origin, text, fill=_COLOR_LABEL_TEXT, font=font)

    buffer = BytesIO()
    base.convert("RGB").save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
