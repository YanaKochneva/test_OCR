from __future__ import annotations

from io import BytesIO
from pathlib import Path

from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from docpipe.ir import Document


def _font_name() -> str:
    candidates = [
	Path("D:/AI/fonts/dejavu/dejavu-fonts-ttf-2.37/ttf/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/dejavu/DejaVuSans.ttf"),
        Path("C:/Windows/Fonts/DejaVuSans.ttf"),
    ]
    for path in candidates:
        if path.exists():
            name = "DocpipeOverlayDejaVu"
            if name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(name, str(path)))
            return name
    raise RuntimeError("Не найден свободный шрифт DejaVu Sans для невидимого слоя")


def _make_overlay(document: Document, page_index: int, width: float, height: float) -> bytes:
    font = _font_name()
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height))
    c.setFont(font, 8)
    text_mode = 3  # PDF text rendering mode: invisible.
    for block in sorted(document.pages[page_index].blocks, key=lambda item: item.order):
        if not block.text:
            continue
        x = block.bbox.x0
        y = height - block.bbox.y1
        size = max(4.0, min(18.0, block.bbox.y1 - block.bbox.y0))
        text = c.beginText(x, y)
        text.setFont(font, size)
        text.setTextRenderMode(text_mode)
        # Для многострочного блока сохраняем слова внутри исходной рамки.
        for line in block.text.splitlines() or [""]:
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
