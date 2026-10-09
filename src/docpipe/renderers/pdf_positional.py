from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from docpipe.ir import BlockType, Document
from docpipe.renderers.fonts import find_bold_font, require_font
from docpipe.renderers.table_layout import build_table_layout
from docpipe.renderers.text_layout import collision_free_text_tops
from docpipe.renderers.typography import TABLE_FONT_PT, text_style


def _register_fonts() -> tuple[str, str]:
    regular_path = require_font()
    regular_name = "DocpipePositional"
    bold_path = find_bold_font()
    bold_name = "DocpipePositionalBold" if bold_path else regular_name
    if regular_name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(regular_name, str(regular_path)))
    if bold_path and bold_name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(bold_name, str(bold_path)))
    return regular_name, bold_name


def _draw_lines(pdf, text: str, x: float, y_top: float, width: float, height: float,
                size: float, font: str, invisible: bool = False) -> None:
    text_object = pdf.beginText(x, y_top - size)
    text_object.setFont(font, size)
    if invisible:
        text_object.setTextRenderMode(3)
    # Keep multiline text inside the OCR width. The shared page layout reserves
    # the resulting height before drawing the next independently placed block.
    line_height = size
    text_object.setLeading(line_height)
    lines = [
        wrapped
        for source_line in (text.splitlines() or [""])
        for wrapped in _wrap_line(source_line, width, font, size)
    ]
    for line in lines:
        text_object.textLine(line)
    pdf.drawText(text_object)


def _wrap_line(text: str, width: float, font: str, size: float) -> list[str]:
    """Wrap positional PDF text with the actual embedded font metrics."""
    if not text:
        return [""]
    available_width = max(1.0, width - 1.0)
    output: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if current and pdfmetrics.stringWidth(candidate, font, size) <= available_width:
            current = candidate
            continue
        if current:
            output.append(current)
        current = ""
        if pdfmetrics.stringWidth(word, font, size) <= available_width:
            current = word
            continue
        # Break unusually long unspaced tokens instead of letting them spill
        # horizontally over the neighboring positioned block.
        fragment = ""
        for char in word:
            if fragment and pdfmetrics.stringWidth(fragment + char, font, size) > available_width:
                output.append(fragment)
                fragment = char
            else:
                fragment += char
        current = fragment
    if current:
        output.append(current)
    return output or [""]


def _draw_table(pdf, block, page_height: float, regular_font: str,
                invisible: bool = False, top: float | None = None) -> None:
    layout = build_table_layout(block)
    top = block.bbox.y0 if top is None else top
    if not invisible:
        pdf.saveState()
        pdf.setFillColor(colors.white)
        pdf.setStrokeColor(colors.white)
        pdf.rect(block.bbox.x0, page_height - top - sum(layout.row_heights),
                 block.bbox.x1 - block.bbox.x0, sum(layout.row_heights),
                 stroke=0, fill=1)
        pdf.restoreState()
    x_positions = [block.bbox.x0]
    for width in layout.column_widths:
        x_positions.append(x_positions[-1] + width)
    y_positions = [top]
    for height in layout.row_heights:
        y_positions.append(y_positions[-1] + height)

    if not invisible:
        pdf.setStrokeColor(colors.HexColor("#333333"))
        pdf.setLineWidth(0.5)
        for row in range(layout.rows):
            for col in range(layout.cols):
                x0, x1 = x_positions[col], x_positions[col + 1]
                top, bottom = y_positions[row], y_positions[row + 1]
                pdf.rect(x0, page_height - bottom, x1 - x0, bottom - top, stroke=1, fill=0)

    for cell in layout.cells:
        x0 = x_positions[cell.col]
        x1 = x_positions[cell.col + cell.colspan]
        top = y_positions[cell.row]
        bottom = y_positions[cell.row + cell.rowspan]
        _draw_lines(pdf, cell.text, x0 + 2, page_height - top - 1,
                    max(1, x1 - x0 - 4), max(1, bottom - top - 2),
                    TABLE_FONT_PT, regular_font, invisible=invisible)


def render(document: Document, out: Path, base_dir: Path | None = None) -> Path:
    """Reconstruct positioned pages from IR with stable typography and table grids."""
    out.parent.mkdir(parents=True, exist_ok=True)
    regular_font, bold_font = _register_fonts()
    base = base_dir or out.parent
    pdf = canvas.Canvas(str(out))
    for page in document.pages:
        text_tops = collision_free_text_tops(
            page.blocks,
            lambda block: text_style(block.type.value),
            font_metric_mode="dejavu",
        )
        pdf.setPageSize((page.width_pt, page.height_pt))
        for block in sorted(page.blocks, key=lambda item: item.order):
            x = block.bbox.x0
            width = max(1.0, block.bbox.x1 - block.bbox.x0)
            height = max(1.0, block.bbox.y1 - block.bbox.y0)
            y = page.height_pt - block.bbox.y1
            if block.type == BlockType.FIGURE and block.figure:
                path = base / block.figure.file
                if path.is_file():
                    pdf.drawImage(str(path), x, y, width=width, height=height,
                                  preserveAspectRatio=True, anchor="sw")
                continue
            if block.type == BlockType.TABLE and block.table:
                _draw_table(pdf, block, page.height_pt, regular_font, top=text_tops.get(id(block)))
                continue
            if not block.text:
                continue
            size, bold, _italic = text_style(block.type.value)
            top = text_tops.get(id(block), block.bbox.y0)
            _draw_lines(pdf, block.text, x, page.height_pt - top,
                        width, height, size, bold_font if bold else regular_font)
        pdf.showPage()
    pdf.save()
    return out
