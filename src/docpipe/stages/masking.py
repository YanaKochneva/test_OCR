from __future__ import annotations

from PIL import Image, ImageDraw
from docpipe.stages.figures import PixelBox


def mask_figures(image: Image.Image, boxes: list[PixelBox]) -> Image.Image:
    out=image.copy()
    draw=ImageDraw.Draw(out)
    for b in boxes:
        draw.rectangle((b.x0,b.y0,b.x1,b.y1), fill=(255,255,255))
    return out
