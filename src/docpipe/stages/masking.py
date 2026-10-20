from __future__ import annotations

from PIL import Image, ImageDraw

from docpipe.engines.base import PageMeta
from docpipe.ir import Block, BlockType
from docpipe.stages.figures import PixelBox


def mask_figures(image: Image.Image, boxes: list[PixelBox]) -> Image.Image:
    out = image.copy()
    draw = ImageDraw.Draw(out)
    for b in boxes:
        draw.rectangle((b.x0, b.y0, b.x1, b.y1), fill=(255, 255, 255))
    return out


def mask_layout_figures(image: Image.Image, blocks: list[Block], meta: PageMeta) -> Image.Image:
    """Exclude illustrations from the additional text pass, preserving assets."""
    sx, sy = image.width / meta.width_pt, image.height / meta.height_pt
    boxes = []
    for block in blocks:
        if block.type != BlockType.FIGURE:
            continue
        box = block.bbox
        x0, y0 = max(0, int(box.x0 * sx)), max(0, int(box.y0 * sy))
        x1, y1 = min(image.width, int(box.x1 * sx)), min(image.height, int(box.y1 * sy))
        if x1 > x0 and y1 > y0:
            boxes.append(PixelBox(x0, y0, x1, y1))
    return mask_figures(image, boxes) if boxes else image
