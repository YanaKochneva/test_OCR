from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from docpipe.ir import BlockType, Document
from docpipe.renderers.fonts import find_bold_font, font_path, require_font
from docpipe.renderers.table_layout import build_table_layout
from docpipe.renderers.typography import TABLE_FONT_PT, text_style


def _register_fonts() -> tuple[str, str]:
    arial = Path("C:/Windows/Fonts/arial.ttf")
    regular_path = arial if arial.is_file() else require_font()
    regular_name = "DocpipePositional"
    arial_bold = Path("C:/Windows/Fonts/arialbd.ttf")
    bold_path = arial_bold if arial.is_file() and arial_bold.is_file() else find_bold_font()
    bold_name = "DocpipePositionalBold" if bold_path else regular_name
    if regular_name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(regular_name, str(regular_path)))
    if bold_path and bold_name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(bold_name, str(bold_path)))
    return regular_name, bold_name


def _draw_lines(
    pdf,
    text: str,
    x: float,
    y_top: float,
    width: float,
    height: float,
    size: float,
    font: str,
    invisible: bool = False,
) -> None:
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


def _draw_table(
    pdf,
    block,
    page_height: float,
    regular_font: str,
    invisible: bool = False,
    top: float | None = None,
) -> None:
    layout = build_table_layout(block, preserve_geometry=True)
    top = block.bbox.y0 if top is None else top
    if not invisible:
        pdf.saveState()
        pdf.setFillColor(colors.white)
        pdf.setStrokeColor(colors.white)
        pdf.rect(
            block.bbox.x0,
            page_height - top - sum(layout.row_heights),
            block.bbox.x1 - block.bbox.x0,
            sum(layout.row_heights),
            stroke=0,
            fill=1,
        )
        pdf.restoreState()
    x_positions = [block.bbox.x0]
    for width in layout.column_widths:
        x_positions.append(x_positions[-1] + width)
    y_positions = [top]
    for height in layout.row_heights:
        y_positions.append(y_positions[-1] + height)
    source_cells = {(cell.row, cell.col): cell for cell in block.table.cells}

    if not invisible:
        pdf.setStrokeColor(colors.HexColor(block.table.border_color or "#333333"))
        pdf.setLineWidth(block.table.border_width_pt or 0.5)
        occupied = {
            (row, col)
            for cell in layout.cells
            for row in range(cell.row, cell.row + cell.rowspan)
            for col in range(cell.col, cell.col + cell.colspan)
        }
        for row in range(layout.rows):
            for col in range(layout.cols):
                if (row, col) not in occupied:
                    pdf.rect(
                        x_positions[col],
                        page_height - y_positions[row + 1],
                        layout.column_widths[col],
                        layout.row_heights[row],
                        stroke=1,
                        fill=0,
                    )
        for cell in layout.cells:
            x0, x1 = x_positions[cell.col], x_positions[cell.col + cell.colspan]
            top, bottom = y_positions[cell.row], y_positions[cell.row + cell.rowspan]
            source = source_cells.get((cell.row, cell.col))
            if source and source.background_color:
                pdf.setFillColor(colors.HexColor(source.background_color))
            pdf.rect(
                x0,
                page_height - bottom,
                x1 - x0,
                bottom - top,
                stroke=1,
                fill=int(bool(source and source.background_color)),
            )
        pdf.setFillColor(colors.black)

    for cell in layout.cells:
        source = source_cells.get((cell.row, cell.col))
        if source and source.lines:
            for line in source.lines:
                _draw_source_line(pdf, line, page_height, regular_font)
            continue
        x0 = x_positions[cell.col]
        x1 = x_positions[cell.col + cell.colspan]
        top = y_positions[cell.row]
        bottom = y_positions[cell.row + cell.rowspan]
        size = _fitted_size(
            cell.text, max(1, x1 - x0 - 4), max(1, bottom - top - 2), regular_font, TABLE_FONT_PT
        )
        _draw_lines(
            pdf,
            cell.text,
            x0 + 2,
            page_height - top - 1,
            max(1, x1 - x0 - 4),
            max(1, bottom - top - 2),
            size,
            regular_font,
            invisible=invisible,
        )


def _draw_source_line(pdf, line, page_height, font):
    if line.rotation in (90, 270):
        from docpipe.ir import BBox

        b = line.bbox
        local = line.model_copy(
            update={"rotation": 0, "bbox": BBox(x0=0, y0=0, x1=b.y1 - b.y0, y1=b.x1 - b.x0)}
        )
        pdf.saveState()
        if line.rotation == 90:
            pdf.translate(b.x1, page_height - b.y1)
        else:
            pdf.translate(b.x0, page_height - b.y0)
        pdf.rotate(line.rotation)
        _draw_source_line(pdf, local, b.x1 - b.x0, font)
        pdf.restoreState()
        return
    if line.runs:
        for run in line.runs:
            _draw_source_line(pdf, run, page_height, font)
        return
    path = font_path(line.font_family, line.bold, line.italic) if line.font_family else None
    if path:
        font = "Docpipe-" + path.stem
        if font not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(font, str(path)))
    size = line.font_size_pt or max(4, (line.bbox.y1 - line.bbox.y0) / 0.75)
    measured = pdfmetrics.stringWidth(line.text, font, size)
    if path:
        from PIL import ImageFont

        glyph = ImageFont.truetype(str(path), 100).getbbox(line.text, anchor="ls")
        measured = (glyph[2] - glyph[0]) * size / 100
    baseline = (
        line.baseline_from_top_pt
        if line.baseline_from_top_pt is not None
        else pdfmetrics.getAscent(font, size)
    )
    text = pdf.beginText(
        line.bbox.x0 - line.glyph_offset_x_pt, page_height - line.bbox.y0 - baseline
    )
    text.setFont(font, size)
    text.setFillColor(colors.HexColor(line.color or "#000000"))
    if measured:
        text.setHorizScale(
            line.horizontal_scale_pct or (line.bbox.x1 - line.bbox.x0) / measured * 100
        )
    text.textOut(line.text)
    pdf.drawText(text)


def _fitted_size(text, width, height, font, preferred):
    size = min(preferred, height)
    while size > 0.5:
        lines = [
            line
            for source in (text.splitlines() or [""])
            for line in _wrap_line(source, width, font, size)
        ]
        if len(lines) * size <= height:
            break
        size = max(0.5, size - 0.25)
    return size


def render(document: Document, out: Path, base_dir: Path | None = None) -> Path:
    """Reconstruct positioned pages from IR with stable typography and table grids."""
    out.parent.mkdir(parents=True, exist_ok=True)
    from docpipe.renderers.fixed_layout import stable_document
    from docpipe.renderers.page_size import a4_document

    document = stable_document(a4_document(document))
    regular_font, bold_font = _register_fonts()
    base = base_dir or out.parent
    pdf = canvas.Canvas(str(out))
    for page in document.pages:
        pdf.setPageSize((page.width_pt, page.height_pt))
        for block in sorted(page.blocks, key=lambda item: item.order):
            x = block.bbox.x0
            width = max(1.0, block.bbox.x1 - block.bbox.x0)
            height = max(1.0, block.bbox.y1 - block.bbox.y0)
            y = page.height_pt - block.bbox.y1
            if block.type == BlockType.FIGURE and block.figure:
                path = base / block.figure.file
                if path.is_file():
                    pdf.drawImage(
                        str(path),
                        x,
                        y,
                        width=width,
                        height=height,
                        preserveAspectRatio=True,
                        anchor="sw",
                    )
                continue
            if block.type == BlockType.TABLE and block.table:
                _draw_table(pdf, block, page.height_pt, regular_font)
                continue
            if not block.text:
                continue
            size, bold, _italic = text_style(block.type.value)
            if block.lines:
                for line in block.lines:
                    font = bold_font if bold else regular_font
                    _draw_source_line(pdf, line, page.height_pt, font)
                continue
            top = block.bbox.y0
            size = _fitted_size(
                block.text, width, height, bold_font if bold else regular_font, size
            )
            _draw_lines(
                pdf,
                block.text,
                x,
                page.height_pt - top,
                width,
                height,
                size,
                bold_font if bold else regular_font,
            )
        pdf.showPage()
    pdf.save()
    return out
