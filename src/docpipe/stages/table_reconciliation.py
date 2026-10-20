"""Recover table text from positioned OCR lines before it is discarded."""

from __future__ import annotations

from rapidfuzz.distance import Levenshtein

from docpipe.ir import BBox, Block
from docpipe.stages.text_refinement import preserves_existing_words


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
