"""Images in and out: normalize what leaves the machine, burn the boxes locally.

Every capture is re-encoded as RGB PNG before it is sent, which also drops
metadata: a screenshot can carry a user or machine name outside its pixels.

The final boxes are drawn on the original frame, never on the image ShinrAI
returned: once the user reveals a finding the two disagree, and a box drawn on
the server's copy could hide nothing.
"""

import io
from collections.abc import Iterable

from PIL import Image, ImageDraw

from eecc_redact import APP_NAME
from eecc_redact.errors import AppError
from eecc_redact.models import Box


def to_png(data: bytes) -> bytes:
    try:
        with Image.open(io.BytesIO(data)) as source:
            image = source.convert("RGB")
    except OSError as exc:
        raise AppError(f"{APP_NAME} cannot read that file as an image.") from exc
    return _encode(image)


def burn(png: bytes, boxes: Iterable[Box], *, padding: int = 0) -> bytes:
    with Image.open(io.BytesIO(png)) as source:
        image = source.convert("RGB")
    draw = ImageDraw.Draw(image)
    for box in boxes:
        b = box.padded(padding)
        # Pillow's rectangle includes both corners: x+w would paint w+1 pixels.
        draw.rectangle((b.x, b.y, b.x + b.w - 1, b.y + b.h - 1), fill="black")
    return _encode(image)


def _encode(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
