from __future__ import annotations

import cv2
import numpy as np
import re
from PIL import Image, ImageFont
from docpipe.engines import PageMeta
from docpipe.ir import BBox, Block, Line, TableCell
from docpipe.renderers.fonts import require_font
from docpipe.stages.text import preserves_existing_words
from pathlib import Path
from rapidfuzz.distance import Levenshtein
from statistics import median


def rebuild_verified_grid(block: Block, image: Image.Image, meta: PageMeta) -> Block:
    """Correct a plain ruled grid only when every separator crosses the table.

    Preserve every populated source cell. Ambiguous mappings, partial separators,
    merged cells and borderless tables retain the original model prediction.
    """
    table = block.table
    if not table or table.n_cols < 2 or any(c.rowspan != 1 or c.colspan != 1 for c in table.cells):
        return block
    sx, sy = image.width / meta.width_pt, image.height / meta.height_pt
    b = block.bbox
    left, top = max(0, int(b.x0 * sx) - 8), max(0, int(b.y0 * sy) - 8)
    right, bottom = min(image.width, int(b.x1 * sx) + 8), min(image.height, int(b.y1 * sy) + 8)
    if right - left < 40 or bottom - top < 30:
        return block
    with image.crop((left, top, right, bottom)) as crop:
        with crop.convert("L") as gray_image:
            gray = np.asarray(gray_image)
            ink = (
                cv2.adaptiveThreshold(
                    gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 12
                )
                > 0
            ).astype("uint8")
    h, w = ink.shape
    horizontal = cv2.morphologyEx(
        cv2.dilate(ink, np.ones((7, 1), np.uint8)),
        cv2.MORPH_OPEN,
        np.ones((1, max(20, w // 3)), np.uint8),
    )
    vertical = cv2.morphologyEx(
        cv2.dilate(ink, np.ones((1, 5), np.uint8)),
        cv2.MORPH_OPEN,
        np.ones((max(20, h // 3), 1), np.uint8),
    )

    def centers(values, threshold):
        groups = []
        for value in np.flatnonzero(values > threshold):
            if groups and value - groups[-1][-1] <= 4:
                groups[-1].append(int(value))
            else:
                groups.append([int(value)])
        return [sum(group) / len(group) for group in groups]

    xs, ys = centers(vertical.sum(axis=0), h * 0.75), centers(horizontal.sum(axis=1), w * 0.75)
    if (
        len(xs) != table.n_cols + 1
        or len(ys) < 3
        or len(ys) - 1 < table.n_rows
        or (len(ys) - 1) * table.n_cols > 4096
    ):
        return block
    if xs[-1] - xs[0] < w * 0.8 or ys[-1] - ys[0] < h * 0.8:
        return block
    if min(np.diff(xs)) < 12 or min(np.diff(ys)) < 8:
        return block
    # A separator present over most of the table can still be absent in a
    # merged header. Verify every individual row/column, not just global sums.
    for x in xs:
        for y0, y1 in zip(ys, ys[1:]):
            strip = vertical[int(y0) + 3 : int(y1) - 2, max(0, int(x) - 3) : min(w, int(x) + 4)]
            if strip.size == 0 or np.mean(strip.max(axis=1) > 0) < 0.7:
                return block
    for y in ys:
        for x0, x1 in zip(xs, xs[1:]):
            strip = horizontal[max(0, int(y) - 4) : min(h, int(y) + 5), int(x0) + 3 : int(x1) - 2]
            if strip.size == 0 or np.mean(strip.max(axis=0) > 0) < 0.7:
                return block
    grouped = {}
    for cell in table.cells:
        if not cell.text.strip():
            continue
        cb = cell.bbox
        cx, cy = (cb.x0 + cb.x1) * sx / 2 - left, (cb.y0 + cb.y1) * sy / 2 - top
        col, row = int(np.searchsorted(xs, cx) - 1), int(np.searchsorted(ys, cy) - 1)
        if not (0 <= col < len(xs) - 1 and 0 <= row < len(ys) - 1):
            return block
        # Reject a source rectangle crossing several measured cells. Never split
        # a paragraph arbitrarily to force the model into the new grid.
        area = max(0.01, (cb.x1 - cb.x0) * (cb.y1 - cb.y0))
        target = BBox(
            x0=(left + xs[col]) / sx,
            x1=(left + xs[col + 1]) / sx,
            y0=(top + ys[row]) / sy,
            y1=(top + ys[row + 1]) / sy,
        )
        overlap = max(0, min(cb.x1, target.x1) - max(cb.x0, target.x0)) * max(
            0, min(cb.y1, target.y1) - max(cb.y0, target.y0)
        )
        if overlap / area < 0.8:
            return block
        if col != cell.col or (row, col) in grouped:
            # Weak rules must not collapse existing rows or concatenate two
            # independently populated source cells into one inferred slot.
            return block
        grouped[(row, col)] = [cell]
    cells = []
    for row in range(len(ys) - 1):
        for col in range(len(xs) - 1):
            parts = sorted(grouped.get((row, col), []), key=lambda c: (c.bbox.y0, c.bbox.x0))
            cells.append(
                TableCell(
                    row=row,
                    col=col,
                    text="\n".join(c.text for c in parts),
                    bbox=BBox(
                        x0=(left + xs[col]) / sx,
                        x1=(left + xs[col + 1]) / sx,
                        y0=(top + ys[row]) / sy,
                        y1=(top + ys[row + 1]) / sy,
                    ),
                )
            )
    return block.model_copy(
        update={
            "table": table.model_copy(
                update={"cells": cells, "n_rows": len(ys) - 1, "n_cols": len(xs) - 1, "html": None}
            )
        }
    )


def adaptive_reading_boxes(block, image, meta):
    """Expand OCR crops using proven row rules and column separators.

    This does not change renderer geometry. An absent outer rule uses the
    existing table envelope, never an inferred row or a neighbour's content.
    """
    if not block.table or block.table.n_cols < 2:
        return {}
    table = block.table
    sx, sy = image.width / meta.width_pt, image.height / meta.height_pt
    b = block.bbox
    left, top = max(0, int(b.x0 * sx) - 10), max(0, int(b.y0 * sy) - 10)
    right, bottom = min(image.width, int(b.x1 * sx) + 10), min(image.height, int(b.y1 * sy) + 10)
    if right <= left or bottom <= top:
        return {}
    gray = np.asarray(image.crop((left, top, right, bottom)).convert("L"))
    h, w = gray.shape
    ink = (
        cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 12
        )
        > 0
    ).astype("uint8")
    horizontal = cv2.morphologyEx(
        cv2.dilate(ink, np.ones((7, 1), np.uint8)),
        cv2.MORPH_OPEN,
        np.ones((1, max(20, w // 4)), np.uint8),
    )
    vertical = cv2.morphologyEx(
        cv2.dilate(ink, np.ones((1, 5), np.uint8)),
        cv2.MORPH_OPEN,
        np.ones((max(20, h // 8), 1), np.uint8),
    )

    def centers(values, threshold):
        groups = []
        for value in np.flatnonzero(values > threshold):
            if groups and value - groups[-1][-1] <= 4:
                groups[-1].append(int(value))
            else:
                groups.append([int(value)])
        return [sum(g) / len(g) for g in groups]

    ys = centers(horizontal.sum(axis=1), w * 0.5)
    xs = centers(vertical.sum(axis=0), h * 0.35)
    if len(ys) != table.n_rows + 1:
        return {}
    separators = []
    for col in range(table.n_cols - 1):
        before = [
            (c.bbox.x0 + c.bbox.x1) * sx / 2 - left
            for c in table.cells
            if c.col == col and c.colspan == 1
        ]
        after = [
            (c.bbox.x0 + c.bbox.x1) * sx / 2 - left
            for c in table.cells
            if c.col == col + 1 and c.colspan == 1
        ]
        if not before or not after:
            return {}
        choices = [x for x in xs if max(before) < x < min(after)]
        if len(choices) != 1:
            return {}
        separators.append(choices[0])
    bounds = [(b.x0 * sx - left), *separators, (b.x1 * sx - left)]
    boxes = {}
    for cell in table.cells:
        if cell.row + cell.rowspan >= len(ys) or cell.col + cell.colspan >= len(bounds):
            return {}
        cy = (cell.bbox.y0 + cell.bbox.y1) * sy / 2 - top
        if not ys[cell.row] - 4 <= cy <= ys[cell.row + cell.rowspan] + 4:
            return {}
        boxes[(cell.row, cell.col)] = BBox(
            x0=(left + bounds[cell.col]) / sx,
            x1=(left + bounds[cell.col + cell.colspan]) / sx,
            y0=(top + ys[cell.row]) / sy,
            y1=(top + ys[cell.row + cell.rowspan]) / sy,
        )
    return boxes


def measured_cell_boxes(
    block: Block, image: Image.Image, meta: PageMeta
) -> dict[tuple[int, int], BBox]:
    """Use a complete detected grid; retain OCR boxes when it is ambiguous.

    OCR cell boxes often enclose only recognized text. Reading those boxes
    again cannot recover words outside that already recognized area.
    """
    if block.table is None:
        return {}
    sx, sy = image.width / meta.width_pt, image.height / meta.height_pt
    box = block.bbox
    left, top = max(0, int(box.x0 * sx) - 10), max(0, int(box.y0 * sy) - 10)
    right = min(image.width, int(box.x1 * sx) + 10)
    bottom = min(image.height, int(box.y1 * sy) + 10)
    if right <= left or bottom <= top:
        return {}
    gray = np.asarray(image.crop((left, top, right, bottom)).convert("L"))
    mask = (gray < 180).astype("uint8")
    height, width = mask.shape
    horizontal = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((1, max(20, width // 4)), np.uint8))
    vertical = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((max(20, height // 8), 1), np.uint8))

    def centers(counts, threshold):
        groups: list[list[int]] = []
        for value in np.flatnonzero(counts > threshold):
            if groups and value - groups[-1][-1] <= 4:
                groups[-1].append(int(value))
            else:
                groups.append([int(value)])
        return [sum(group) / len(group) for group in groups]

    ys = centers(horizontal.sum(axis=1), width * 0.5)
    xs = centers(vertical.sum(axis=0), height * 0.35)
    if len(xs) != block.table.n_cols + 1 or len(ys) != block.table.n_rows + 1:
        return {}
    boxes: dict[tuple[int, int], BBox] = {}
    for cell in block.table.cells:
        if (
            cell.row < 0
            or cell.col < 0
            or cell.col + cell.colspan >= len(xs)
            or cell.row + cell.rowspan >= len(ys)
        ):
            return {}
        boxes[(cell.row, cell.col)] = BBox(
            x0=(left + xs[cell.col]) / sx,
            x1=(left + xs[cell.col + cell.colspan]) / sx,
            y0=(top + ys[cell.row]) / sy,
            y1=(top + ys[cell.row + cell.rowspan]) / sy,
        )
    return boxes


def materialize_missing_cells(block: Block, image: Image.Image, meta: PageMeta) -> Block:
    if block.table is None:
        return block
    table = block.table
    rows, cols = table.n_rows, table.n_cols
    if rows < 1 or cols < 1 or rows * cols > 4096:
        return block
    occupied = {
        (row, col)
        for cell in table.cells
        for row in range(cell.row, min(rows, cell.row + cell.rowspan))
        for col in range(cell.col, min(cols, cell.col + cell.colspan))
    }
    holes = [(row, col) for row in range(rows) for col in range(cols) if (row, col) not in occupied]
    if not holes:
        return block
    # Dummy boxes are never used for OCR: only the independently measured
    # complete grid can authorize a new cell's rectangle.
    candidates = [TableCell(row=row, col=col, text="", bbox=block.bbox) for row, col in holes]
    probe = block.model_copy(
        update={"table": table.model_copy(update={"cells": [*table.cells, *candidates]})}
    )
    measured = measured_cell_boxes(probe, image, meta)
    if not measured:
        return block
    added = [
        cell.model_copy(update={"bbox": measured[(cell.row, cell.col)]}) for cell in candidates
    ]
    return block.model_copy(
        update={"table": table.model_copy(update={"cells": [*table.cells, *added]})}
    )


def clean_proven_cell_borders(image):
    gray = np.asarray(image.convert("L"))
    h, w = gray.shape
    if min(h, w) < 16:
        return image
    ink = (
        cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 12
        )
        > 0
    ).astype("uint8")
    horizontal = cv2.morphologyEx(
        cv2.dilate(ink, np.ones((7, 1), np.uint8)),
        cv2.MORPH_OPEN,
        np.ones((1, max(20, w // 3)), np.uint8),
    )
    vertical = cv2.morphologyEx(
        cv2.dilate(ink, np.ones((1, 5), np.uint8)),
        cv2.MORPH_OPEN,
        np.ones((max(20, h // 3), 1), np.uint8),
    )
    margin_y = min(8, h // 5)
    margin_x = min(8, w // 5)
    horizontal[margin_y:-margin_y, :] = 0
    vertical[:, margin_x:-margin_x] = 0
    remove = ((horizontal | vertical) > 0) & (ink > 0)
    pixels = np.asarray(image.convert("RGB")).copy()
    pixels[remove] = 255
    return Image.fromarray(pixels)


def equivalent_cell_words(original, candidate):
    def words(value):
        return re.findall(r"[^\W_]+", value.casefold())

    return words(original) == words(candidate)


def is_word_extension(original, candidate):
    old = re.findall(r"\w+", original.casefold())
    new = re.findall(r"\w+", candidate.casefold())
    if not old or len(new) <= len(old):
        return False
    iterator = iter(new)
    return all(any(word == item for item in iterator) for word in old)


def accepts_cell_candidate(original, candidate):
    if not candidate.strip():
        return False
    if not original.strip():
        return True
    if not preserves_cell_values(original, candidate):
        return False
    # Border remnants often become high-confidence trailing underscores.
    # They add no lexical evidence to an already populated cell. Do not reject
    # ordinary punctuation corrections (e.g. restoring a sentence's period).
    if (
        equivalent_cell_words(original, candidate)
        and re.search(r"_+\s*$", candidate)
        and not re.search(r"_+\s*$", original)
    ):
        return False

    def normal(value):
        return " ".join(value.casefold().split())

    # Numeric values present in the original must remain, in their original order.
    def numbers(value):
        return re.findall(r"\d+(?:[.,]\d+)*", value)

    iterator = iter(numbers(candidate))
    if not all(any(number == item for item in iterator) for number in numbers(original)):
        return False

    def operators(value):
        return re.findall(r"[<>≤≥±=]+", value)

    iterator = iter(operators(candidate))
    if not all(any(operator == item for item in iterator) for operator in operators(original)):
        return False
    if is_text_extension(original, candidate):
        return True
    similarity = Levenshtein.normalized_similarity(normal(original), normal(candidate))
    # Permit a larger addition when segmentation recovers a long missing span.
    # Retain the existing token-deletion guard used by table reconciliation.
    return (
        similarity >= 0.7
        and len(candidate) >= len(original) * 0.9
        and preserves_existing_words(original, candidate)
    )


def preserves_cell_values(original, candidate):
    """Even a corroborated reread may not delete numbers or formula signs."""

    def tokens(text):
        return re.findall(r"\d+(?:[.,]\d+)*|[<>≤≥±=+*/%()\[\]_-]", text)

    iterator = iter(tokens(candidate))
    return all(any(value == item for item in iterator) for value in tokens(original))


def is_text_extension(original: str, candidate: str) -> bool:
    """The original tokens must be an ordered subsequence, including punctuation.

    This accepts long additions that a normalized character similarity gate
    rejects. It does not allow correcting or deleting the original tokens.
    """

    def tokens(value):
        return re.findall(r"\w+|[^\w\s]", value.casefold())

    old, new = tokens(original), tokens(candidate)
    if not old or len(new) <= len(old):
        return False
    iterator = iter(new)
    return all(any(word == item for item in iterator) for word in old)


def detected_cell_text(image, reader, minimum=0.9):
    """An independent segmentation pass, using the same visual recognizer."""
    result = reader(image, use_det=True, use_cls=False)
    boxes = getattr(result, "boxes", None)
    if boxes is None or not result.txts or len(boxes) != len(result.txts):
        return ""
    lines = []
    for box, text, score in zip(boxes, result.txts, result.scores):
        if score < minimum or not text.strip():
            continue
        xs, ys = [float(p[0]) for p in box], [float(p[1]) for p in box]
        if min(xs) < -1 or min(ys) < -1 or max(xs) > image.width + 1 or max(ys) > image.height + 1:
            continue
        lines.append((min(ys), min(xs), text.strip()))
    return " ".join(text for _, _, text in sorted(lines))


def reconcile_table_lines(blocks: list[Block], lines: list[tuple[str, BBox, float]]) -> list[Block]:
    """Assign each trustworthy line to at most one cell, retaining old words.

    This stage uses only recognizer output and geometry, never a reference.
    Table boxes must be complete cell rectangles, not tight text rectangles,
    to recover text beyond the original recognized extent.
    """
    output = []
    for block in blocks:
        if block.table is None:
            output.append(block)
            continue
        grouped: dict[int, list[tuple[str, BBox, float]]] = {}
        for text, box, confidence in lines:
            if not text.strip() or confidence < 0.85:
                continue
            cx, cy = (box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2
            area = max(0.01, (box.x1 - box.x0) * (box.y1 - box.y0))
            choices = []
            for index, cell in enumerate(block.table.cells):
                b = cell.bbox
                if not (b.x0 <= cx <= b.x1 and b.y0 <= cy <= b.y1):
                    continue
                intersection = max(0, min(box.x1, b.x1) - max(box.x0, b.x0)) * max(
                    0, min(box.y1, b.y1) - max(box.y0, b.y0)
                )
                if intersection / area >= 0.9:
                    choices.append(index)
            # Ambiguous overlapping or merged cells must not receive duplicates.
            if len(choices) == 1:
                grouped.setdefault(choices[0], []).append((text, box, confidence))
        cells = []
        for index, cell in enumerate(block.table.cells):
            local = sorted(grouped.get(index, []), key=lambda line: (line[1].y0, line[1].x0))
            candidate = " ".join(text.strip() for text, _, _ in local)

            def normal(text):
                return " ".join(text.casefold().split())

            old = normal(cell.text)
            new = normal(candidate)
            # Avoid copying an unrelated line over a populated cell. Blank cells
            # may be filled when their rectangle uniquely contains the OCR line.
            if candidate and (
                not old
                or (
                    len(new) >= len(old)
                    and Levenshtein.normalized_similarity(old, new) >= 0.7
                    and preserves_existing_words(cell.text, candidate)
                )
            ):
                cell = cell.model_copy(update={"text": candidate})
            cells.append(cell)
        output.append(
            block.model_copy(update={"table": block.table.model_copy(update={"cells": cells})})
        )
    return output


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
