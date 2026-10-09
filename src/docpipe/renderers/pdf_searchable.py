from __future__ import annotations

from io import BytesIO
from pathlib import Path

from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from docpipe.ir import Document
from docpipe.renderers.text_layout import wrap_text
from docpipe.renderers.fonts import require_font


def _font_name() -> str:
    path = require_font()
    name = "DocpipeOverlay"
    if name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(name, str(path)))
    return name


def _make_overlay(document: Document, page_index: int, width: float, height: float) -> bytes:
    font = _font_name()
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height))
    c.setFont(font, 8)
    text_mode = 3  # PDF text rendering mode: invisible.
    for block in sorted(document.pages[page_index].blocks, key=lambda item: item.order):
        regions = ([(cell.text, cell.bbox) for cell in sorted(
            block.table.cells, key=lambda cell: (cell.row, cell.col)
        )] if block.table and block.table.cells else [(block.text, block.bbox)])
        for content, box in regions:
            if not content:
                continue
            available_width = max(1, box.x1 - box.x0)
            available_height = max(1, box.y1 - box.y0)
            size = min(11, available_height)
            # Invisible text must still stay within the source region, otherwise
            # PDF extractors lose lines outside the page or corrupt selection.
            while True:
                lines = wrap_text(content, available_width, (size, False, False),
                                  font_metric_mode="dejavu").splitlines() or [""]
                if len(lines) * size <= available_height or size <= .5:
                    break
                size = max(.5, size * .85)
            text = c.beginText(box.x0, height - box.y0 - size)
            text.setFont(font, size)
            text.setLeading(size)
            text.setTextRenderMode(text_mode)
            for line in lines:
                text.textLine(line)
            c.drawText(text)
    c.showPage()
    c.save()
    return buf.getvalue()


def render(document: Document, source_pdf: Path, out: Path) -> Path:
    """Добавить невидимый Unicode-текст поверх неизменённых исходных страниц.

    Требует pikepdf. Исходные image streams не декодируются и не пересобираются.
    """
    try:
        import pikepdf
    except ImportError as exc:
        raise RuntimeError("Для searchable-PDF нужен pikepdf; установите пакет из constraints.txt") from exc

    out.parent.mkdir(parents=True, exist_ok=True)
    with pikepdf.Pdf.open(str(source_pdf)) as pdf:
        if len(pdf.pages) != len(document.pages):
            raise ValueError("Число страниц исходного PDF и IR не совпадает")
        for index, page in enumerate(pdf.pages):
            box = page.cropbox or page.mediabox
            x0, y0, x1, y1 = map(float, box)
            width, height = x1 - x0, y1 - y0
            overlay_bytes = _make_overlay(document, index, width, height)
            with pikepdf.Pdf.open(BytesIO(overlay_bytes)) as overlay:
                page.add_overlay(overlay.pages[0], pikepdf.Rectangle(x0, y0, x1, y1))
        pdf.save(str(out))
    return out
