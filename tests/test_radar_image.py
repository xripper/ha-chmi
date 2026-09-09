"""Tests of the radar image processing."""

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from custom_components.chmi.const import RADAR_CAPTION_ROWS, RADAR_DATA_SIZE
from custom_components.chmi.radar_image import (
    dbz_to_rain_rate,
    open_data_area,
    render_composite,
    sample_reflectivity,
)
from tests.conftest import load_bytes


@pytest.fixture(name="frame")
def frame_fixture() -> bytes:
    """Return the published maximum reflectivity composite."""
    return load_bytes("radar_maxz.png")


@pytest.fixture(name="masked_frame")
def masked_frame_fixture() -> bytes:
    """Return the composite masked to precipitation reaching the ground."""
    return load_bytes("radar_maxz_masked.png")


def test_composite_has_the_size_of_the_data_area(frame: bytes) -> None:
    """The rendered image covers exactly the georeferenced data area."""
    image = Image.open(BytesIO(render_composite(frame, "2026-09-09 13:50")))
    assert image.size == RADAR_DATA_SIZE
    assert image.format == "PNG"


def test_composite_removes_the_burned_in_caption(frame: bytes) -> None:
    """The rows carrying the ČHMÚ caption are replaced by the base map."""
    raw = Image.open(BytesIO(frame)).convert("RGBA")
    caption_pixels = [
        raw.getpixel((x, 82 + y))
        for x in range(0, 200, 10)
        for y in range(RADAR_CAPTION_ROWS)
    ]
    assert any(pixel[:3] == (0, 0, 0) for pixel in caption_pixels), (
        "fixture is expected to contain the black caption"
    )

    rendered = Image.open(
        BytesIO(render_composite(frame, "2026-09-09 13:50"))
    ).convert("RGB")
    rendered_caption = [
        rendered.getpixel((x, y))
        for x in range(0, 200, 10)
        for y in range(RADAR_CAPTION_ROWS)
    ]
    assert all(pixel != (0, 0, 0) for pixel in rendered_caption)


def test_sample_over_precipitation(frame: bytes) -> None:
    """A point under the rain band reports its own pixel, not the neighbourhood."""
    sample = sample_reflectivity(frame, 49.7384, 13.3736)  # Plzeň
    assert sample.in_coverage is True
    assert sample.dbz == 24
    assert sample.rain_rate == pytest.approx(1.15, abs=0.05)


def test_sample_uses_the_pixel_of_the_location(frame: bytes) -> None:
    """The sampled value equals the colour of the pixel itself."""
    from custom_components.chmi.geo import latlon_to_pixel
    from custom_components.chmi.radar_image import _DBZ_BY_COLOR

    image = open_data_area(frame)
    x, y = latlon_to_pixel(49.7384, 13.3736)
    pixel = image.getpixel((int(x), int(y)))
    assert _DBZ_BY_COLOR[pixel[:3]] == sample_reflectivity(frame, 49.7384, 13.3736).dbz


def test_caption_rows_are_cleared_before_sampling(frame: bytes) -> None:
    """The caption pixels never reach the reflectivity lookup."""
    image = open_data_area(frame)
    assert all(
        image.getpixel((x, y))[3] == 0
        for x in range(0, 200, 10)
        for y in range(RADAR_CAPTION_ROWS)
    )


def test_sample_without_echo(frame: bytes) -> None:
    """A point without an echo reports zero, not unknown."""
    sample = sample_reflectivity(frame, 49.1951, 16.6068)  # Brno
    assert sample.in_coverage is True
    assert sample.dbz is None
    assert sample.rain_rate == 0.0


def test_sample_outside_the_composite(frame: bytes) -> None:
    """A point outside the composite is reported as not covered."""
    sample = sample_reflectivity(frame, 45.0, 14.0)
    assert sample.in_coverage is False
    assert sample.rain_rate is None


def test_masked_product_marks_precipitation_aloft(masked_frame: bytes) -> None:
    """The masked product reports rain that evaporates before the ground."""
    sample = sample_reflectivity(masked_frame, 50.0693, 14.4278)  # Praha
    assert sample.in_coverage is True
    assert sample.rain_rate == 0.0
    assert sample.aloft_only is True


def test_rejects_images_of_an_unexpected_size() -> None:
    """A layout change of the published PNG has to fail loudly."""
    buffer = BytesIO()
    Image.new("RGB", (100, 100)).save(buffer, format="PNG")
    with pytest.raises(ValueError, match="Unexpected radar image size"):
        sample_reflectivity(buffer.getvalue(), 50.0, 14.0)


@pytest.mark.parametrize(
    ("dbz", "rain_rate"),
    [(4, 0.065), (20, 0.648), (36, 6.484), (52, 64.842)],
)
def test_marshall_palmer_conversion(dbz: int, rain_rate: float) -> None:
    """Reflectivity converts to a rain rate with Z = 200 R^1.6."""
    assert dbz_to_rain_rate(dbz) == pytest.approx(rain_rate, rel=0.01)
