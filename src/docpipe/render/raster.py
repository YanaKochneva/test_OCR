from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

from docpipe.config import AppConfig

@dataclass(frozen=True)
class RasterPage:
    image: Image.Image
    width_pt: float
    height_pt: float
    rotation: int
    dpi: int


def rasterize_page(page: pdfium.PdfPage, dpi: int) -> RasterPage:
    width_pt, height_pt = page.get_size()
    rotation = page.get_rotation() or 0
    scale = dpi / 72.0
    bitmap = page.render(scale=scale, rotation=0, fill_to_stroke=True)
    image = bitmap.to_pil()
    return RasterPage(image=image.convert("RGB"), width_pt=width_pt, height_pt=height_pt, rotation=rotation, dpi=dpi)
