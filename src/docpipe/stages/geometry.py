from __future__ import annotations

from docpipe.ir import BBox


def bottom_left_to_top_left(box: BBox, page_height: float) -> BBox:
    return BBox(x0=box.x0, y0=page_height-box.y1, x1=box.x1, y1=page_height-box.y0)


def scale_bbox(box: BBox, sx: float, sy: float) -> BBox:
    return BBox(x0=box.x0*sx, y0=box.y0*sy, x1=box.x1*sx, y1=box.y1*sy)


def rotate_bbox(box: BBox, width: float, height: float, rotation: int) -> BBox:
    r = rotation % 360
    if r == 0: return box
    if r == 90: return BBox(x0=height-box.y1, y0=box.x0, x1=height-box.y0, y1=box.x1)
    if r == 180: return BBox(x0=width-box.x1, y0=height-box.y1, x1=width-box.x0, y1=height-box.y0)
    if r == 270: return BBox(x0=box.y0, y0=width-box.x1, x1=box.y1, y1=width-box.x0)
    raise ValueError("Поддерживаются только повороты 0/90/180/270")
