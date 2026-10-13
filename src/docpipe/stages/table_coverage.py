"""Materialize missing table slots only when image rules prove their bounds."""
from __future__ import annotations

from PIL import Image
from docpipe.engines.base import PageMeta
from docpipe.ir import Block, TableCell
from docpipe.stages.table_geometry import measured_cell_boxes


def materialize_missing_cells(block: Block, image: Image.Image, meta: PageMeta) -> Block:
    if block.table is None:
        return block
    table = block.table
    rows, cols = table.n_rows, table.n_cols
    if rows < 1 or cols < 1 or rows * cols > 4096:
        return block
    occupied = {
        (row, col) for cell in table.cells
        for row in range(cell.row, min(rows, cell.row + cell.rowspan))
        for col in range(cell.col, min(cols, cell.col + cell.colspan))
    }
    holes = [(row, col) for row in range(rows) for col in range(cols) if (row, col) not in occupied]
    if not holes:
        return block
    # Dummy boxes are never used for OCR: only the independently measured
    # complete grid can authorize a new cell's rectangle.
    candidates = [TableCell(row=row, col=col, text='', bbox=block.bbox) for row, col in holes]
    probe = block.model_copy(update={'table': table.model_copy(update={'cells': [*table.cells, *candidates]})})
    measured = measured_cell_boxes(probe, image, meta)
    if not measured:
        return block
    added = [cell.model_copy(update={'bbox': measured[(cell.row, cell.col)]}) for cell in candidates]
    return block.model_copy(update={'table': table.model_copy(update={'cells': [*table.cells, *added]})})
