from __future__ import annotations

import re
from docpipe.ir import BBox
from docpipe.renderers.fonts import FONT_FAMILY, TABLE_FONT_PT, text_style
from rapidfuzz.distance import Levenshtein
from statistics import median


A4_WIDTH = 210 * 72 / 25.4
A4_HEIGHT = 297 * 72 / 25.4


def a4_document(document):
    pages = []
    for page in document.pages:
        width, height = (
            (A4_HEIGHT, A4_WIDTH) if page.width_pt > page.height_pt else (A4_WIDTH, A4_HEIGHT)
        )
        scale = min(width / page.width_pt, height / page.height_pt)
        dx, dy = (width - page.width_pt * scale) / 2, (height - page.height_pt * scale) / 2

        def box(b):
            return BBox(
                x0=b.x0 * scale + dx,
                y0=b.y0 * scale + dy,
                x1=b.x1 * scale + dx,
                y1=b.y1 * scale + dy,
            )

        def lines(items):
            return (
                [
                    line.model_copy(update={"bbox": box(line.bbox), "runs": lines(line.runs)})
                    for line in items
                ]
                if items
                else None
            )

        blocks = []
        for block in page.blocks:
            update = {"bbox": box(block.bbox), "lines": lines(block.lines)}
            if block.table:
                update["table"] = block.table.model_copy(
                    update={
                        "cells": [
                            cell.model_copy(
                                update={"bbox": box(cell.bbox), "lines": lines(cell.lines)}
                            )
                            for cell in block.table.cells
                        ]
                    }
                )
            blocks.append(block.model_copy(update=update))
        pages.append(
            page.model_copy(update={"width_pt": width, "height_pt": height, "blocks": blocks})
        )
    return document.model_copy(update={"pages": pages})


def stable_document(document):
    """Return a render-only copy: never mutate recognized text or geometry."""

    def normalize(items, role):
        size, bold, italic = text_style(role)
        if role == "table":
            size, bold, italic = TABLE_FONT_PT, False, False
        if role == "table_header":
            size, bold, italic = TABLE_FONT_PT, True, False
        if role == "header":
            size, bold, italic = 9.5, True, False
        if role in ("footer", "page_number"):
            size, bold, italic = 8, False, False
        result = []
        for line in items or []:
            from PIL import ImageFont

            from docpipe.renderers.fonts import font_path, require_font

            font = ImageFont.truetype(
                str(font_path(FONT_FAMILY, bold, italic) or require_font()), 100
            )
            box = line.bbox
            available_width = box.y1 - box.y0 if line.rotation in (90, 270) else box.x1 - box.x0
            available_height = box.x1 - box.x0 if line.rotation in (90, 270) else box.y1 - box.y0
            measured = font.getlength(line.text)
            height_factor = 1.05 if role in ("table", "table_header") else 1.35
            fitted = min(
                size,
                max(5, available_height * height_factor),
                available_width * 100 / measured if measured else size,
            )
            # Optical font ambiguity is irrelevant to an explicitly fixed style.
            optical = line.font_match_error is not None
            result.append(
                line.model_copy(
                    update={
                        "font_family": FONT_FAMILY,
                        "font_size_pt": max(4, fitted),
                        "bold": bold,
                        "italic": italic,
                        "runs": normalize(line.runs, role),
                        "horizontal_scale_pct": 100,
                        "glyph_offset_x_pt": 0,
                        "baseline_from_top_pt": None,
                        "font_match_error": None,
                        "font_match_margin": None,
                        "review_required": False if optical else line.review_required,
                        "review_reason": None if optical else line.review_reason,
                    }
                )
            )
        return result or None

    pages = []
    for page in document.pages:
        contents = any(
            (b.text or "").strip().casefold() in ("содержание", "оглавление") for b in page.blocks
        )
        blocks = []
        for block in page.blocks:
            update = {"lines": normalize(block.lines, block.type.value)}
            rows = (block.text or "").splitlines()
            if update["lines"] and len(rows) > 1 and len(rows) == len(update["lines"]):
                update["lines"] = [
                    line.model_copy(update={"text": text})
                    for line, text in zip(update["lines"], rows)
                ]
                if contents:
                    from PIL import ImageFont

                    from docpipe.renderers.fonts import font_path, require_font

                    font = ImageFont.truetype(
                        str(font_path(FONT_FAMILY, False, False) or require_font()), 100
                    )
                    repaired = []
                    for line in update["lines"]:
                        match = re.match(r"^(\d+(?:\.\d+)*\.\s+.+?)\s+(\d{1,4})$", line.text)
                        if match:
                            title, number = match.groups()
                            size = line.font_size_pt
                            gap = (
                                line.bbox.x1
                                - line.bbox.x0
                                - font.getlength(title + " " + number) * size / 100
                            )
                            dots = max(0, int(gap / (font.getlength(".") * size / 100)))
                            if dots:
                                line = line.model_copy(
                                    update={"text": title + " " + "." * dots + number}
                                )
                        repaired.append(line)
                    update["lines"] = repaired
            if block.table:
                update["table"] = block.table.model_copy(
                    update={
                        "cells": [
                            cell.model_copy(
                                update={
                                    "lines": normalize(
                                        cell.lines,
                                        "table_header"
                                        if cell.row == 0
                                        and cell.background_color
                                        and cell.background_color.upper() != "#FFFFFF"
                                        else "table",
                                    )
                                }
                            )
                            for cell in block.table.cells
                        ]
                    }
                )
            blocks.append(block.model_copy(update=update))
        pages.append(page.model_copy(update={"blocks": blocks}))

    return prepare_layout(document.model_copy(update={"pages": pages}))


def _key(text):
    return "".join(re.findall(r"\w+", text.casefold()))


def _intersection(a, b):
    return max(0, min(a.x1, b.x1) - max(a.x0, b.x0)) * max(0, min(a.y1, b.y1) - max(a.y0, b.y0))


def _area(box):
    return max(0.01, (box.x1 - box.x0) * (box.y1 - box.y0))


def _deduplicate(blocks):
    """Remove a composite OCR echo only when separate overlapping parts corroborate it."""
    output = []
    for block in blocks:
        key = _key(block.text or "")
        if not key or block.table or block.type.value == "figure":
            output.append(block)
            continue
        parts = [
            b
            for b in blocks
            if b is not block
            and b.text
            and not b.table
            and b.type.value != "figure"
            and 0 < len(_key(b.text)) <= len(key)
            and _intersection(block.bbox, b.bbox) / min(_area(block.bbox), _area(b.bbox)) >= 0.55
        ]
        parts.sort(key=lambda b: (b.bbox.y0, b.bbox.x0, b.order))
        if parts and max(b.bbox.y0 for b in parts) - min(b.bbox.y0 for b in parts) <= 1.5 * max(
            b.bbox.y1 - b.bbox.y0 for b in parts
        ):
            parts.sort(key=lambda b: (b.bbox.x0, b.bbox.y0, b.order))
        combined = "".join(_key(b.text) for b in parts)
        if (
            len(parts) >= 2
            and 0.9 <= len(combined) / len(key) <= 1.1
            and Levenshtein.normalized_similarity(key, combined) >= 0.94
        ):
            continue
        output.append(block)
    return output


def without_overlapping_ocr_echoes(document):
    """Expected export text: collapse only the same spatial echoes as rendering."""
    return document.model_copy(
        update={
            "pages": [
                page.model_copy(update={"blocks": _deduplicate(page.blocks)})
                for page in document.pages
            ]
        }
    )


def _move_box(box, dy):
    return box.model_copy(update={"y0": box.y0 + dy, "y1": box.y1 + dy})


def _move_lines(lines, dy):
    return (
        [
            line.model_copy(
                update={"bbox": _move_box(line.bbox, dy), "runs": _move_lines(line.runs, dy)}
            )
            for line in lines
        ]
        if lines
        else None
    )


def _uniform_cell(cell):
    if not cell.lines:
        return cell
    from PIL import ImageFont

    from docpipe.renderers.fonts import font_path, require_font
    from docpipe.renderers.fonts import FONT_FAMILY, TABLE_FONT_PT

    lines = sorted(cell.lines, key=lambda line: (line.bbox.y0, line.bbox.x0))
    limit = min(TABLE_FONT_PT, median(line.font_size_pt or TABLE_FONT_PT for line in lines))
    for previous, following in zip(lines, lines[1:]):
        pitch = following.bbox.y0 - previous.bbox.y0
        if pitch > 1:
            limit = min(limit, pitch / 1.12)
    available = max(1, cell.bbox.y1 - lines[-1].bbox.y0 - 1.5)
    size = max(1, min(limit, available / 1.12))
    size = int(size * 2) / 2 or 0.5  # match DOCX half-point serialization

    def normalize(line):
        family = line.font_family or FONT_FAMILY
        font = ImageFont.truetype(
            str(font_path(family, line.bold, line.italic) or require_font()), 100
        )
        measured = font.getlength(line.text) * size / 100
        width = (
            line.bbox.y1 - line.bbox.y0
            if line.rotation in (90, 270)
            else line.bbox.x1 - line.bbox.x0
        )
        scale = min(100, max(1, width / measured * 100)) if measured else 100
        return line.model_copy(
            update={
                "font_size_pt": size,
                "horizontal_scale_pct": scale,
                "runs": [normalize(run) for run in line.runs] if line.runs else None,
            }
        )

    return cell.model_copy(update={"lines": [normalize(line) for line in lines]})


def prepare_layout(document):
    """Use identical cell sizes and collision offsets for positional renderers."""
    from docpipe.renderers.text_layout import estimate_text_height
    from docpipe.renderers.fonts import text_style

    pages = []
    for page in document.pages:
        blocks = _deduplicate(page.blocks)
        placed = []
        results = {}
        for block in sorted(blocks, key=lambda b: (b.bbox.y0, b.bbox.x0, b.order)):
            original_identity = id(block)
            if block.table:
                block = block.model_copy(
                    update={
                        "table": block.table.model_copy(
                            update={"cells": [_uniform_cell(c) for c in block.table.cells]}
                        )
                    }
                )
            box = block.bbox
            # Thin rules are intentional form fields, not occupied text rectangles.
            obstacle = block.type.value != "figure" or box.y1 - box.y0 > 3
            height = box.y1 - box.y0
            if block.lines:
                height = max(
                    height,
                    max(line.bbox.y0 + 1.2 * (line.font_size_pt or 10) for line in block.lines)
                    - box.y0,
                )
            elif block.text and not block.table:
                height = max(
                    height,
                    estimate_text_height(block.text, box.x1 - box.x0, text_style(block.type.value)),
                )
            top = box.y0
            if obstacle:
                for left, right, first, last in placed:
                    if (
                        min(box.x1, right) - max(box.x0, left) > 2
                        and top < last + 1
                        and top + height > first
                    ):
                        top = last + 1
            dy = top - box.y0
            if abs(dy) > 0.001:
                updates = {"bbox": _move_box(box, dy), "lines": _move_lines(block.lines, dy)}
                if block.table:
                    updates["table"] = block.table.model_copy(
                        update={
                            "cells": [
                                c.model_copy(
                                    update={
                                        "bbox": _move_box(c.bbox, dy),
                                        "lines": _move_lines(c.lines, dy),
                                    }
                                )
                                for c in block.table.cells
                            ]
                        }
                    )
                block = block.model_copy(update=updates)
            if obstacle:
                placed.append((box.x0, box.x1, top, top + height))
            results[original_identity] = block
        pages.append(page.model_copy(update={"blocks": [results[id(b)] for b in blocks]}))
    return document.model_copy(update={"pages": pages})
