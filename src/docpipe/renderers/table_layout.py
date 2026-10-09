"""Convert OCR table cells into a stable row/column grid for renderers."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median

from docpipe.ir import Block, BBox


@dataclass(frozen=True)
class CellPlacement:
    row: int
    col: int
    rowspan: int
    colspan: int
    text: str


@dataclass(frozen=True)
class TableLayout:
    rows: int
    cols: int
    column_widths: list[float]
    row_heights: list[float]
    cells: list[CellPlacement]


def build_table_layout(block: Block) -> TableLayout:
    """Build a rectangular grid while retaining recognized spans and geometry."""
    if block.table is None:
        raise ValueError("table layout requires a table block")

    source_cells = block.table.cells
    rows = max(
        block.table.n_rows,
        max((cell.row + cell.rowspan for cell in source_cells), default=0),
        1,
    )
    cols = max(
        block.table.n_cols,
        max((cell.col + cell.colspan for cell in source_cells), default=0),
        1,
    )

    placements: list[CellPlacement] = []
    occupied: set[tuple[int, int]] = set()
    for cell in sorted(source_cells, key=lambda item: (item.row, item.col)):
        row, col = cell.row, cell.col
        rowspan = min(cell.rowspan, rows - row)
        colspan = min(cell.colspan, cols - col)
        if rowspan <= 0 or colspan <= 0 or (row, col) in occupied:
            continue
        placement = CellPlacement(row, col, rowspan, colspan, cell.text)
        placements.append(placement)
        for covered_row in range(row, row + rowspan):
            for covered_col in range(col, col + colspan):
                occupied.add((covered_row, covered_col))

    # An engine can report table text without usable cell coordinates. Keep the
    # text visible inside a real one-cell table rather than dropping it.
    if not placements and (block.text or "").strip():
        placements.append(CellPlacement(0, 0, rows, cols, block.text or ""))

    column_samples: list[list[float]] = [[] for _ in range(cols)]
    row_samples: list[list[float]] = [[] for _ in range(rows)]
    if source_cells:
        for cell in source_cells:
            width = max(0.0, cell.bbox.x1 - cell.bbox.x0) / cell.colspan
            height = max(0.0, cell.bbox.y1 - cell.bbox.y0) / cell.rowspan
            for col in range(cell.col, min(cols, cell.col + cell.colspan)):
                column_samples[col].append(width)
            for row in range(cell.row, min(rows, cell.row + cell.rowspan)):
                row_samples[row].append(height)

    box: BBox = block.bbox
    total_width = max(0.5, box.x1 - box.x0)
    total_height = max(0.5, box.y1 - box.y0)
    widths = [median(samples) if samples else 0.0 for samples in column_samples]
    heights = [median(samples) if samples else 0.0 for samples in row_samples]

    def normalize(values: list[float], total: float) -> list[float]:
        positive = [max(0.0, value) for value in values]
        measured = sum(positive)
        if measured <= 0:
            return [total / len(values)] * len(values)
        # Scale OCR cell dimensions to the enclosing table bbox. This prevents
        # small coordinate-rounding differences from widening the table.
        return [value * total / measured for value in positive]

    normalized_widths = normalize(widths, total_width)
    normalized_heights = normalize(heights, total_height)
    # Native tables may grow to fit their contents. Reserve that height in all
    # renderers so later rows and page objects cannot cover the cell text.
    from docpipe.renderers.text_layout import estimate_text_height
    from docpipe.renderers.typography import TABLE_FONT_PT

    for cell in placements:
        width = max(1.0, sum(normalized_widths[cell.col:cell.col + cell.colspan]) - 4)
        required = max(
            estimate_text_height(cell.text, width, (TABLE_FONT_PT, False, False), mode)
            for mode in ("arial", "dejavu")
        ) + 4
        available = sum(normalized_heights[cell.row:cell.row + cell.rowspan])
        if required > available:
            increment = (required - available) / cell.rowspan
            for row in range(cell.row, cell.row + cell.rowspan):
                normalized_heights[row] += increment

    return TableLayout(
        rows=rows,
        cols=cols,
        column_widths=normalized_widths,
        row_heights=normalized_heights,
        cells=placements,
    )
