"""Képfeldolgozás: borítók JPEG-re alakítása és bélyegképek."""
from __future__ import annotations

import io

from PIL import Image


def to_jpeg(data: bytes, max_height: int = 1920, quality: int = 90) -> bytes:
    """Bármilyen kép (WebP, PNG…) → RGB JPEG. A konvertáló a WebP borítót csendben eldobja, ezért fontos."""
    img = Image.open(io.BytesIO(data))
    img = img.convert("RGB")
    if img.height > max_height:
        img = img.resize((max(1, int(img.width * max_height / img.height)), max_height), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=quality, optimize=True)
    return out.getvalue()


def thumbnail(data: bytes, width: int, grayscale: bool = False) -> bytes:
    img = Image.open(io.BytesIO(data))
    img = img.convert("L" if grayscale else "RGB")
    if img.width > width:
        img = img.resize((width, max(1, int(img.height * width / img.width))), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=82, optimize=True)
    return out.getvalue()


def image_size(data: bytes) -> tuple[int, int] | None:
    try:
        return Image.open(io.BytesIO(data)).size
    except Exception:
        return None
