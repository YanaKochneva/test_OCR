"""Reconcile recognizer lines with structured layout without a reference document."""
from __future__ import annotations

from docpipe.ir import BBox, Block, BlockType


def recover_unassigned_lines(blocks: list[Block], lines: list[tuple[str, BBox, float]],
                             page_height: float) -> list[Block]:
    result = list(blocks)
    for index, (text, box, confidence) in enumerate(lines):
        if not text.strip() or confidence < .75:
            continue
        area = max(.01, (box.x1 - box.x0) * (box.y1 - box.y0))
        def covered(block):
            b = block.bbox
            intersection = max(0, min(box.x1, b.x1) - max(box.x0, b.x0)) * max(
                0, min(box.y1, b.y1) - max(box.y0, b.y0))
            return intersection / area >= .6
        if any(covered(b) for b in result):
            continue
        kind = BlockType.TEXT
        if box.y1 < page_height * .07:
            kind = BlockType.HEADER
        elif box.y0 > page_height * .94:
            kind = BlockType.FOOTER
        recovered = Block(id=f"ocr-recovery-{index}", type=kind, bbox=box,
                          order=0, text=text, confidence=confidence,
                          source_label="unassigned_ocr_line")
        # Keep the engine's reading order. Insert near the preceding text in
        # the same column instead of rebuilding every column from scratch.
        predecessors = [i for i, b in enumerate(result)
                        if b.bbox.y0 <= box.y0
                        and min(b.bbox.x1, box.x1) > max(b.bbox.x0, box.x0)]
        position = max(predecessors, key=lambda i: result[i].bbox.y0) + 1 if predecessors else 0
        result.insert(position, recovered)
    return [b.model_copy(update={"order": i}) for i, b in enumerate(result)]
