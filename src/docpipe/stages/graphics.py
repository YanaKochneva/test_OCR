from __future__ import annotations

import cv2
import numpy as np
from PIL import Image, ImageDraw
from dataclasses import dataclass
from docpipe.engines import PageMeta
from docpipe.ir import BBox, Block, BlockType, FigureData
from pathlib import Path


@dataclass(frozen=True)
class PixelBox:
    x0: int
    y0: int
    x1: int
    y1: int


def crop_and_save(
    image: Image.Image, boxes: list[PixelBox], out_dir: Path, page_no: int
) -> list[tuple[PixelBox, Path]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    result = []
    for idx, b in enumerate(boxes, 1):
        crop = image.crop((b.x0, b.y0, b.x1, b.y1))
        try:
            # Bounded palette counting avoids sorting every pixel of a large figure.
            if crop.getcolors(maxcolors=256) is not None:
                path = out_dir / f"p{page_no:03d}_fig{idx:02d}.png"
                crop.save(path, format="PNG", optimize=True)
            else:
                path = out_dir / f"p{page_no:03d}_fig{idx:02d}.jpg"
                crop.save(path, format="JPEG", quality=90)
        finally:
            crop.close()
        result.append((b, path))
    return result


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


def retain_graphic_fields(blocks, image, meta):
    sx, sy = image.width / meta.width_pt, image.height / meta.height_pt
    # Some scans encode pixel dimensions as PDF points; use relative page scale.
    minimum = max(25, round(image.width * 0.018))
    output = list(blocks)
    seen = set()
    regions = [(cell.bbox, True) for block in blocks if block.table for cell in block.table.cells]
    regions.append((BBox(x0=0, y0=0, x1=meta.width_pt, y1=meta.height_pt), False))
    for box, is_cell in regions:
        left, top = max(0, round(box.x0 * sx) + 4), max(0, round(box.y0 * sy) + 4)
        right, bottom = (
            min(image.width, round(box.x1 * sx) - 4),
            min(image.height, round(box.y1 * sy) - 4),
        )
        if right <= left or bottom <= top:
            continue
        with image.crop((left, top, right, bottom)) as crop:
            ink = (np.asarray(crop.convert("L")) < 150).astype("uint8")
        if not is_cell:
            # Table grids and already segmented pictures have their own export.
            for block in blocks:
                if block.table or block.figure:
                    b = block.bbox
                    x0, y0 = max(0, round(b.x0 * sx) - left - 3), max(0, round(b.y0 * sy) - top - 3)
                    x1, y1 = (
                        min(ink.shape[1], round(b.x1 * sx) - left + 3),
                        min(ink.shape[0], round(b.y1 * sy) - top + 3),
                    )
                    ink[y0:y1, x0:x1] = 0
        rules = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((1, minimum), np.uint8))
        contours, _ = cv2.findContours(rules, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            if is_cell and (y < 3 or y + h > ink.shape[0] - 3):
                continue  # Broken grid fragments near the cell's edges.
            if w < minimum or h > max(3, round(image.height * 0.0015)) or w / h < 15:
                continue
            # Grid borders cross most of a cell and reach its edges.
            if w >= 0.9 * (right - left) or x <= 1 or x + w >= right - left - 1:
                continue
            key = (left + int(x), top + int(y), int(w), int(h))
            if key in seen:
                continue
            seen.add(key)
            px, py, w, h = key
            output.append(
                Block(
                    id=f"p{meta.index}-graphic-field-{len(seen)}",
                    type=BlockType.FIGURE,
                    order=len(output),
                    source_label="graphic_field",
                    bbox=BBox(
                        x0=(px - 1) / sx,
                        y0=(py - 1) / sy,
                        x1=(px + w + 1) / sx,
                        y1=(py + h + 1) / sy,
                    ),
                    figure=FigureData(file="", format="png", width_px=w + 2, height_px=h + 2),
                )
            )
    fields = [b.bbox for b in output if b.source_label == "graphic_field"]

    def reserve_gaps(lines):
        from docpipe.stages.tables import split_at_visual_lines

        result = []
        for line in lines or []:
            b = line.bbox
            gaps = sorted(
                [
                    (g.x0, g.x1)
                    for g in fields
                    if b.x0 < g.x0 < g.x1 < b.x1 and b.y0 <= g.y0 <= b.y1 + 3 / sy
                ]
            )
            if not gaps:
                result.append(line)
                continue
            spans = []
            start = b.x0
            for left, right in gaps:
                if left > start:
                    spans.append((start, left))
                start = max(start, right)
            if start < b.x1:
                spans.append((start, b.x1))
            chunks = split_at_visual_lines(line.text, [right - left for left, right in spans])
            if chunks:
                runs = [
                    line.model_copy(
                        update={
                            "text": text,
                            "runs": None,
                            "bbox": BBox(x0=left, y0=b.y0, x1=right, y1=b.y1),
                        }
                    )
                    for text, (left, right) in zip(chunks, spans)
                ]
                line = line.model_copy(update={"runs": runs})
            result.append(line)
        return result or None

    positioned = []
    for block in output:
        update = {"lines": reserve_gaps(block.lines)}
        if block.table:
            update["table"] = block.table.model_copy(
                update={
                    "cells": [
                        cell.model_copy(update={"lines": reserve_gaps(cell.lines)})
                        for cell in block.table.cells
                    ]
                }
            )
        positioned.append(block.model_copy(update=update))
    return positioned
