import io

import pytest
from PIL import Image

from conftest import png, size
from eecc_redact.errors import AppError
from eecc_redact.imaging import burn, to_png
from eecc_redact.models import Box


def pixel(data: bytes, x: int, y: int) -> tuple:
    with Image.open(io.BytesIO(data)) as image:
        return image.getpixel((x, y))


def test_to_png_re_encodes_and_keeps_the_size():
    buffer = io.BytesIO()
    Image.new("RGB", (80, 20), "white").save(buffer, format="JPEG")
    out = to_png(buffer.getvalue())
    assert out.startswith(b"\x89PNG")
    assert size(out) == (80, 20)


def test_to_png_refuses_what_is_not_an_image():
    with pytest.raises(AppError, match="cannot read that file as an image") as caught:
        to_png(b"not a picture")
    assert caught.value.detail == ""  # Pillow's BytesIO repr helps nobody


def test_burn_covers_exactly_the_reported_box():
    """A box w wide covers w pixels, not w+1: Pillow's rectangle includes both corners."""
    out = burn(png((60, 20)), [Box(10, 5, 8, 6)])
    assert pixel(out, 10, 5) == (0, 0, 0)
    assert pixel(out, 17, 10) == (0, 0, 0)
    assert pixel(out, 18, 5) == (255, 255, 255)
    assert pixel(out, 10, 11) == (255, 255, 255)
    assert pixel(out, 5, 5) == (255, 255, 255)


def test_padding_grows_the_box_and_clamps_at_the_origin():
    assert Box(0, 0, 4, 4).padded(3) == Box(0, 0, 7, 7)
    assert Box(10, 10, 4, 4).padded(2) == Box(8, 8, 8, 8)
    out = burn(png(), [Box(10, 10, 20, 10)], padding=4)
    assert pixel(out, 6, 6) == (0, 0, 0)
    assert pixel(out, 5, 5) == (255, 255, 255)


def test_box_containment():
    assert Box(0, 0, 10, 10).contains(Box(2, 2, 5, 5))
    assert Box(0, 0, 10, 10).contains(Box(0, 0, 10, 10))
    assert not Box(0, 0, 10, 10).contains(Box(5, 5, 10, 10))
