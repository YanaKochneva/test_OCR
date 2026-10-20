"""Conservative quarter-turn correction, confirmed by image-only OCR."""

from __future__ import annotations

from dataclasses import replace
from typing import Callable

import cv2
import numpy as np
from PIL import Image

from docpipe.render.raster import RasterPage


def sideways_text_candidate(image: Image.Image) -> bool:
    """Gate orientation trials using text-band projections, excluding rules."""
    preview = image.convert("L").copy()
    preview.thumbnail((1200, 1200))
    ink = (np.asarray(preview) < 170).astype("uint8")
    _, _, stats, _ = cv2.connectedComponentsWithStats(ink, 8)
    glyphs = 0
    for _, _, width, height, area in stats[1:]:
        if 5 <= width <= 60 and 5 <= height <= 60 and area >= 20:
            ratio = width / height
            if 0.2 <= ratio <= 5:
                glyphs += 1
    if glyphs < 80:
        return False
    height, width = ink.shape
    horizontal = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((1, max(50, width // 8)), np.uint8))
    vertical = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((max(50, height // 8), 1), np.uint8))
    text_ink = ink & ~(horizontal | vertical)
    rows, cols = text_ink.sum(axis=1), text_ink.sum(axis=0)
    row_signal = rows.std() / max(1, rows.mean())
    col_signal = cols.std() / max(1, cols.mean())
    # This is a candidate gate, not permission to rotate. The subsequent OCR
    # comparison must establish a large gain and a unique reading direction.
    return bool(row_signal > 0 and col_signal > row_signal * 1.25)


def correct_orientation(raster: RasterPage, get_reader: Callable) -> RasterPage:
    """Rotate only with a clear gain over original and opposite direction.

    Scores measure confidently recognized characters; they are not CER.
    Ordinary upright pages never initialize the extra preview reader.
    """
    if not sideways_text_candidate(raster.image):
        return raster
    preview = raster.image.copy()
    preview.thumbnail((1600, 1600))
    reader = get_reader()

    def score(image):
        result = reader(image, use_det=True, use_cls=False)
        if result.txts is None or result.scores is None:
            return 0.0
        return sum(
            len(text.strip()) * confidence
            for text, confidence in zip(result.txts, result.scores)
            if confidence >= 0.9
        )

    base = score(preview)
    trials = [(score(preview.rotate(angle, expand=True)), angle) for angle in (90, 270)]
    best, angle = max(trials)
    other = min(value for value, _ in trials)
    if best < max(40, base * 1.7) or best < other * 1.35:
        return raster
    # PIL positive angles turn counterclockwise; PDF /Rotate is clockwise.
    correction = (360 - angle) % 360
    return replace(
        raster,
        image=raster.image.rotate(angle, expand=True),
        width_pt=raster.height_pt,
        height_pt=raster.width_pt,
        rotation=(raster.rotation + correction) % 360,
    )
