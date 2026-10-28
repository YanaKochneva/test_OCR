"""Convert OCR table cells into a stable row/column grid for renderers."""

from __future__ import annotations

from dataclasses import dataclass, replace
from statistics import median

from docpipe.ir import BBox, Block


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


def build_table_layout(
    block: Block,
    *,
    font_size_pt: float | None = None,
    full_line_boxes: bool = False,
    preserve_geometry: bool = False,
) -> TableLayout:
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
    owners: dict[tuple[int, int], int] = {}
    for cell in sorted(source_cells, key=lambda item: (item.row, item.col)):
        row, col = cell.row, cell.col
        rowspan = min(cell.rowspan, rows - row)
        colspan = min(cell.colspan, cols - col)
        if rowspan <= 0 or colspan <= 0:
            continue
        if (row, col) in occupied:
            # OCR can emit a merged placeholder and a separate populated cell
            # for the same grid position. Never discard that populated payload.
            owner = owners[(row, col)]
            previous = placements[owner]
            text = cell.text.strip()
            if text and text not in previous.text.splitlines():
                placements[owner] = replace(
                    previous, text="\n".join(t for t in (previous.text, text) if t)
                )
            continue
        placement = CellPlacement(row, col, rowspan, colspan, cell.text)
        placements.append(placement)
        for covered_row in range(row, row + rowspan):
            for covered_col in range(col, col + colspan):
                occupied.add((covered_row, covered_col))
                owners[(covered_row, covered_col)] = len(placements) - 1

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
    if any(cell.lines for cell in source_cells):
        # Pixel-verified cells contain complete rectangles rather than glyph
        # boxes. Use their actual separator coordinates, including merged spans.
        def edges(count, first, last, start_name, end_name, offset_name, span_name):
            samples = [[] for _ in range(count + 1)]
            for cell in source_cells:
                start = getattr(cell, offset_name)
                end = start + getattr(cell, span_name)
                samples[start].append(getattr(cell.bbox, start_name))
                samples[end].append(getattr(cell.bbox, end_name))
            bounds = [
                median(values) if values else first + (last - first) * i / count
                for i, values in enumerate(samples)
            ]
            bounds[0], bounds[-1] = first, last
            if any(b <= a for a, b in zip(bounds, bounds[1:])):
                return None
            return [b - a for a, b in zip(bounds, bounds[1:])]

        normalized_widths = (
            edges(cols, box.x0, box.x1, "x0", "x1", "col", "colspan") or normalized_widths
        )
        normalized_heights = (
            edges(rows, box.y0, box.y1, "y0", "y1", "row", "rowspan") or normalized_heights
        )
    if preserve_geometry:
        return TableLayout(rows, cols, normalized_widths, normalized_heights, placements)
    # Native tables may grow to fit their contents. Reserve that height in all
    # renderers so later rows and page objects cannot cover the cell text.
    from docpipe.renderers.text_layout import estimate_text_height, wrap_text
    from docpipe.renderers.fonts import TABLE_FONT_PT

    font_size = TABLE_FONT_PT if font_size_pt is None else font_size_pt

    for cell in placements:
        width = max(1.0, sum(normalized_widths[cell.col : cell.col + cell.colspan]) - 4)
        required = (
            max(
                estimate_text_height(cell.text, width, (font_size, False, False), mode)
                for mode in ("arial", "dejavu")
            )
            + 4
        )
        if full_line_boxes:
            required = max(
                required,
                max(
                    max(
                        1,
                        len(
                            wrap_text(
                                cell.text, width, (font_size, False, False), font_metric_mode=mode
                            ).splitlines()
                        ),
                    )
                    * font_size
                    + 4
                    for mode in ("arial", "dejavu")
                ),
            )
        available = sum(normalized_heights[cell.row : cell.row + cell.rowspan])
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
