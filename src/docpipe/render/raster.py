from __future__ import annotations

import math
from dataclasses import dataclass

import pypdfium2 as pdfium
from PIL import Image

from docpipe.config import LimitsConfig


@dataclass(frozen=True)
class RasterPage:
    image: Image.Image
    width_pt: float
    height_pt: float
    rotation: int
    dpi: int


def bounded_dpi(width_pt: float, height_pt: float, dpi: int, limits: LimitsConfig) -> int:
    """Apply the pixel budget before PDFium allocates a page bitmap."""
    if not all(math.isfinite(v) and v > 0 for v in (width_pt, height_pt, dpi)):
        raise ValueError("Invalid page dimensions or DPI")
    allowed = min(
        float(dpi),
        72 * math.sqrt(limits.max_pixels_per_page / (width_pt * height_pt)),
        72 * limits.max_dimension_px / max(width_pt, height_pt),
    )
    selected = math.floor(allowed)
    while selected >= 1:
        w, h = math.ceil(width_pt * selected / 72), math.ceil(height_pt * selected / 72)
        if w * h <= limits.max_pixels_per_page and max(w, h) <= limits.max_dimension_px:
            return selected
        selected -= 1
    raise ValueError("Page cannot fit configured raster limits even at 1 DPI")


def rasterize_page(
    page: pdfium.PdfPage, dpi: int, limits: LimitsConfig | None = None
) -> RasterPage:
    width_pt, height_pt = page.get_size()
    rotation = page.get_rotation() or 0
    dpi = bounded_dpi(width_pt, height_pt, dpi, limits or LimitsConfig())
    scale = dpi / 72.0
    bitmap = page.render(scale=scale, rotation=0, fill_to_stroke=True)
    try:
        image = bitmap.to_pil()
        try:
            # Detach from PDFium-owned memory exactly once, then close the bitmap.
            rgb = image.copy() if image.mode == "RGB" else image.convert("RGB")
        finally:
            image.close()
    finally:
        bitmap.close()
    return RasterPage(image=rgb, width_pt=width_pt, height_pt=height_pt, rotation=rotation, dpi=dpi)
