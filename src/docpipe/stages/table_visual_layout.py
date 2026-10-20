"""Restore cell line positions and fills from pixels, without recognizing text."""

from pathlib import Path
from statistics import median

import cv2
import numpy as np
from PIL import ImageFont
from rapidfuzz.distance import Levenshtein

from docpipe.ir import BBox, Line
from docpipe.renderers.fonts import require_font
from docpipe.stages.table_geometry import adaptive_reading_boxes, measured_cell_boxes


def split_at_visual_lines(text, widths):
    """Partition existing words into measured lines; never add or delete words."""
    words = text.split()
    count = len(widths)
    if not count or len(words) < count:
        return None
    arial = Path("C:/Windows/Fonts/arial.ttf")
    font = ImageFont.truetype(str(arial if arial.is_file() else require_font()), 64)
    space = font.getlength(" ")
    prefix = [0.0]
    for word in words:
        prefix.append(prefix[-1] + font.getlength(word) + space)
    ratio = (prefix[-1] - count * space) / max(1, sum(widths))
    targets = [max(1, width * ratio) for width in widths]
    previous = {0: 0.0}
    parents = []
    for row, target in enumerate(targets):
        current = {}
        links = {}
        remaining = count - row - 1
        for end in range(row + 1, len(words) - remaining + 1):
            best = (float("inf"), None)
            for start, cost in previous.items():
                if start >= end:
                    continue
                width = prefix[end] - prefix[start] - space
                proposal = cost + ((width - target) / target) ** 2
                if proposal < best[0]:
                    best = (proposal, start)
            if best[1] is not None:
                current[end] = best[0]
                links[end] = best[1]
        previous = current
        parents.append(links)
    end = len(words)
    chunks = []
    for links in reversed(parents):
        start = links[end]
        chunks.append(" ".join(words[start:end]))
        end = start
    return list(reversed(chunks))


def split_using_visual_ocr(text, readings):
    """Use OCR only to locate breaks; retain every existing recognized word."""
    old = text.split()
    candidate = []
    owners = []
    for index, reading in enumerate(readings):
        words = reading.split()
        candidate.extend(words)
        owners.extend([index] * len(words))

    def normalize(values):
        return [word.casefold() for word in values]

    if (
        not candidate
        or Levenshtein.normalized_similarity(normalize(old), normalize(candidate)) < 0.65
    ):
        return None
    assigned = [[] for _ in readings]
    for op in Levenshtein.opcodes(normalize(candidate), normalize(old)):
        for position in range(op.dest_start, op.dest_end):
            if op.src_end > op.src_start:
                source = op.src_start + min(
                    op.src_end - op.src_start - 1,
                    (position - op.dest_start)
                    * (op.src_end - op.src_start)
                    // max(1, op.dest_end - op.dest_start),
                )
            else:
                source = min(op.src_start, len(owners) - 1)
            assigned[owners[source]].append(old[position])
    return [" ".join(words) for words in assigned]


def restore_table_visual_layout(blocks, image, meta, line_reader=None):
    """Use proven cell borders and ink bands; keep recognition unchanged."""
    output = []
    sx, sy = image.width / meta.width_pt, image.height / meta.height_pt
    for block in blocks:
        if not block.table:
            output.append(block)
            continue
        boxes = measured_cell_boxes(block, image, meta) or adaptive_reading_boxes(
            block, image, meta
        )
        cells = []
        for cell in block.table.cells:
            box = boxes.get((cell.row, cell.col))
            if box is None:
                cells.append(cell)
                continue
            left, top = max(0, round(box.x0 * sx) + 3), max(0, round(box.y0 * sy) + 3)
            right, bottom = (
                min(image.width, round(box.x1 * sx) - 3),
                min(image.height, round(box.y1 * sy) - 3),
            )
            if right <= left or bottom <= top:
                cells.append(cell)
                continue
            with image.crop((left, top, right, bottom)) as crop:
                rgb = np.asarray(crop.convert("RGB"))
                gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
            ink = (gray < 170).astype("uint8")
            h, w = ink.shape
            rules = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((1, max(30, w // 3)), np.uint8))
            rules |= cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((max(30, h // 3), 1), np.uint8))
            ink &= ~rules
            active = np.flatnonzero(ink.sum(axis=1) > max(2, w * 0.002))
            bands = []
            for y in active:
                if bands and y - bands[-1][1] <= 3:
                    bands[-1][1] = int(y)
                else:
                    bands.append([int(y), int(y)])
            joined = []
            for start, end in bands:
                if joined and start - joined[-1][1] <= max(
                    3, round(max(end - start + 1, joined[-1][1] - joined[-1][0] + 1) * 0.18)
                ):
                    joined[-1][1] = end
                else:
                    joined.append([start, end])
            bands = joined
            line_boxes = []
            line_weights = []
            for start, end in bands:
                if not 5 <= end - start + 1 <= max(100, h * 0.1):
                    continue
                columns = np.flatnonzero(ink[start : end + 1].sum(axis=0) >= 2)
                if len(columns) < 8:
                    continue
                line_boxes.append(
                    BBox(
                        x0=(left + int(columns[0])) / sx,
                        x1=(left + int(columns[-1]) + 1) / sx,
                        y0=(top + start) / sy,
                        y1=(top + end + 1) / sy,
                    )
                )
                line_weights.append(int(ink[start : end + 1].sum()))
            if 0 < len(cell.text.split()) < len(line_boxes):
                selected = sorted(
                    sorted(range(len(line_boxes)), key=lambda i: line_weights[i], reverse=True)[
                        : len(cell.text.split())
                    ]
                )
                line_boxes = [line_boxes[i] for i in selected]
            chunks = split_at_visual_lines(cell.text, [b.x1 - b.x0 for b in line_boxes])
            if line_reader is not None and line_boxes and cell.text.strip():
                readings = []
                for b in line_boxes:
                    with image.crop(
                        (
                            max(0, int(b.x0 * sx) - 6),
                            max(0, int(b.y0 * sy) - 6),
                            min(image.width, int(b.x1 * sx) + 6),
                            min(image.height, int(b.y1 * sy) + 6),
                        )
                    ) as crop:
                        result = line_reader(crop, use_det=False, use_cls=False)
                    readings.append(result.txts[0] if result.txts else "")
                anchored = split_using_visual_ocr(cell.text, readings)
                if anchored is not None:
                    chunks = anchored
            updates = {"bbox": box}
            if chunks:
                size = min(14, max(4, median(b.y1 - b.y0 for b in line_boxes) / 0.85))
                updates["lines"] = [
                    Line(text=text, bbox=b, font_size_pt=size)
                    for text, b in zip(chunks, line_boxes)
                    if text
                ]
            # A colored fill occupies the cell, unlike sparse colored glyphs.
            chromatic = (rgb.max(axis=2).astype("int16") - rgb.min(axis=2)) > 50
            if np.mean(chromatic) > 0.3:
                color = np.median(rgb[chromatic], axis=0).astype("uint8")
                updates["background_color"] = "#" + "".join(f"{int(v):02x}" for v in color)
            cells.append(cell.model_copy(update=updates))
        style = _rule_style(block, image, meta)
        updates = {"table": block.table.model_copy(update={"cells": cells, **style})}
        if boxes:
            updates["bbox"] = BBox(
                x0=min(b.x0 for b in boxes.values()),
                y0=min(b.y0 for b in boxes.values()),
                x1=max(b.x1 for b in boxes.values()),
                y1=max(b.y1 for b in boxes.values()),
            )
        output.append(block.model_copy(update=updates))
    return output


def _rule_style(block, image, meta):
    sx, sy = image.width / meta.width_pt, image.height / meta.height_pt
    b = block.bbox
    with image.crop(
        (
            max(0, int(b.x0 * sx) - 3),
            max(0, int(b.y0 * sy) - 3),
            min(image.width, int(b.x1 * sx) + 4),
            min(image.height, int(b.y1 * sy) + 4),
        )
    ) as crop:
        rgb = np.asarray(crop.convert("RGB"))
    neutral = rgb.max(axis=2).astype("int16") - rgb.min(axis=2) < 25
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    mask = ((gray < 210) & neutral).astype("uint8")
    h, w = mask.shape
    rules = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((1, max(30, w // 3)), np.uint8))
    rules |= cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((max(30, h // 3), 1), np.uint8))
    if rules.sum() < max(w, h):
        return {}
    color = np.median(rgb[rules > 0], axis=0).astype("uint8")
    thickness = cv2.distanceTransform(rules, cv2.DIST_L2, 3)[rules > 0]
    width = max(0.1, min(3, float(np.median(thickness)) * 2 / min(sx, sy)))
    return {
        "border_color": "#" + "".join(f"{int(v):02x}" for v in color),
        "border_width_pt": round(width, 3),
    }
